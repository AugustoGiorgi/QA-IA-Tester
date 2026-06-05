# backend/services/playwright_xlsx.py
from fastapi import APIRouter, UploadFile, File, HTTPException, Response
from typing import Dict, Any, List, Optional, Tuple
import io, re, csv, json, zipfile, tempfile, textwrap, pathlib, unicodedata
import pandas as pd
import yaml

router = APIRouter()

# --- Config
ROOT = pathlib.Path(__file__).resolve().parents[1]  # backend/
KNOWLEDGE = ROOT / "knowledge" / "visualtime"
PROCESSES_DIR = KNOWLEDGE / "processes"
DEFAULT_SELECTORS = KNOWLEDGE / "selectors.csv"

# --- Normalización & helpers de encabezados ---
CANON = [
    "Id. Caso de Prueba",
    "Objetivo de la prueba",
    "Funcionalidad",
    "Resultado Esperado",
    "Validación del resultado",
    "Observaciones",
    "Paso a Paso (auto)",
    "Datos de Prueba",
]
def _norm(s: str) -> str:
    s = str(s or "")
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = s.lower()
    return re.sub(r"[^a-z0-9]+", "", s)

SYN = {
    "idcasodeprueba": "Id. Caso de Prueba",
    "idcaso": "Id. Caso de Prueba",
    "id": "Id. Caso de Prueba",
    "objetivodelaprueba": "Objetivo de la prueba",
    "funcionalidad": "Funcionalidad",
    "resultadoesperado": "Resultado Esperado",
    "validaciondelresultado": "Validación del resultado",
    "observaciones": "Observaciones",
    "pasoapasoauto": "Paso a Paso (auto)",
    "pasoapaso": "Paso a Paso (auto)",
    "pasos": "Paso a Paso (auto)",
    "datosdeprueba": "Datos de Prueba",
}

def _map_header(cell: str) -> str:
    return SYN.get(_norm(cell), "")

# --- Utilidades varias ---
def slug(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE).strip().lower()
    return re.sub(r"[\s_-]+", "-", s)

def parse_kv(text: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if not text:
        return out
    for raw in str(text).splitlines():
        line = raw.strip()
        if not line or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v = v[1:-1]
        if v == "true":
            out[k] = True
        elif v == "false":
            out[k] = False
        else:
            out[k] = v
    return out

def read_selectors_from_zip(sel_zip: UploadFile) -> List[Dict[str, str]]:
    content = sel_zip.file.read()
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        name = next((n for n in z.namelist() if n.lower().endswith("selectors.csv")), None)
        if not name:
            raise HTTPException(400, "ZIP de selectores no contiene selectors.csv")
        with z.open(name) as f:
            return list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8")))

def read_selectors_default() -> List[Dict[str, str]]:
    if not DEFAULT_SELECTORS.exists():
        raise HTTPException(400, f"No encuentro {DEFAULT_SELECTORS}. Subí un ZIP de selectores o creá ese archivo.")
    with open(DEFAULT_SELECTORS, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))

def load_process_yaml(key: str) -> Dict[str, Any]:
    path = PROCESSES_DIR / f"{key}.yaml"
    if not path.exists():
        raise HTTPException(400, f"Proceso no soportado: {key}")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def detect_proceso(row: Dict[str, Any]) -> str:
    dsl = (row.get("Paso a Paso (auto)") or row.get("Paso a Paso") or "").strip()
    texto = " ".join(str(row.get(k, "") or "") for k in [
        "Objetivo de la prueba", "Funcionalidad", "Observaciones"
    ]).lower()

    if "@tabendosos" in dsl.lower() or "@botonnuevoendoso" in dsl.lower():
        return "endoso_poliza"
    if "@opcionca001" in dsl.lower() or "@comboviapago" in dsl.lower() or "@tabfacturacion" in dsl.lower():
        return "emitir_poliza"

    if any(p in texto for p in ["endoso", "modificación", "modificar"]):
        return "endoso_poliza"
    if any(p in texto for p in ["emisión", "emitir", "ca001", "poliza nueva", "póliza nueva"]):
        return "emitir_poliza"

    return "emitir_poliza"

def wanted_vars(proc: Dict[str, Any]) -> List[str]:
    return [v["name"] for v in (proc.get("variables") or [])]

def normalize_datos_prueba(raw: Any) -> Dict[str, Any]:
    return parse_kv(str(raw or ""))

def _extract_with_fuzzy_headers(contents: bytes) -> pd.DataFrame:
    """Acepta Excel con filas arriba del header. Devuelve DataFrame con headers CANON presentes."""
    df_raw = pd.read_excel(io.BytesIO(contents), header=None, dtype=str)
    header_idx = None
    mapped_headers: List[str] = []
    # Busco la fila de header en las primeras 15 filas con >=4 coincidencias por mapeo
    for i in range(min(15, len(df_raw))):
        row_vals = [str(x) for x in df_raw.iloc[i].tolist()]
        mapped = [_map_header(c) for c in row_vals]
        score = sum(1 for m in mapped if m)
        if score >= 4:
            header_idx = i
            mapped_headers = mapped
            break
    if header_idx is None:
        raise HTTPException(400, "No pude detectar los encabezados. Asegurate de tener columnas como 'Id. Caso de Prueba', 'Objetivo de la prueba' y 'Paso a Paso'.")

    # Construyo los nombres finales
    raw_headers = [str(x) for x in df_raw.iloc[header_idx].tolist()]
    final_headers: List[str] = []
    seen: Dict[str, int] = {}
    for j, raw in enumerate(raw_headers):
        name = mapped_headers[j] or raw.strip()
        if not name:
            name = f"col_{j}"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 1
        final_headers.append(name)

    df = df_raw.iloc[header_idx + 1 :].reset_index(drop=True)
    df.columns = final_headers

    # Normalizaciones mínimas: si hay "Paso a Paso" renómbralo al canónico
    for col in list(df.columns):
        if _map_header(col) == "Paso a Paso (auto)" and "Paso a Paso (auto)" not in df.columns:
            df = df.rename(columns={col: "Paso a Paso (auto)"})

    # Aseguro todas las CANON; si faltan, las creo vacías
    for c in CANON:
        if c not in df.columns:
            df[c] = ""

    # Filtro filas completamente vacías
    df = df[~(df[CANON].fillna("").apply(lambda r: "".join(map(str, r)), axis=1).str.strip() == "")]
    df = df.reset_index(drop=True)
    return df[CANON]

def extract_cases_from_xlsx(contents: bytes) -> List[Dict[str, Any]]:
    df = _extract_with_fuzzy_headers(contents)
    cases: List[Dict[str, Any]] = []
    for _, r in df.iterrows():
        row = {c: ("" if pd.isna(r.get(c)) else r.get(c)) for c in df.columns}
        case_id = str(row.get("Id. Caso de Prueba") or "").strip()
        title = str(row.get("Objetivo de la prueba") or row.get("Funcionalidad") or "").strip()
        dsl = str(row.get("Paso a Paso (auto)") or "").strip()
        data = normalize_datos_prueba(row.get("Datos de Prueba"))
        cases.append({
            "id": case_id or None,
            "title": title or "Caso sin título",
            "dsl": dsl,
            "datos": data,
            "row": row
        })
    # Autogenero ID si falta (CP-001, CP-002, …)
    seq = 1
    for c in cases:
        if not c["id"]:
            c["id"] = f"CP-{seq:03d}"
            seq += 1
    return cases

# --------------------------
#  Transpilado DSL → TS API
# --------------------------
_KV_RX = re.compile(r'(\w+)=(".*?"|\'.*?\'|[^\s]+)')

def _parse_args(arg_str: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for m in _KV_RX.finditer(arg_str or ""):
        k = m.group(1)
        v = m.group(2)
        out[k] = v
    return out

def _js_str_literal(val: str) -> str:
    """
    Devuelve un literal de string JS válido con comillas escapadas y sin evaluar ${...}.
    """
    return json.dumps(val)

def transpile_dsl_to_ts(dsl: str, case_id: str) -> Tuple[str, List[str]]:
    """
    Convierte el DSL línea a línea a sentencias Playwright + evidencias.
    Devuelve (codigo_ts, aliases_usados)
    """
    code: List[str] = []
    aliases: List[str] = []
    code.append("let __step = 1;")
    lines = [ln.strip() for ln in (dsl or "").splitlines()]
    for raw in lines:
        if not raw or raw.startswith("#") or raw.startswith("//"):
            continue
        line = re.sub(r"\s+", " ", raw).strip()
        parts = line.split(" ", 1)
        cmd = parts[0].upper()
        rest = parts[1] if len(parts) > 1 else ""
        args = _parse_args(rest)

        def shot(tag: str, name_expr: Optional[str] = None):
            if name_expr:
                code.append(f"await shot(page, '{case_id}', __step, '{tag}', {name_expr});")
            else:
                code.append(f"await shot(page, '{case_id}', __step, '{tag}');")
            code.append("__step += 1;")

        if cmd == "NAV":
            path_v = args.get("path", '"/"')
            path_lit = _js_str_literal(path_v)
            code.append("{")
            code.append(f"  const _path = expandVars({path_lit}, (data as any)).replace(/^[\"']|[\"']$/g, '');")
            code.append(f"  const _base = expandVars(\"${{base_url}}\", (data as any));")
            code.append(f"  if (!_base) throw new Error('Falta base_url en data JSON');")
            code.append(f"  await page.goto(_base + _path);")
            code.append("}")
            shot("NAV")

        elif cmd == "CLICK":
            sel = (args.get("sel") or "").lstrip("@")
            aliases.append(sel)
            code.append(f"await getLocator(page, '{sel}').click();")
            shot("CLICK")

        elif cmd == "TYPE":
            sel = (args.get("sel") or "").lstrip("@")
            val = args.get("value", '""')
            val_lit = _js_str_literal(val)
            aliases.append(sel)
            code.append("{ const _v = expandVars(" + val_lit + ", (data as any)).replace(/^[\"']|[\"']$/g, '');")
            code.append(f"  await getLocator(page, '{sel}').fill(_v); }}")
            shot("TYPE")

        elif cmd == "SELECT":
            sel = (args.get("sel") or "").lstrip("@")
            opt = args.get("option", '""')
            opt_lit = _js_str_literal(opt)
            aliases.append(sel)
            code.append("{ const _opt = expandVars(" + opt_lit + ", (data as any)).replace(/^[\"']|[\"']$/g, '');")
            code.append(f"  try {{ await getLocator(page, '{sel}').selectOption({{ label: _opt }}); }}")
            code.append(f"  catch {{ await getLocator(page, '{sel}').selectOption(_opt); }} }}")
            shot("SELECT")

        elif cmd == "ASSERT_TEXT":
            sel = (args.get("sel") or "").lstrip("@")
            contains = args.get("contains", '""')
            contains_lit = _js_str_literal(contains)
            aliases.append(sel)
            code.append("{ const _t = expandVars(" + contains_lit + ", (data as any)).replace(/^[\"']|[\"']$/g, '');")
            code.append(f"  await expectPW(getLocator(page, '{sel}')).toContainText(_t); }}")
            shot("ASSERT")

        elif cmd == "WAIT":
            sel = (args.get("sel") or "").lstrip("@")
            state = args.get("state", "visible")
            timeout = args.get("timeout", "10000")
            aliases.append(sel)
            state_lit = _js_str_literal(state)
            code.append(f"await getLocator(page, '{sel}').waitFor({{ state: {state_lit}, timeout: {int(timeout) if timeout.isdigit() else 10000} }});")
            shot("WAIT")

        elif cmd == "SHOT":
            name_v = args.get("name", '"shot"')
            name_lit = _js_str_literal(name_v)
            expr = f"expandVars({name_lit}, (data as any)).replace(/^[\\\"']|[\\\"']$/g, '')"
            shot("SHOT", expr)

        elif cmd == "SET":
            if args:
                k, v = next(iter(args.items()))
                k_lit = _js_str_literal(k)
                v_lit = _js_str_literal(v)
                code.append(f"(data as any)[{k_lit}] = expandVars({v_lit}, (data as any)).replace(/^[\"']|[\"']$/g, '');")
            shot("SET")

        elif cmd in ("USE", "API", "LOGIN"):
            code.append(f"// {cmd} no-op (a implementar si se define macro específica)")
            shot(cmd)

        else:
            code.append(f"// Comando desconocido: {cmd}  (línea: {raw})")
            shot("UNKNOWN")

    return ("\n".join(code), aliases)

# --------------------------
#  Helpers TS (runtime)
# --------------------------
def runtime_ts() -> str:
    return textwrap.dedent("""
    import { Page } from '@playwright/test';
    import { promises as fs } from 'fs';

    export function expandVars(t: string, data: Record<string, any>): string {
      return String(t ?? '').replace(/\\$\\{([^}]+)\\}/g, (_, k) => {
        const v = (data as any)[k];
        return v === undefined || v === null ? '' : String(v);
      });
    }

    export async function shot(page: Page, caseId: string, step: number, tag: string, name?: string) {
      const fname = `artifacts/screenshots/${caseId}/step_${String(step).padStart(2,'0')}_${tag}${name ? '_' + name : ''}.png`;
      await fs.mkdir(fname.split('/').slice(0,-1).join('/'), { recursive: true }).catch(()=>{});
      await page.screenshot({ path: fname, fullPage: true });
    }

    export function ensureRequiredVars(data: Record<string, any>, required: string[]) {
      const missing = required.filter(k => {
        const v = (data as any)[k];
        return v === undefined || v === null || String(v).trim() === '';
      });
      if (missing.length) throw new Error('Faltan datos obligatorios: ' + missing.join(', '));
    }
    """)

def fixture_ts() -> str:
    return textwrap.dedent("""
    import { test as base, expect } from '@playwright/test';
    export const test = base;
    export const expectPW = expect;
    """)

def playwright_config_ts() -> str:
    return textwrap.dedent("""
    import { defineConfig } from '@playwright/test';
    export default defineConfig({
      testDir: './tests',
      use: {
        headless: true,
        screenshot: 'off',
        trace: 'retain-on-failure',
      },
      reporter: [
        ['html', { open: 'never' }],
        ['json', { outputFile: 'artifacts/report.json' }]
      ],
      outputDir: 'artifacts/test-results'
    });
    """)

def package_json() -> str:
    return json.dumps({
        "name": "playwright-project",
        "private": True,
        "devDependencies": {
            "@playwright/test": "^1.46.0",
            "typescript": "^5.4.0",
            "ts-node": "^10.9.2"
        },
        "scripts": {
            "test": "playwright test",
            "test:all": "playwright test",
            "test:headed": "playwright test --headed",
            "test:trace": "playwright test --trace on"
        }
    }, indent=2)

def tsconfig_json() -> str:
    return json.dumps({
        "compilerOptions": {
            "target": "ES2020",
            "module": "commonjs",
            "types": ["@playwright/test"],
            "esModuleInterop": True,
            "resolveJsonModule": True,
            "skipLibCheck": True
        }
    }, indent=2)

def readme_md() -> str:
    return textwrap.dedent("""
    # Proyecto Playwright (generado)

    ## Correr todo
    1) `npm i`
    2) `npx playwright install`
    3) `npm run test:all`

    ## Correr por caso (filtrado por tag @<id>)
    - `npx playwright test -g @CP-001`
    - `npx playwright test -g @1`  # si el Excel traía "1" como Id

    ## Completar variables
    - Editá `tests/data/<ID>.json` con base_url, user, pass y el resto. Si hay vacíos, el test falla al inicio.

    ## Evidencias
    - Screens por paso en `artifacts/screenshots/<ID>/*`
    - Reporter HTML + JSON: `playwright-report/*` y `artifacts/report.json`

    ## Generador
    Este ZIP fue generado por **transpiler v2** (sin executeDSL).
    """)

def spec_ts_transpiled(case_id: str, title: str, ts_steps: str, required_vars: List[str]) -> str:
    slug_title = slug(title) or "caso"
    req = ", ".join([f"'{k}'" for k in required_vars])
    return textwrap.dedent(f"""
    // tests/specs/{case_id}.{slug_title}.spec.ts
    // GEN: transpiler-v2
    import {{ test, expectPW }} from '../helpers/fixture';
    import * as data from '../data/{case_id}.json';
    import {{ expandVars, shot, ensureRequiredVars }} from '../helpers/runtime';
    import {{ getLocator }} from '../selectors/vt';

    test.describe('@{case_id}', () => {{
      test('{title}', async ({{ page }}) => {{
        ensureRequiredVars((data as any), [{req}]);
{ts_steps if ts_steps.strip() else "        // Sin pasos DSL, completá tu Excel o editá este spec."}
      }});
    }});
    """)

# --------------------------
#   Endpoint principal (v2)
# --------------------------
@router.post("/build-xlsx-v2")
async def build_xlsx(
    cases_xlsx: UploadFile = File(...),
    selectors_zip: Optional[UploadFile] = File(None),
):
    try:
        xlsx_bytes = await cases_xlsx.read()
    except Exception:
        raise HTTPException(400, "No pude leer el Excel")

    selectors_rows = read_selectors_from_zip(selectors_zip) if selectors_zip else read_selectors_default()
    cases = extract_cases_from_xlsx(xlsx_bytes)

    tmpdir = tempfile.TemporaryDirectory()
    root = pathlib.Path(tmpdir.name)

    (root / "tests" / "specs").mkdir(parents=True, exist_ok=True)
    (root / "tests" / "helpers").mkdir(parents=True, exist_ok=True)
    (root / "tests" / "selectors").mkdir(parents=True, exist_ok=True)
    (root / "tests" / "data").mkdir(parents=True, exist_ok=True)
    (root / "artifacts").mkdir(parents=True, exist_ok=True)
    (root / "scripts").mkdir(parents=True, exist_ok=True)

    # helpers/config
    (root / "tests" / "helpers" / "runtime.ts").write_text(runtime_ts(), encoding="utf-8")
    (root / "tests" / "helpers" / "fixture.ts").write_text(fixture_ts(), encoding="utf-8")
    (root / "playwright.config.ts").write_text(playwright_config_ts(), encoding="utf-8")
    (root / "package.json").write_text(package_json(), encoding="utf-8")
    (root / "tsconfig.json").write_text(tsconfig_json(), encoding="utf-8")
    (root / "README.md").write_text(readme_md(), encoding="utf-8")

    aliases_needed: List[str] = []

    for row in cases:
        proceso = detect_proceso(row["row"])
        proc = load_process_yaml(proceso)

        vars_required = wanted_vars(proc)
        datos = row["datos"]
        data_json: Dict[str, Any] = {v: datos.get(v, "") for v in vars_required}

        dsl = row["dsl"] or (proc.get("template_dsl") or "")
        if not dsl.strip():
            dsl = "# DSL vacío; completalo en tu Excel o en el spec."

        # Transpilar DSL → TS
        ts_steps, used_aliases = transpile_dsl_to_ts(dsl, row["id"])
        aliases_needed.extend(used_aliases)

        # Escribo data del caso
        (root / "tests" / "data" / f"{row['id']}.json").write_text(
            json.dumps(data_json, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        # Spec TS
        spec_code = spec_ts_transpiled(row["id"], row["title"], "        " + ts_steps.replace("\n", "\n        "), vars_required)
        spec_path = root / "tests" / "specs" / f"{row['id']}.{slug(row['title'])}.spec.ts"
        spec_path.write_text(spec_code, encoding="utf-8")

    # selectors/vt.ts solo con alias usados (si no, todos)
    def gen_vt_ts(selectors_rows: List[Dict[str, str]], aliases_needed: List[str]) -> str:
        cleaned = []
        alias_set = set(a.lower() for a in aliases_needed) if aliases_needed else None
        for r in selectors_rows:
            alias = (r.get("alias") or "").strip()
            if not alias:
                continue
            if alias_set and alias.lower() not in alias_set:
                continue
            cleaned.append({
                "alias": alias,
                "strategy": (r.get("strategy") or "xpath").strip(),
                "locator": (r.get("locator") or "").strip()
            })
        if alias_set and not cleaned:  # fallback: incluí todos
            for r in selectors_rows:
                a = (r.get("alias") or "").strip()
                if not a:
                    continue
                cleaned.append({
                    "alias": a,
                    "strategy": (r.get("strategy") or "xpath").strip(),
                    "locator": (r.get("locator") or "").strip()
                })

        lines = ["export const SELECTORS: Record<string,{strategy:string,locator:string}> = {"]
        for r in cleaned:
            loc = r["locator"].replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'  "{r["alias"]}": {{ strategy: "{r["strategy"]}", locator: "{loc}" }},')
        lines.append("};\n")
        lines.append("""
export function getLocator(page: any, key: string) {
  const s = SELECTORS[key];
  if (!s) throw new Error(`Alias no definido: ${key}`);
  switch (s.strategy) {
    case 'xpath': return page.locator(`xpath=${s.locator}`);
    case 'css': return page.locator(s.locator);
    case 'text': return page.getByText(s.locator);
    case 'role': {
      const [role, name] = s.locator.split(':', 2);
      return page.getByRole(role as any, name ? { name } : {});
    }
    case 'testid': return page.getByTestId(s.locator);
    default: return page.locator(s.locator);
  }
}
""")
        return "\n".join(lines)

    vt_code = gen_vt_ts(selectors_rows, aliases_needed)
    (root / "tests" / "selectors" / "vt.ts").write_text(vt_code, encoding="utf-8")

    # ZIP
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in root.rglob("*"):
            if p.is_file():
                z.write(p, p.relative_to(root).as_posix())
    buf.seek(0)
    tmpdir.cleanup()
    headers = {"Content-Disposition": "attachment; filename=playwright_from_excel.zip"}
    return Response(content=buf.read(), media_type="application/zip", headers=headers)
