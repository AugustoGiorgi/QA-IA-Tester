from __future__ import annotations

import json
import base64
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from pymongo import ASCENDING, DESCENDING

from services.activity import record_activity
from services.ai import MODEL, client, complete
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
    await page.goto(`${{BASE_URL}}{url}`);

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


def _build_prompt(payload: Dict[str, str]) -> List[Dict[str, str]]:
    system = (
        "Sos un generador senior de pruebas Playwright para QA. "
        "Genera codigo TypeScript con @playwright/test, mantenible y listo para editar. "
        "No inventes credenciales reales. Usa variables arriba del archivo para selectors y testData. "
        "Si faltan datos tecnicos, deja selectores data-testid sugeridos y notas claras. "
        "Preferi getByRole/getByLabel si el usuario provee labels; si no, usa page.locator(selectors.nombre). "
        "No uses waitForTimeout. Inclui assertions. Responde en JSON estricto con claves: "
        "generated_code, selectors, test_data, ai_notes."
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
        return None


def _extract_video_frames(video_path: Optional[Path]) -> List[Path]:
    if not video_path or not video_path.exists() or not shutil.which("ffmpeg"):
        return []
    stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    out_dir = FRAME_DIR / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(out_dir / "frame_%02d.jpg")
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(video_path),
                "-vf",
                "fps=1/6,scale=960:-1",
                "-frames:v",
                "6",
                pattern,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=45,
        )
        return sorted(out_dir.glob("frame_*.jpg"))[:6]
    except Exception:
        return []


def _image_part(path: Path) -> Dict[str, Any]:
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{data}"}}


def _generate_with_vision(payload: Dict[str, str], frames: List[Path]) -> Optional[Dict[str, Any]]:
    if not frames:
        return None
    try:
        prompt = (
            "Analiza estos frames de un video de paso a paso y genera una prueba Playwright. "
            "Usa las observaciones como fuente principal cuando aclaren lo que no se ve. "
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
                    "content": "Sos un generador senior de pruebas Playwright con vision. No uses waitForTimeout y deja variables de selectors/testData arriba del spec.",
                },
                {"role": "user", "content": content},
            ],
            temperature=0.15,
            max_tokens=6000,
        )
        data = _parse_ai_json(resp.choices[0].message.content or "")
        if not data or not data.get("generated_code"):
            return None
        return {
            "generated_code": _clean(data.get("generated_code"), 20000),
            "selectors": data.get("selectors") or {},
            "test_data": data.get("test_data") or {},
            "ai_notes": data.get("ai_notes") or ["Codigo generado analizando frames del video."],
        }
    except Exception:
        return None


def _generate_with_ai(payload: Dict[str, str], video_path: Optional[Path] = None) -> Dict[str, Any]:
    fallback = _fallback_code(
        payload.get("title", ""),
        payload.get("initial_url", ""),
        payload.get("description", ""),
        payload.get("observations", ""),
    )
    vision = _generate_with_vision(payload, _extract_video_frames(video_path))
    if vision:
        if not vision["selectors"]:
            vision["selectors"] = _extract_json_object(vision["generated_code"], "selectors") or fallback["selectors"]
        if not vision["test_data"]:
            vision["test_data"] = _extract_json_object(vision["generated_code"], "testData") or fallback["test_data"]
        return vision
    try:
        raw = complete(_build_prompt(payload), temperature=0.15)
        data = _parse_ai_json(raw)
        if not data:
            return fallback
        code = _clean(data.get("generated_code"), 20000)
        if not code:
            return fallback
        return {
            "generated_code": code,
            "selectors": data.get("selectors") or _extract_json_object(code, "selectors") or fallback["selectors"],
            "test_data": data.get("test_data") or _extract_json_object(code, "testData") or fallback["test_data"],
            "ai_notes": data.get("ai_notes") or ["Codigo generado por IA."],
        }
    except Exception as exc:
        fallback["ai_notes"].append(f"No se pudo usar IA configurada; se uso plantilla base. Detalle: {type(exc).__name__}.")
        return fallback


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
    video: Optional[UploadFile] = File(None),
    user: Dict[str, Any] = Depends(current_user),
):
    if user.get("role") != "qa":
        raise HTTPException(status_code=403, detail="El generador Playwright esta disponible solo para QA.")
    await _ensure_indexes()
    clean_mode = mode if mode in {"text", "video"} else "text"
    video_name = await _save_video(video) if clean_mode == "video" else None
    payload = {
        "mode": clean_mode,
        "title": _clean(title, 180),
        "requirement_id": _clean(requirement_id, 120),
        "module": _clean(module, 120),
        "initial_url": _clean(initial_url, 500),
        "execution_role": _clean(execution_role, 80),
        "description": _clean(description),
        "observations": _clean(observations),
        "video_file": video_name or "",
        "video_note": "El video queda asociado al registro. La generacion usa principalmente observaciones y descripcion textual.",
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
