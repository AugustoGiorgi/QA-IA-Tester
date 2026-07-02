from __future__ import annotations

import json
import base64
import re
import shutil
import subprocess
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from bson import ObjectId
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from pymongo import ASCENDING, DESCENDING

from services.activity import record_activity
from services.ai import MODEL, client
from services.auth import _db, current_user
from services.files import safe_filename


router = APIRouter(prefix="/ai", tags=["playwright-ai"])
COLLECTION = "PlaywrightGeneratedTests"
UPLOAD_DIR = Path(__file__).resolve().parent.parent / "data" / "playwright_videos"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
FRAME_DIR = UPLOAD_DIR / "frames"
FRAME_DIR.mkdir(parents=True, exist_ok=True)


class GeneratedUpdateIn(BaseModel):
    title: Optional[str] = None
    requirement_id: Optional[str] = None
    module: Optional[str] = None
    initial_url: Optional[str] = None
    generated_code: Optional[str] = None
    selectors: Optional[Dict[str, str]] = None
    test_data: Optional[Dict[str, str]] = None
    ai_notes: Optional[List[str]] = None


MAX_CODE_LENGTH = 80000
MAX_VIDEO_FRAMES = 20


def _clean(value: Optional[str], max_len: int = 4000) -> str:
    return ((value or "").replace("\x00", "").strip())[:max_len]


def _slug(value: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return clean or "generated-playwright-test"


def _as_public(doc: Dict[str, Any]) -> Dict[str, Any]:
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    return doc


async def _ensure_indexes() -> None:
    col = _db()[COLLECTION]
    await col.create_index([("created_by", ASCENDING), ("created_at", DESCENDING)])
    await col.create_index([("requirement_id", ASCENDING)])
    await col.create_index([("title", ASCENDING)])


def _extract_block(text: str) -> str:
    match = re.search(r"```(?:ts|typescript)?\s*(.*?)```", text, re.S | re.I)
    return (match.group(1) if match else text).strip()


def _extract_json_object(text: str, key: str) -> Dict[str, str]:
    pattern = rf"{key}\s*[:=]\s*(\{{.*?\}})"
    match = re.search(pattern, text, re.S | re.I)
    if not match:
        return {}
    try:
        data = json.loads(match.group(1))
        return {str(k): str(v) for k, v in data.items()}
    except Exception:
        return {}


def _fallback_code(title: str, initial_url: str, description: str, observations: str) -> Dict[str, Any]:
    spec_title = title or "Prueba generada"
    url = initial_url or "/"
    navigation_target = url if re.match(r"^https?://", url, re.I) else f"${{BASE_URL}}{url}"
    data_hint = "Completa estos datos con valores reales antes de ejecutar."
    code = f"""import {{ test, expect }} from '@playwright/test';

const BASE_URL = process.env.BASE_URL ?? 'http://localhost:8000';

const selectors = {{
  firstActionButton: 'data-testid=accion-principal',
  firstInput: 'data-testid=input-principal',
  submitButton: 'data-testid=guardar',
  successMessage: 'data-testid=mensaje-exito',
}};

const testData = {{
  inputValue: 'valor-de-prueba',
}};

test.describe('{spec_title}', () => {{
  test('debe ejecutar el flujo esperado', async ({{ page }}) => {{
    await page.goto('{navigation_target}');

    // TODO: Ajustar estos pasos segun el flujo real detectado.
    await page.locator(selectors.firstActionButton).click();
    await page.locator(selectors.firstInput).fill(testData.inputValue);
    await page.locator(selectors.submitButton).click();

    await expect(page.locator(selectors.successMessage)).toBeVisible();
  }});
}});
"""
    notes = [
        "Se genero una base ejecutable con variables de selectores y datos para completar.",
        data_hint,
    ]
    if description:
        notes.append("Descripcion usada: " + description[:260])
    if observations:
        notes.append("Observaciones usadas: " + observations[:260])
    return {
        "generated_code": code,
        "selectors": {
            "firstActionButton": "data-testid=accion-principal",
            "firstInput": "data-testid=input-principal",
            "submitButton": "data-testid=guardar",
            "successMessage": "data-testid=mensaje-exito",
        },
        "test_data": {"inputValue": "valor-de-prueba"},
        "ai_notes": notes,
    }


def _generation_rules() -> str:
    return (
        "El objetivo es generar un scaffold Playwright de alta calidad para que un QA lo termine, no prometer "
        "ejecucion inmediata sin conocer el DOM real. Genera TypeScript con @playwright/test completo y mantenible. "
        "Transforma TODOS los pasos funcionales identificables en acciones trazables con comentarios // Paso N. "
        "Si la entrada contiene mas de un proceso, flujo, transaccion o camino funcional, NO elijas uno solo: "
        "segmenta el material en procesos detectados y genera un test independiente por cada proceso dentro del "
        "mismo describe, reutilizando login y helpers cuando corresponda. "
        "Conserva el orden e incluye login, navegacion, modales, dialogs, iframes, pestanas, agendas, tablas, "
        "autocompletados, confirmaciones y guardados cuando aparezcan. "
        "No inventes credenciales, polizas, clientes ni IDs: crea una entrada especifica en testData con valor "
        "TODO por cada dato desconocido y UTILIZALA en el paso correspondiente. No declares variables sin uso. "
        "Incluye tambien como variables los datos conocidos que el QA podria querer cambiar. "
        "No supongas que un control es input, select o button sin evidencia tecnica. Si falta esa evidencia, "
        "crea una entrada especifica en selectors con un selector provisional descriptivo que contenga TODO "
        "y utilizala en el codigo. Cada modal, agenda, solapa y boton repetido debe tener su propio selector "
        "o quedar acotado mediante un locator padre. "
        "Nunca hagas fill sobre botones ni selectOption sobre elementos sin evidencia de que sean select. "
        "Usa nombres especificos, nunca firstInput, firstActionButton o submitButton. "
        "Prioriza codegen y selectores aportados; luego getByRole, getByLabel, getByPlaceholder y getByText. "
        "Usa CSS o data-testid provisionales solo como ultimo recurso. Distingue botones repetidos por modal, "
        "seccion o locator padre. No uses page.click('body') ni waitForTimeout. "
        "Espera visibilidad, habilitacion, URL, respuesta o estado del DOM. Crea helpers para agendas, modales, "
        "confirmaciones y acciones repetidas cuando reduzcan el trabajo posterior del QA. "
        "Maneja iframes con frameLocator y nuevas paginas con context.waitForEvent('page') si la evidencia lo indica. "
        "Navega directamente a initial_url si es absoluta; si es relativa combinala con BASE_URL. "
        "Genera fechas de forma determinista en el formato indicado o deja el formato configurable. "
        "Incluye assertions intermedias y finales solo para resultados conocidos. No inventes mensajes de exito. "
        "Si falta el resultado esperado, deja un comentario TODO_ASSERTION y explicalo en ai_notes, sin inventarlo. "
        "Incluye en ai_notes una lista llamada conceptualmente 'Procesos detectados' con todos los flujos que "
        "identificaste y aclara si alguno quedo parcial por falta de evidencia visual o textual. "
        "Marca en ai_notes una checklist concreta de selectores, datos y assertions que el QA debe completar. "
        "El codigo debe compilar luego de completar unicamente valores TODO y selectores provisionales. "
        "Devuelve selectors y test_data completos porque la interfaz los presentara como campos editables."
    )


def _video_generation_rules() -> str:
    return (
        "Reglas especificas para video: primero analiza los frames como una linea de tiempo y detecta cortes de "
        "contexto, cambios de pantalla, nuevos codigos de transaccion, nuevos formularios o reinicios de flujo. "
        "Cada corte importante puede indicar un proceso distinto. Si ves dos procesos en el video, genera dos "
        "tests; si ves tres, genera tres. No reemplaces un proceso por otro. "
        "Cuando un texto del video no sea legible, crea variables TODO descriptivas en testData y selectores TODO "
        "descriptivos en selectors, pero conserva el paso funcional observado. "
        "Si el usuario no paso descripcion escrita, inferi el flujo desde el video sin inventar datos de negocio: "
        "usa nombres genericos seguros como TODO_TRANSACTION_CODE_PROCESS_2, TODO_POLICY_NUMBER_PROCESS_2 o "
        "TODO_CLIENT_PROCESS_2. "
        "El resultado ideal es un borrador amplio para que QA lo complete, por lo tanto es preferible dejar pasos "
        "TODO bien ubicados antes que omitir un proceso observado."
    )


def _build_prompt(payload: Dict[str, str]) -> List[Dict[str, str]]:
    system = (
        "Sos un arquitecto senior de automatizacion QA especializado en Playwright. "
        + _generation_rules()
        + " Devuelve JSON valido con exactamente estas claves: generated_code (string), "
        "selectors (objeto string a string), test_data (objeto string a string) y ai_notes (array de strings)."
    )
    user = "Contexto de generacion Playwright:\n" + json.dumps(payload, ensure_ascii=False, indent=2)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _parse_ai_json(raw: str) -> Optional[Dict[str, Any]]:
    clean = raw.strip()
    if clean.startswith("```"):
      clean = _extract_block(clean)
    try:
        return json.loads(clean)
    except Exception:
        start = clean.find("{")
        end = clean.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            return json.loads(clean[start:end + 1])
        except Exception:
            return None


def _ffmpeg_executable() -> Optional[str]:
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _video_duration(video_path: Path, ffmpeg_executable: str) -> Optional[float]:
    try:
        result = subprocess.run(
            [ffmpeg_executable, "-i", str(video_path)],
            capture_output=True,
            text=True,
            timeout=20,
        )
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
        if not match:
            return None
        hours, minutes, seconds = match.groups()
        return max((int(hours) * 3600) + (int(minutes) * 60) + float(seconds), 1.0)
    except Exception:
        return None


def _error_tail(value: str, max_len: int = 700) -> str:
    clean = re.sub(r"\s+", " ", value or "").strip()
    if len(clean) <= max_len:
        return clean
    return clean[-max_len:]


def _extract_video_frames(video_path: Optional[Path]) -> Tuple[List[Path], str]:
    ffmpeg_executable = _ffmpeg_executable()
    if not video_path or not video_path.exists():
        return [], "El archivo subido no existe o no quedo disponible en el servidor."
    if not ffmpeg_executable:
        return [], "FFmpeg no esta disponible en el servidor."
    stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    out_dir = FRAME_DIR / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    interval = max((_video_duration(video_path, ffmpeg_executable) or 72) / MAX_VIDEO_FRAMES, 2)
    attempts = [
        f"fps=1/{interval:.2f},scale=1280:-2",
        f"fps=1/{interval:.2f},scale=960:-2",
        "fps=1/5,scale=960:-2",
        "thumbnail,scale=960:-2",
    ]
    errors: List[str] = []
    for index, video_filter in enumerate(attempts, start=1):
        attempt_dir = out_dir / f"try_{index}"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        pattern = str(attempt_dir / "frame_%03d.jpg")
        command = [
            ffmpeg_executable,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(video_path),
            "-vf",
            video_filter,
            "-frames:v",
            str(MAX_VIDEO_FRAMES),
            pattern,
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            errors.append(f"Intento {index}: FFmpeg excedio los 120 segundos.")
            continue
        except Exception as exc:
            errors.append(f"Intento {index}: {type(exc).__name__}.")
            continue
        frames = sorted(attempt_dir.glob("frame_*.jpg"))[:MAX_VIDEO_FRAMES]
        if result.returncode == 0 and frames:
            return frames, ""
        detail = _error_tail(result.stderr or result.stdout or f"FFmpeg termino con codigo {result.returncode}.")
        errors.append(f"Intento {index}: {detail}")
    error_detail = _error_tail(" | ".join(error for error in errors if error))
    if not error_detail:
        error_detail = "FFmpeg no genero imagenes del video."
    return [], error_detail


def _video_extraction_detail(error_detail: str) -> str:
    if not error_detail:
        return ""
    return f" Detalle tecnico: {error_detail}"



def _image_part(path: Path) -> Dict[str, Any]:
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{data}"}}


def _generate_with_vision(payload: Dict[str, str], frames: List[Path]) -> Optional[Dict[str, Any]]:
    if not frames:
        return None
    try:
        prompt = (
            "Analiza cronologicamente estos frames de un video de paso a paso e identifica pantallas, campos, "
            "iconos, ventanas, solapas, selecciones y confirmaciones. Usa descripcion, observaciones, codegen "
            "y selectores aportados para completar lo que no se ve. "
            + _generation_rules()
            + " "
            + _video_generation_rules()
            + " "
            "Responde JSON estricto con generated_code, selectors, test_data, ai_notes. "
            "Contexto:\n" + json.dumps(payload, ensure_ascii=False, indent=2)
        )
        content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
        content.extend(_image_part(frame) for frame in frames)
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Sos un arquitecto senior de automatizacion Playwright con vision. "
                        "Tu prioridad es detectar todos los procesos funcionales del video antes de escribir codigo. "
                        "Devuelve solamente JSON valido y no omitas acciones ni procesos observados."
                    ),
                },
                {"role": "user", "content": content},
            ],
            temperature=0.15,
            max_tokens=16000,
            response_format={"type": "json_object"},
        )
        data = _parse_ai_json(resp.choices[0].message.content or "")
        if not data or not data.get("generated_code"):
            return None
        return {
            "generated_code": _clean(data.get("generated_code"), MAX_CODE_LENGTH),
            "selectors": data.get("selectors") or {},
            "test_data": data.get("test_data") or {},
            "ai_notes": data.get("ai_notes") or ["Codigo generado analizando frames del video."],
        }
    except Exception:
        return None


def _audit_prompt(payload: Dict[str, str], generated: Dict[str, Any]) -> List[Dict[str, str]]:
    candidate = {
        "request": payload,
        "candidate": {
            "generated_code": generated.get("generated_code", ""),
            "selectors": generated.get("selectors", {}),
            "test_data": generated.get("test_data", {}),
            "ai_notes": generated.get("ai_notes", []),
        },
    }
    system = (
        "Sos el revisor principal de automatizacion Playwright. Audita y CORRIGE el candidato antes de "
        "entregarlo al QA. " + _generation_rules() + " "
        "Si el material original o el candidato evidencia multiples procesos, conserva o crea un test por proceso; "
        "no unifiques todo en un solo flujo ni descartes procesos parciales. "
        "Comprueba cobertura de pasos, datos omitidos, acciones incompatibles, botones repetidos, modales, "
        "popups, iframes, fechas, navegacion, assertions inventadas y selectores provisionales. "
        "Devuelve el spec corregido, no solamente recomendaciones. Asigna quality_score de 0 a 100. "
        "manual_actions debe incluir solo aquello imposible de resolver sin datos humanos reales: selectores DOM, "
        "credenciales, poliza, cliente o resultado esperado desconocido. covered_steps lista los pasos cubiertos. "
        "Responde JSON con: generated_code, selectors, test_data, ai_notes, quality_score, covered_steps, "
        "manual_actions y warnings."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(candidate, ensure_ascii=False)},
    ]


def _normalized(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in text if not unicodedata.combining(char)).lower()


def _deterministic_findings(
    code: str,
    payload: Dict[str, str],
    selectors: Optional[Dict[str, str]] = None,
    test_data: Optional[Dict[str, str]] = None,
) -> Dict[str, List[str]]:
    critical: List[str] = []
    warnings: List[str] = []
    manual: List[str] = []
    selectors = {str(key): str(value) for key, value in (selectors or {}).items()}
    test_data = {str(key): str(value) for key, value in (test_data or {}).items()}
    for needle, message in (
        ("waitForTimeout(", "El codigo contiene waitForTimeout."),
        ("firstInput", "El codigo contiene el selector generico firstInput."),
        ("firstActionButton", "El codigo contiene el selector generico firstActionButton."),
    ):
        if needle in code:
            warnings.append(message)
    if "page.click('body')" in code or 'page.click("body")' in code:
        critical.append(
            "El codigo hace click sobre body para disparar el autocompletado; debe usar un elemento estable "
            "o una accion explicita del campo."
        )

    selector_types: Dict[str, str] = {}
    for key, value in selectors.items():
        match = re.match(r"\s*(input|button|select|textarea|tab)\b", value, re.I)
        selector_types[key] = match.group(1).lower() if match else ""
        if selector_types[key] == "tab":
            critical.append(f"El selector {key} usa la etiqueta HTML inexistente o dudosa 'tab'.")

    referenced_selectors = set(re.findall(r"\bselectors\.(\w+)\b", code))
    undefined_selectors = sorted(referenced_selectors - set(selectors))
    if undefined_selectors:
        critical.append(
            "El codigo utiliza selectores que no estan definidos: " + ", ".join(undefined_selectors) + "."
        )

    for action, key in re.findall(r"\.(fill|selectOption|click)\(\s*selectors\.(\w+)", code):
        kind = selector_types.get(key, "")
        lowered_key = key.lower()
        if action == "fill" and (
            kind in {"button", "select", "tab"}
            or any(token in lowered_key for token in ("button", "icon", "tab"))
        ):
            critical.append(f"Se usa fill sobre {key}, que parece ser {kind or 'un control no editable'}.")
        if action == "selectOption" and (
            kind not in {"", "select"}
            or any(token in lowered_key for token in ("input", "button", "icon", "tab"))
        ):
            critical.append(f"Se usa selectOption sobre {key}, pero su selector no corresponde a un select.")
        if action == "click" and kind in {"input", "textarea"}:
            warnings.append(f"Se hace click sobre {key}; verifica si la accion correcta era fill.")

    source = _normalized(
        " ".join(
            str(payload.get(key, ""))
            for key in ("description", "observations", "codegen", "selector_context")
        )
    )
    technical_source = payload.get("codegen", "") + "\n" + payload.get("selector_context", "")
    direct_css_actions = re.findall(
        r"page\.(?:click|fill|selectOption)\(\s*(['\"])(.*?)\1",
        code,
        re.I,
    )
    for _, direct_selector in direct_css_actions:
        if direct_selector in {"body", "html"}:
            continue
        if direct_selector not in technical_source:
            critical.append(
                f"El selector directo '{direct_selector}' no fue aportado por codegen ni por el contexto tecnico."
            )

    assumption_markers = (
        "asumiendo",
        "se asume",
        "suponiendo",
        "selector sugerido",
        "ajustar selector",
    )
    normalized_code = _normalized(code)
    detected_assumptions = [marker for marker in assumption_markers if marker in normalized_code]
    if detected_assumptions:
        critical.append(
            "El codigo contiene suposiciones tecnicas explicitas: " + ", ".join(detected_assumptions) + "."
        )

    combined_output = _normalized(code + " " + json.dumps(test_data, ensure_ascii=False))
    required_concepts = {
        "poliza": ("poliza", "policy"),
        "cliente": ("cliente", "client"),
        "usuario": ("usuario", "username"),
        "contrasena": ("contrasena", "password"),
    }
    for label, aliases in required_concepts.items():
        if any(alias in source for alias in aliases) and not any(alias in combined_output for alias in aliases):
            critical.append(f"El flujo solicita {label}, pero no existe en testData ni en el codigo.")

    functional_values: List[Tuple[str, str]] = []
    for raw_line in str(payload.get("description", "")).splitlines():
        line = raw_line.strip().lstrip("-").strip()
        match = re.match(r'["“]?([^:"]{2,80})["”]?\s*:\s*(.+)$', line)
        if not match:
            continue
        field = match.group(1).strip(" \"“”")
        value = match.group(2).strip().strip(" \"“”.,")
        value = re.split(r"\s+\(|\s+[Yy]\s+luego\b|\s+[Hh]acer clic\b", value, maxsplit=1)[0].strip()
        if not value or value.lower().startswith(("hacer ", "seleccionar ", "cliquear ", "fecha del dia")):
            continue
        if len(value) <= 80:
            functional_values.append((field, value))

    for field, value in functional_values:
        normalized_value = _normalized(value)
        if normalized_value and normalized_value not in combined_output:
            critical.append(
                f"El valor funcional obligatorio '{field}: {value}' no aparece en testData ni en el codigo."
            )

    for key, value in test_data.items():
        if str(value).strip().lower().startswith("todo") and not re.search(
            rf"\btestData\.{re.escape(key)}\b",
            code,
        ):
            critical.append(f"El dato pendiente testData.{key} fue declarado pero nunca se utiliza en el flujo.")

    referenced_data = set(re.findall(r"\btestData\.(\w+)\b", code))
    undefined_data = sorted(referenced_data - set(test_data))
    if undefined_data:
        critical.append(
            "El codigo utiliza datos de prueba que no estan definidos: " + ", ".join(undefined_data) + "."
        )

    dynamic_text_selectors = re.findall(r"[`'\"]text=\$\{testData\.(\w+)\}[`'\"]", code)
    for key in dynamic_text_selectors:
        manual.append(
            f"Confirmar que el texto visible usado para seleccionar testData.{key} sea unico en la ventana correspondiente."
        )

    if "toLocaleDateString()" in code:
        warnings.append("La fecha usa el locale del servidor y puede cambiar de formato entre ambientes.")

    expected_source = _normalized(payload.get("description", "") + " " + payload.get("observations", ""))
    asserted_literals = re.findall(r"getByText\(\s*['\"]([^'\"]{8,120})['\"]", code, re.I)
    asserted_literals.extend(re.findall(r"locator\(\s*['\"]text=([^'\"]{8,120})['\"]", code, re.I))
    for literal in asserted_literals:
        normalized_literal = _normalized(literal)
        if normalized_literal and normalized_literal not in expected_source:
            critical.append(f"La validacion final parece inventada y no figura en el caso: '{literal}'.")

    asserted_urls = re.findall(r"toHaveURL\(\s*/([^/]{2,120})/", code, re.I)
    asserted_urls.extend(re.findall(r"toHaveURL\(\s*['\"]([^'\"]{2,200})['\"]", code, re.I))
    for expected_url in asserted_urls:
        if _normalized(expected_url) not in expected_source:
            critical.append(
                f"La assertion de URL '/{expected_url}/' fue inventada y no figura en el caso."
            )

    semantic_pairs = re.findall(
        r"\.(?:fill|selectOption)\(\s*selectors\.(\w+)\s*,\s*testData\.(\w+)",
        code,
    )
    semantic_tokens = {
        "client": {"client", "cliente"},
        "figure": {"figure", "figura"},
        "policy": {"policy", "poliza"},
        "claimtype": {"claimtype", "tiporeclamo", "tiposiniestro"},
        "indemnity": {"indemnity", "indemnizacion"},
    }
    for selector_key, data_key in semantic_pairs:
        normalized_selector = _normalized(selector_key).replace("_", "")
        normalized_data = _normalized(data_key).replace("_", "")
        selector_groups = {
            group for group, tokens in semantic_tokens.items()
            if any(token in normalized_selector for token in tokens)
        }
        data_groups = {
            group for group, tokens in semantic_tokens.items()
            if any(token in normalized_data for token in tokens)
        }
        if selector_groups and data_groups and selector_groups.isdisjoint(data_groups):
            critical.append(
                f"Asignacion semantica incompatible: selectors.{selector_key} recibe testData.{data_key}."
            )

    repeated_selectors = re.findall(r"page\.click\(\s*selectors\.(\w+)", code)
    for key in set(repeated_selectors):
        count = repeated_selectors.count(key)
        if count >= 3 and not re.search(rf"(?:modal|dialog|section).*selectors\.{re.escape(key)}", code, re.I | re.S):
            critical.append(
                f"El selector {key} se reutiliza {count} veces para distintas acciones sin quedar acotado "
                "a cada modal o seccion."
            )

    technical_context = bool(_clean(payload.get("codegen")) or _clean(payload.get("selector_context")))
    provisional = [
        key for key, value in selectors.items()
        if "data-testid" in value and value not in payload.get("selector_context", "")
    ]
    if provisional and not technical_context:
        manual.append(
            f"Confirmar o reemplazar {len(provisional)} selectores data-testid provisionales con selectores reales de VT."
        )

    description_steps = {
        int(value)
        for value in re.findall(r"(?m)^\s*(\d{1,2})\s*[\)\.-]", payload.get("description", ""))
    }
    code_steps = {int(value) for value in re.findall(r"//\s*Paso\s+(\d{1,2})", code, re.I)}
    missing = sorted(description_steps - code_steps)
    if missing:
        critical.append("Faltan referencias explicitas a los pasos: " + ", ".join(map(str, missing)) + ".")
    return {
        "critical": list(dict.fromkeys(critical)),
        "warnings": list(dict.fromkeys(warnings)),
        "manual": list(dict.fromkeys(manual)),
    }


def _repair_generated(
    payload: Dict[str, str],
    generated: Dict[str, Any],
    findings: Dict[str, List[str]],
) -> Dict[str, Any]:
    prompt = {
        "request": payload,
        "candidate": generated,
        "mandatory_fixes": findings["critical"],
        "additional_warnings": findings["warnings"],
    }
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "Sos el reparador final de un spec Playwright. Corrige obligatoriamente todos los defectos "
                    "indicados. No ocultes un defecto cambiando solamente las notas. Si falta informacion real, "
                    "deja una variable TODO y una accion tecnicamente compatible. No inventes assertions. "
                    + _generation_rules()
                    + " Si hay multiples procesos, conserva un test separado por proceso y no reduzcas la cobertura. "
                    + " Devuelve JSON con generated_code, selectors, test_data, ai_notes, covered_steps, "
                    "manual_actions y warnings."
                ),
            },
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        temperature=0,
        max_tokens=16000,
        top_p=1,
        response_format={"type": "json_object"},
    )
    repaired = _parse_ai_json(response.choices[0].message.content or "")
    if not repaired or not repaired.get("generated_code"):
        raise ValueError("La reparacion final no devolvio codigo valido.")
    return repaired


def _reviewed_result(
    payload: Dict[str, str],
    generated: Dict[str, Any],
    audit_data: Optional[Dict[str, Any]] = None,
    extra_note: str = "",
) -> Dict[str, Any]:
    audit_data = audit_data or {}
    code = _clean(
        audit_data.get("generated_code") or generated.get("generated_code"),
        MAX_CODE_LENGTH,
    )
    selectors = (
        audit_data.get("selectors")
        or _extract_json_object(code, "selectors")
        or generated.get("selectors", {})
    )
    test_data = (
        audit_data.get("test_data")
        or _extract_json_object(code, "testData")
        or generated.get("test_data", {})
    )
    findings = _deterministic_findings(code, payload, selectors, test_data)
    notes = [str(item) for item in audit_data.get("ai_notes", generated.get("ai_notes", []))]
    if extra_note:
        notes.append(extra_note)
    warnings = [str(item) for item in audit_data.get("warnings", [])]
    warnings.extend(findings["warnings"])
    warnings.extend(f"REQUIERE REVISION: {item}" for item in findings["critical"])
    manual_actions = [str(item) for item in audit_data.get("manual_actions", [])]
    manual_actions.extend(findings["manual"])
    manual_actions.extend(findings["critical"])
    try:
        score = int(audit_data.get("quality_score", 85))
    except (TypeError, ValueError):
        score = 85
    if findings["critical"]:
        score = min(score, 45)
    elif findings["manual"]:
        score = min(score, 75)
    return {
        "generated_code": code,
        "selectors": selectors,
        "test_data": test_data,
        "ai_notes": list(dict.fromkeys(notes)),
        "quality_score": max(0, min(100, score)),
        "covered_steps": [str(item) for item in audit_data.get("covered_steps", [])],
        "manual_actions": list(dict.fromkeys(manual_actions)),
        "warnings": list(dict.fromkeys(warnings)),
        "review_status": "needs_review" if findings["critical"] or findings["manual"] else "ready",
    }


def _audit_generated(payload: Dict[str, str], generated: Dict[str, Any]) -> Dict[str, Any]:
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=_audit_prompt(payload, generated),
            temperature=0,
            max_tokens=14000,
            top_p=1,
            response_format={"type": "json_object"},
        )
        audited = _parse_ai_json(response.choices[0].message.content or "")
        if not audited or not audited.get("generated_code"):
            raise ValueError("La auditoria no devolvio codigo valido.")
        code = _clean(audited.get("generated_code"), MAX_CODE_LENGTH)
        selectors = audited.get("selectors") or _extract_json_object(code, "selectors") or generated.get("selectors", {})
        test_data = audited.get("test_data") or _extract_json_object(code, "testData") or generated.get("test_data", {})
        findings = _deterministic_findings(code, payload, selectors, test_data)
        if findings["critical"]:
            try:
                repaired = _repair_generated(
                    payload,
                    {
                        "generated_code": code,
                        "selectors": selectors,
                        "test_data": test_data,
                        "ai_notes": audited.get("ai_notes", []),
                    },
                    findings,
                )
                return _reviewed_result(
                    payload,
                    generated,
                    {**audited, **repaired},
                    "El codigo paso por una reparacion automatica adicional.",
                )
            except Exception as exc:
                return _reviewed_result(
                    payload,
                    generated,
                    audited,
                    (
                        "La reparacion automatica no pudo completarse. "
                        f"Se conserva el codigo auditado como borrador ({type(exc).__name__})."
                    ),
                )
        return _reviewed_result(payload, generated, audited)
    except Exception as exc:
        return _reviewed_result(
            payload,
            generated,
            extra_note=(
                "La auditoria automatica no pudo completarse. "
                f"Se conserva el codigo original como borrador ({type(exc).__name__})."
            ),
        )


def _generate_with_ai(payload: Dict[str, str], video_path: Optional[Path] = None) -> Dict[str, Any]:
    fallback = _fallback_code(
        payload.get("title", ""),
        payload.get("initial_url", ""),
        payload.get("description", ""),
        payload.get("observations", ""),
    )
    frames, video_error = _extract_video_frames(video_path)
    if video_path and not frames:
        raise HTTPException(
            status_code=502,
            detail=(
                "No se pudieron extraer capturas del video. Verifica que el archivo no este danado."
                + _video_extraction_detail(video_error)
            ),
        )
    vision = _generate_with_vision(payload, frames)
    if vision:
        if not vision["selectors"]:
            vision["selectors"] = _extract_json_object(vision["generated_code"], "selectors") or fallback["selectors"]
        if not vision["test_data"]:
            vision["test_data"] = _extract_json_object(vision["generated_code"], "testData") or fallback["test_data"]
        return _audit_generated(payload, vision)
    if video_path:
        raise HTTPException(
            status_code=502,
            detail=(
                "La IA no pudo analizar correctamente las capturas del video. "
                "No se genero codigo usando solamente suposiciones textuales."
            ),
        )
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=_build_prompt(payload),
            temperature=0.1,
            max_tokens=10000,
            top_p=1,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or ""
        data = _parse_ai_json(raw)
        if not data:
            raise ValueError("La respuesta de IA no contiene JSON valido.")
        code = _clean(data.get("generated_code"), MAX_CODE_LENGTH)
        if not code:
            raise ValueError("La respuesta de IA no contiene codigo Playwright.")
        generated = {
            "generated_code": code,
            "selectors": data.get("selectors") or _extract_json_object(code, "selectors") or fallback["selectors"],
            "test_data": data.get("test_data") or _extract_json_object(code, "testData") or fallback["test_data"],
            "ai_notes": data.get("ai_notes") or ["Codigo generado por IA."],
        }
        return _audit_generated(payload, generated)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "La IA no pudo generar un spec valido. No se guardo una plantilla generica. "
                f"Intenta nuevamente o revisa la configuracion de OpenAI ({type(exc).__name__})."
            ),
        ) from exc


async def _save_video(video: Optional[UploadFile]) -> Optional[str]:
    if not video or not video.filename:
        return None
    filename = safe_filename(video.filename)
    if not filename.lower().endswith((".mp4", ".webm", ".mov", ".mkv")):
        raise HTTPException(status_code=400, detail="El video debe ser .mp4, .webm, .mov o .mkv.")
    dest = UPLOAD_DIR / f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{filename}"
    size = 0
    with dest.open("wb") as fh:
        while True:
            chunk = await video.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > 120 * 1024 * 1024:
                raise HTTPException(status_code=400, detail="El video supera el maximo permitido de 120 MB.")
            fh.write(chunk)
    return dest.name


def _video_path(video_name: Optional[str]) -> Optional[Path]:
    if not video_name:
        return None
    return UPLOAD_DIR / video_name


@router.post("/generate")
async def generate_playwright(
    mode: str = Form("text"),
    title: str = Form(...),
    requirement_id: str = Form(""),
    module: str = Form(""),
    initial_url: str = Form(""),
    execution_role: str = Form("qa"),
    description: str = Form(""),
    observations: str = Form(""),
    codegen: str = Form(""),
    selector_context: str = Form(""),
    video: Optional[UploadFile] = File(None),
    user: Dict[str, Any] = Depends(current_user),
):
    if user.get("role") != "qa":
        raise HTTPException(status_code=403, detail="El generador Playwright esta disponible solo para QA.")
    await _ensure_indexes()
    clean_mode = mode if mode in {"text", "video"} else "text"
    if clean_mode == "text" and not _clean(description):
        raise HTTPException(status_code=400, detail="Describe los pasos que debe realizar el caso.")
    if clean_mode == "video" and (not video or not video.filename):
        raise HTTPException(status_code=400, detail="Selecciona un video para generar el caso.")
    video_name = await _save_video(video) if clean_mode == "video" else None
    payload = {
        "mode": clean_mode,
        "title": _clean(title, 180),
        "requirement_id": _clean(requirement_id, 120),
        "module": _clean(module, 120),
        "initial_url": _clean(initial_url, 500),
        "execution_role": _clean(execution_role, 80),
        "description": _clean(description, 12000),
        "observations": _clean(observations, 6000),
        "codegen": _clean(codegen, 20000),
        "selector_context": _clean(selector_context, 12000),
        "video_file": video_name or "",
        "video_note": (
            "El video se analiza mediante frames distribuidos en toda su duracion. "
            "Las observaciones, codegen y selectores aportados complementan los elementos no visibles."
        ),
    }
    generated = _generate_with_ai(payload, _video_path(video_name))
    now = datetime.utcnow()
    doc = {
        **payload,
        **generated,
        "created_by": user["username"],
        "created_by_name": user.get("full_name") or "",
        "created_at": now,
        "updated_at": now,
    }
    result = await _db()[COLLECTION].insert_one(doc)
    doc["_id"] = result.inserted_id
    await record_activity(user, "Generacion Playwright", "playwright", f"Genero prueba: {doc['title']}", {"record_id": str(result.inserted_id), "mode": clean_mode})
    return {"record": _as_public(doc)}


@router.get("/generated")
async def list_generated(user: Dict[str, Any] = Depends(current_user)):
    if user.get("role") != "qa":
        raise HTTPException(status_code=403, detail="Playwright disponible solo para QA.")
    await _ensure_indexes()
    docs = await _db()[COLLECTION].find({}).sort("created_at", DESCENDING).to_list(300)
    return {"records": [_as_public(doc) for doc in docs]}


@router.get("/generated/{record_id}")
async def get_generated(record_id: str, user: Dict[str, Any] = Depends(current_user)):
    if user.get("role") != "qa":
        raise HTTPException(status_code=403, detail="Playwright disponible solo para QA.")
    if not ObjectId.is_valid(record_id):
        raise HTTPException(status_code=400, detail="ID invalido.")
    doc = await _db()[COLLECTION].find_one({"_id": ObjectId(record_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Prueba no encontrada.")
    return {"record": _as_public(doc)}


@router.put("/generated/{record_id}")
async def update_generated(record_id: str, payload: GeneratedUpdateIn, user: Dict[str, Any] = Depends(current_user)):
    if user.get("role") != "qa":
        raise HTTPException(status_code=403, detail="Playwright disponible solo para QA.")
    if not ObjectId.is_valid(record_id):
        raise HTTPException(status_code=400, detail="ID invalido.")
    current = await _db()[COLLECTION].find_one({"_id": ObjectId(record_id)})
    if not current:
        raise HTTPException(status_code=404, detail="Prueba no encontrada.")
    update = {k: v for k, v in payload.dict().items() if v is not None}
    update["updated_at"] = datetime.utcnow()
    await _db()[COLLECTION].update_one({"_id": current["_id"]}, {"$set": update})
    current.update(update)
    await record_activity(user, "Edicion Playwright", "playwright", f"Edito prueba: {current.get('title')}", {"record_id": record_id})
    return {"record": _as_public(current)}


@router.post("/generated/{record_id}/audit")
async def audit_generated(record_id: str, user: Dict[str, Any] = Depends(current_user)):
    if user.get("role") != "qa":
        raise HTTPException(status_code=403, detail="Playwright disponible solo para QA.")
    if not ObjectId.is_valid(record_id):
        raise HTTPException(status_code=400, detail="ID invalido.")
    current = await _db()[COLLECTION].find_one({"_id": ObjectId(record_id)})
    if not current:
        raise HTTPException(status_code=404, detail="Prueba no encontrada.")
    payload = {
        key: current.get(key, "")
        for key in (
            "mode", "title", "requirement_id", "module", "initial_url", "execution_role",
            "description", "observations", "codegen", "selector_context", "video_note",
        )
    }
    audited = _audit_generated(
        payload,
        {
            "generated_code": current.get("generated_code", ""),
            "selectors": current.get("selectors", {}),
            "test_data": current.get("test_data", {}),
            "ai_notes": current.get("ai_notes", []),
        },
    )
    audited["updated_at"] = datetime.utcnow()
    await _db()[COLLECTION].update_one({"_id": current["_id"]}, {"$set": audited})
    current.update(audited)
    await record_activity(
        user,
        "Auditoria Playwright",
        "playwright",
        f"Audito prueba: {current.get('title')}",
        {"record_id": record_id, "quality_score": current.get("quality_score", 0)},
    )
    return {"record": _as_public(current)}


@router.delete("/generated/{record_id}")
async def delete_generated(record_id: str, user: Dict[str, Any] = Depends(current_user)):
    if user.get("role") != "qa":
        raise HTTPException(status_code=403, detail="Playwright disponible solo para QA.")
    if not ObjectId.is_valid(record_id):
        raise HTTPException(status_code=400, detail="ID invalido.")
    current = await _db()[COLLECTION].find_one({"_id": ObjectId(record_id)})
    if not current:
        raise HTTPException(status_code=404, detail="Prueba no encontrada.")
    await _db()[COLLECTION].delete_one({"_id": current["_id"]})
    await record_activity(user, "Eliminacion Playwright", "playwright", f"Elimino prueba: {current.get('title')}", {"record_id": record_id})
    return {"ok": True}


@router.get("/generated/{record_id}/download")
async def download_generated(record_id: str, user: Dict[str, Any] = Depends(current_user)):
    if user.get("role") != "qa":
        raise HTTPException(status_code=403, detail="Playwright disponible solo para QA.")
    if not ObjectId.is_valid(record_id):
        raise HTTPException(status_code=400, detail="ID invalido.")
    doc = await _db()[COLLECTION].find_one({"_id": ObjectId(record_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Prueba no encontrada.")
    await record_activity(user, "Descarga Playwright", "playwright", f"Descargo spec: {doc.get('title')}", {"record_id": record_id})
    filename = f"{_slug(doc.get('title') or 'playwright-test')}.spec.ts"
    return PlainTextResponse(
        doc.get("generated_code") or "",
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
