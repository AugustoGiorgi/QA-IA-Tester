# Python 3.8 compatible
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple
import os
import re
import json
import unicodedata

# ---- Texto / Template ----
try:
    from .parsing import docx_to_text, extract_tables
except Exception:
    def docx_to_text(path: str) -> str:
        return open(path, "rb").read().decode("utf-8", errors="ignore")
    def extract_tables(path: str) -> List[Dict[str, Any]]:
        return []

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")
TEMPLATE_JSON = os.path.join(TEMPLATE_DIR, "template_df_v1.json")

def _load_template() -> dict:
    with open(TEMPLATE_JSON, "r", encoding="utf-8") as f:
        return json.load(f)

# ---- Modelos ----
@dataclass
class SectionResult:
    name: str
    max_points: float
    score: float
    issues: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)
    strengths: List[str] = field(default_factory=list)
    improvements: List[str] = field(default_factory=list)
    rationale: str = ""

@dataclass
class QualityResult:
    version: str
    total_score: float
    section_results: List[SectionResult]
    coverage: Dict[str, Any]

# ---- Helpers ----
def _normalize(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().strip().lower()

def _words(s: str) -> int:
    return len(re.findall(r"\w+", s or ""))


# --- Helpers de scoring suave ---
def _frac_banded(frac: float, floor_if_nonempty: float = 0.3, block: str = "") -> float:
    """Suaviza el crédito: aplica piso si hay texto y recorta a [0,1]."""
    try:
        frac = float(frac)
    except Exception:
        frac = 0.0
    frac = max(0.0, min(1.0, frac))
    if _words(block) > 0:
        frac = max(frac, floor_if_nonempty)
    return frac

def _count_true(*flags: bool) -> int:
    return sum(1 for f in flags if f)

def _first_tx(block: str) -> Optional[str]:
    m = re.search(r"\b(?:vt|tx|trx|transacci[oó]n)[-_ ]?(\d{2,6})\b", block or "", flags=re.I)
    return f"VT-{m.group(1)}" if m else None

def _blk(blocks: Dict[str, str], *names: str) -> str:
    for n in names:
        if n in blocks and blocks[n]:
            return blocks[n]
    return ""

# “no aplica” (cualquiera de estas expresiones) ⇒ NO penaliza
NA_RE = re.compile(r"\b(no\s*aplica|n/?a|no\s*corresponde|sin\s*impacto)\b", re.I)

# --- Cortar el DF en bloques por sección (robusto a numeración y :) ---
def _extract_section_blocks(text: str, required_titles: List[str]) -> Dict[str, str]:
    lines = [l.rstrip() for l in re.sub(r"\r\n?", "\n", text).split("\n")]
    # normalizo títulos (sin tildes, lower)
    norm_required = [unicodedata.normalize("NFKD", t).encode("ascii","ignore").decode().lower() for t in required_titles]
    idxs: List[Tuple[int, str]] = []
    for i, raw in enumerate(lines):
        base = unicodedata.normalize("NFKD", raw).encode("ascii","ignore").decode().lower().strip()
        # header si: empieza por número+.)? + título + posible ":" y/o bolds
        for req in norm_required:
            if re.match(rf"^\s*(\d+[\.)]\s*)?{re.escape(req)}\s*:?\s*$", base):
                idxs.append((i, req))
                break
    idxs.sort(key=lambda x: x[0])

    out: Dict[str, str] = {}
    for j, (i, req) in enumerate(idxs):
        start = i + 1
        end = idxs[j + 1][0] if j + 1 < len(idxs) else len(lines)
        block = "\n".join(lines[start:end]).strip()
        out[required_titles[norm_required.index(req)]] = block
    return out

# (alias opcional)
def extract_section_blocks(text: str, required_titles: List[str]) -> Dict[str, str]:
    return _extract_section_blocks(text, required_titles)

# ---- Pesos: sinónimos + defaults (evita 0/0 por configuración) ----
DEFAULT_WEIGHTS: Dict[str, float] = {
    "situacion_actual": 1.0,
    "objetivo": 1.0,
    "funcionalidades": 4.0,
    "configuraciones": 1.0,
    "riesgos": 1.5,
    "sistemas_impactados": 1.0,
    "fuera_alcance": 0.5,
    "casos_prueba": 2.0,
    "comentarios_tecnicos": 1.5,
}

WEIGHT_KEYS: Dict[str, List[str]] = {
    "situacion_actual": ["situacion_actual", "situación_actual", "situacion", "situación"],
    "objetivo": ["objetivo", "objetivos"],
    "funcionalidades": ["funcionalidades", "funcionalidades_afectadas", "funcionalidades afectadas"],
    "configuraciones": ["configuraciones", "config"],
    "riesgos": ["riesgos", "riesgo"],
    "sistemas_impactados": ["sistemas_impactados", "sistemas impactados", "sistemas"],
    "fuera_alcance": ["fuera_alcance", "fuera de alcance", "exclusiones"],
    "casos_prueba": ["casos_prueba", "casos_de_prueba", "casos_pruebas", "casos de pruebas", "casos"],
    "comentarios_tecnicos": ["comentarios_tecnicos", "comentarios técnicos", "comentarios"],
}

def _get_weight(weights: Dict[str, Any], canonical: str) -> float:
    for k in WEIGHT_KEYS.get(canonical, [canonical]):
        if k in weights:
            val = weights[k]
            if isinstance(val, (int, float)) and val > 0:
                return float(val)
    return float(DEFAULT_WEIGHTS.get(canonical, 1.0))

# ---- Construcción del resultado de sección (con crédito parcial) ----
def _mk_sr_frac(
    name: str,
    maxp: float,
    frac: float,
    strengths: List[str],
    improvements: List[str],
    evidence: Optional[List[str]] = None,
    rationale_hint: str = ""
) -> SectionResult:
    frac = max(0.0, min(1.0, frac))
    score = round(maxp * frac, 2)
    rationale = rationale_hint or ""
    if strengths:
        rationale = (rationale + (" " if rationale else "") + f"✔ {strengths[0]}").strip()
    if improvements:
        rationale = (rationale + (" " if rationale else "") + f"⚠ {improvements[0]}").strip()
    return SectionResult(
        name=name,
        max_points=maxp,
        score=score,
        issues=improvements[:],
        evidence=(evidence or [])[:],
        strengths=strengths[:],
        improvements=improvements[:],
        rationale=rationale
    )

# ==========================================================
# Validaciones (con fracciones). Si hay contenido ⇒ nunca 0.
# ==========================================================

# 1) SITUACIÓN ACTUAL
def eval_situacion(block: str, maxp: float) -> SectionResult:
    name = "Situación Actual"
    if not block or not block.strip():
        return _mk_sr_frac(name, maxp, 0.0, [], ["Describir la situación actual o motivo que origina el RQ."], [])
    if NA_RE.search(block):
        return _mk_sr_frac(name, maxp, 1.0, ["'No aplica' — sin penalización."], [], [block[:220]], "Excluida de la nota.")

    # Heurística proporcional: 5 checks típicos
    has_motivo   = bool(re.search(r"motivo|problema|necesidad|dolor|justificaci[oó]n", block, re.I))
    has_alcance  = bool(re.search(r"alcance|[áa]rea|sector|m[oó]dulo", block, re.I))
    has_usuario  = bool(re.search(r"usuario|actor|rol(es)?", block, re.I))
    has_sistema  = bool(re.search(r"sistema|aplicaci[oó]n|plataforma|m[oó]dulo", block, re.I))
    has_datos    = bool(re.search(r"dato[s]?|informaci[oó]n|campos|entradas", block, re.I))

    checks = _count_true(has_motivo, has_alcance, has_usuario, has_sistema, has_datos)
    total  = 5
    frac   = _frac_banded(checks / max(1, total), 0.3, block)
    strengths = ["Motivo/situación claramente descrita."] if frac >= 0.8 else []
    improvements = []
    if checks < total:
        improvements.append("Ampliar la situación: motivo, alcance, actores, sistemas y datos relevantes.")

    return _mk_sr_frac(name, maxp, frac, strengths, improvements, [block[:220]])

# 2) OBJETIVO
def eval_objetivo(block: str, maxp: float) -> SectionResult:
    name = "Objetivo"
    if not block or not block.strip():
        return _mk_sr_frac(name, maxp, 0.0, [], ["Describir el objetivo del RQ (qué permitirá administrar/gestionar)."], [])
    if NA_RE.search(block):
        return _mk_sr_frac(name, maxp, 1.0, ["'No aplica' — sin penalización."], [], [block[:220]], "Excluida de la nota.")

    has_verbo_claro  = bool(re.search(r"\b(automatizar|gestionar|administrar|visualizar|integrar|emitir|consultar|actualizar)\b", block, re.I))
    has_quien        = bool(re.search(r"usuario|rol|actor", block, re.I))
    has_que          = bool(re.search(r"objetivo|fin|prop[oó]sito|permitir|lograr", block, re.I))
    has_medida       = bool(re.search(r"indicador|kpi|m[eé]trica|tiempo|porcentaje|volumen", block, re.I))
    has_contexto     = bool(re.search(r"alcance|m[oó]dulo|proceso|escenario", block, re.I))

    checks = _count_true(has_verbo_claro, has_quien, has_que, has_medida, has_contexto)
    total  = 5
    frac   = _frac_banded(checks / max(1, total), 0.3, block)
    strengths = ["Objetivo claro."] if frac >= 0.8 else []
    improvements = []
    if checks < total:
        improvements.append("Aclarar el objetivo con verbo de acción, quién, qué, medida y contexto.")

    return _mk_sr_frac(name, maxp, frac, strengths, improvements, [block[:220]])

# 3) FUNCIONALIDADES AFECTADAS
def eval_funcionalidades(block: str, maxp: float) -> SectionResult:
    name = "Funcionalidades Afectadas"
    if not block or not block.strip():
        return _mk_sr_frac(name, maxp, 0.0, [], ["Indicar VT nueva/ajuste, imagen o especificación de pantalla; campos (oblig./opt., valores, min/max) y validaciones con mensaje."], [])
    if NA_RE.search(block):
        return _mk_sr_frac(name, maxp, 1.0, ["'No aplica' — sin penalización."], [], [block[:220]], "Excluida de la nota.")

    strengths, improvements = [], []
    checks = 0
    total = 0

    text = block

    # VT nueva/ajuste
    total += 1
    vt = _first_tx(text)
    if vt:
        checks += 1
        strengths.append(f"Referencia a transacción: {vt}.")
    else:
        improvements.append("Indicar si es VT-#### nueva o ajuste.")

    # Imagen/maqueta o detalle de pantalla
    total += 1
    hay_ui = bool(re.search(r"(imagen|pantalla|mockup|maqueta|wireframe)", text, re.I))
    if hay_ui:
        checks += 1
        strengths.append("Incluye referencia visual de pantalla.")
    else:
        improvements.append("Incluir imagen/maqueta o describir los elementos de la pantalla.")

    if hay_ui:
        total += 1
        si_aclara_nueva_ajuste = bool(re.search(r"(nueva|ajuste)\s+de\s+pantalla|pantalla\s+(nueva|ajustada)", text, re.I))
        if si_aclara_nueva_ajuste:
            checks += 1
            strengths.append("Imagen de pantalla indicando nueva/ajuste.")
        else:
            improvements.append("Indicar en la imagen si la pantalla es nueva o ajuste de una existente.")

    # Campos (oblig/opt + valores o rangos)
    total += 1
    campos = bool(re.search(r"campo[s]?|formulario|input|dato[s]?|atributo[s]?", text, re.I))
    oblig_opt = bool(re.search(r"obligatori[oa]|optativ[oa]", text, re.I))
    valores   = bool(re.search(r"valores?\s+posibles|cat[aá]logo|lista\s+de\s+valores|enum", text, re.I))
    rangos    = bool(re.search(r"\b(min|m[ií]nimo|max|m[aá]ximo|longitud|l[óo]ngitud|tama[ñn]o)\b", text, re.I))
    if campos and oblig_opt and (valores or rangos):
        checks += 1
        strengths.append("Campos detallados (oblig/opt + valores/rangos).")
    else:
        improvements.append("Especificar oblig/opt y valores o rangos (min/max/longitud).")

    # Validaciones y mensaje
    total += 1
    tiene_valid = bool(re.search(r"validaci[oó]n(es)?|regla[s]?|mensaje[s]?\s+de\s+error|restricci[oó]n", text, re.I))
    if tiene_valid:
        checks += 1
        strengths.append("Incluye validaciones y mensaje de incumplimiento.")
    else:
        improvements.append("Incluir validaciones y el mensaje ante incumplimiento.")

    frac = checks / max(1, total)
    if _words(block) > 0:
        frac = max(frac, 0.4)   # piso suave si hay contenido
    return _mk_sr_frac(name, maxp, frac, strengths, improvements, [block[:220]])

# 4) CONFIGURACIONES (proporcional)
def eval_configuraciones(block: str, maxp: float) -> SectionResult:
    name = "Configuraciones"
    if not block or not block.strip():
        return _mk_sr_frac(name, maxp, 0.0, [], ["Comentar configuraciones necesarias o indicar 'No aplica'."], [])
    if NA_RE.search(block):
        return _mk_sr_frac(name, maxp, 1.0, ["'No aplica' — sin penalización."], [], [block[:220]], "Excluida de la nota.")

    # 6 checks frecuentes de configuración
    has_param     = bool(re.search(r"par[aá]metro|configuraci[oó]n|flag|toggle", block, re.I))
    has_valores   = bool(re.search(r"valor(es)?|cat[aá]logo|lista|enum|rango|min|max|longitud", block, re.I))
    has_ambiente  = bool(re.search(r"ambiente|dev|test|qa|prod|entorno", block, re.I))
    has_permisos  = bool(re.search(r"permiso|rol|perfil|acceso", block, re.I))
    has_dep       = bool(re.search(r"dependenc", block, re.I))
    has_tablas    = bool(re.search(r"tabla(s)? maestra(s)?|cat[aá]logo(s)?", block, re.I))

    checks = _count_true(has_param, has_valores, has_ambiente, has_permisos, has_dep, has_tablas)
    total  = 6
    frac   = _frac_banded(checks / max(1, total), 0.3, block)

    strengths = ["Config. necesarias documentadas."] if frac >= 0.8 else []
    improvements = []
    if checks < total:
        improvements.append("Agregar detalle de parámetros/valores, ambientes, permisos, dependencias y tablas maestras.")

    return _mk_sr_frac(name, maxp, frac, strengths, improvements, [block[:220]])

# 5) RIESGOS (ya proporcional)
def eval_riesgos(block: str, maxp: float) -> SectionResult:
    name = "Riesgos"
    if not block or not block.strip():
        return _mk_sr_frac(name, maxp, 0.0, [], ["Especificar riesgos y/o dependencias, o indicar 'No aplica'."], [])
    if NA_RE.search(block):
        return _mk_sr_frac(name, maxp, 1.0, ["'No aplica' — sin penalización."], [], [block[:220]], "Excluida de la nota.")

    strengths, improvements = [], []
    comps = 0
    total = 0

    total += 1
    has_risk = bool(re.search(r"riesgo|eventualidad|contingencia", block, re.I))
    if has_risk:
        comps += 1
        strengths.append("Describe al menos un riesgo.")
    else:
        improvements.append("Incluir al menos un riesgo relevante.")

    total += 1
    has_dep = bool(re.search(r"dependenc", block, re.I))
    if has_dep:
        comps += 1
        strengths.append("Menciona dependencias.")
    else:
        improvements.append("Incluir dependencias si existen.")

    total += 1
    has_impact = bool(re.search(r"impacto|consecuencia|severidad", block, re.I))
    if has_impact:
        comps += 1
        strengths.append("Indica impacto/consecuencia.")
    else:
        improvements.append("Indicar impacto/consecuencia del riesgo.")

    total += 1
    has_mit = bool(re.search(r"mitigaci[oó]n|plan|contingencia", block, re.I))
    if has_mit:
        comps += 1
        strengths.append("Define mitigaciones/planes.")
    else:
        improvements.append("Proponer mitigaciones o planes de contingencia.")

    frac = comps / max(1, total)
    if _words(block) > 0:
        frac = max(frac, 0.4)
    return _mk_sr_frac(name, maxp, frac, strengths, improvements, [block[:220]])

# 6) SISTEMAS IMPACTADOS
def eval_sistemas(block: str, maxp: float) -> SectionResult:
    name = "Sistemas impactados"
    if not block or not block.strip():
        return _mk_sr_frac(name, maxp, 0.0, [], ["Listar sistemas impactados e indicar el impacto, o marcar 'No aplica'."], [])
    if NA_RE.search(block):
        return _mk_sr_frac(name, maxp, 1.0, ["'No aplica' — sin penalización."], [], [block[:220]], "Excluida de la nota.")
    has_list = bool(re.search(r"\b(sap|vt|core|crm|erp|etl|api|servicio|m[oó]dulo|microservicio|proceso)\b", block, re.I))
    if has_list:
        return _mk_sr_frac(name, maxp, 1.0, ["Lista de sistemas impactados."], [], [block[:220]])
    if _words(block) > 0:
        return _mk_sr_frac(name, maxp, 0.6, [], ["Detallar los sistemas impactados y el tipo de impacto."], [block[:220]])
    return _mk_sr_frac(name, maxp, 0.0, [], ["Listar sistemas impactados."], [block[:220]])

# 7) FUERA DE ALCANCE
def eval_out_of_scope(block: str, maxp: float) -> SectionResult:
    name = "Fuera de Alcance"
    if not block or not block.strip():
        return _mk_sr_frac(name, maxp, 0.0, [], ["Indicar exclusiones o marcar 'No aplica'."], [])
    if NA_RE.search(block):
        return _mk_sr_frac(name, maxp, 1.0, ["'No aplica' — sin penalización."], [], [block[:220]], "Excluida de la nota.")
    if re.search(r"exclusi[oó]n|fuera de alcance|no incluye|no contempla", block, re.I):
        return _mk_sr_frac(name, maxp, 1.0, ["Exclusiones definidas."], [], [block[:220]])
    return _mk_sr_frac(name, maxp, 0.6, [], ["Aclarar las exclusiones."], [block[:220]])

# 8) CASOS DE PRUEBA (mantengo heurística previa: tabla/texto)
def _find_cases_table(tables: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    # busco headers típicos
    for t in tables or []:
        hdrs = [unicodedata.normalize("NFKD", (h or "")).encode("ascii", "ignore").decode().strip().lower() for h in (t.get("headers") or [])]
        ok = 0
        for h in hdrs:
            if re.search(r"(tipo|transacci[oó]n|funcionalidad|escenario|objetivo)", h):
                ok += 1
        if ok >= 2:
            return t
    return None

def eval_casos_prueba(block: str, maxp: float, tables: Optional[List[Dict[str, Any]]] = None) -> SectionResult:
    name = "Casos de Pruebas"

    # 1) Prioridad: si hay tabla válida en el documento, úsala
    t = _find_cases_table(tables or [])
    if t and t.get("rows"):
        rows = t["rows"]
        n = len(rows)
        # evidencia: primeras hasta 2 filas resumidas
        ev = []
        for r in rows[:2]:
            cols = [c for c in r if c]
            if cols:
                ev.append(" | ".join(cols)[:220])
        strengths = [f"Tabla de casos detectada con {n} fila(s)."]
        # fracción por cantidad mínima de casos (3 = completo; 1–2 parcial)
        frac = 1.0 if n >= 3 else 0.7 if n == 2 else 0.5
        return _mk_sr_frac(name, maxp, frac, strengths, [], ev)

    # 2) Sin tabla válida: heurísticas sobre el texto plano (fallback)
    if not block or not block.strip():
        return _mk_sr_frac(name, maxp, 0.0, [], ["Incluir tabla de casos con columnas Tipo/Transacción/Escenario/Objetivo o listar pruebas mínimas."], [])

    has_tipo   = re.search(r"\btipo\b", block, re.I)
    has_tx_fun = re.search(r"transacci[oó]n|funcionalidad", block, re.I)
    has_esc    = re.search(r"\bescenario\b", block, re.I)
    has_obj    = re.search(r"\bobjetivo\b", block, re.I)
    has_separators = ("\t" in block) or bool(re.search(r"\S\s{2,}\S", block))
    nonempty_lines = [ln for ln in block.splitlines() if ln.strip()]
    has_rows = len(nonempty_lines) >= 4

    if (has_tipo and has_tx_fun and has_esc and has_obj) and (has_separators or has_rows):
        return _mk_sr_frac(name, maxp, 0.8, ["Tabla de casos (texto plano) detectada."], [], [block[:220]])

    if _words(block) > 0:
        return _mk_sr_frac(name, maxp, 0.5, [], ["Agregar tabla de casos de prueba (mínimo 3)."], [block[:220]])
    return _mk_sr_frac(name, maxp, 0.0, [], ["Incluir casos de prueba."], [block[:220]])

# 9) COMENTARIOS TÉCNICOS (proporcional)
def eval_comentarios(block: str, maxp: float) -> SectionResult:
    name = "Comentarios técnicos"
    if not block or not block.strip():
        return _mk_sr_frac(name, maxp, 0.0, [], ["Incluir comentarios técnicos relevantes o marcar 'No aplica'."], [])
    if NA_RE.search(block):
        return _mk_sr_frac(name, maxp, 1.0, ["'No aplica' — sin penalización."], [], [block[:220]], "Excluida de la nota.")

    # Familias técnicas para puntuar proporcional
    has_api   = bool(re.search(r"\b(api|endpoint|payload|json|xml)\b", block, re.I))
    has_datos = bool(re.search(r"\b(bd|sql|[íi]ndice|esquema|modelo de datos|migraci[oó]n)\b", block, re.I))
    has_perf  = bool(re.search(r"\b(performance|latencia|carga|concurrencia|throughput)\b", block, re.I))
    has_secu  = bool(re.search(r"\b(seguridad|auth|oauth|jwt|encriptaci[oó]n|auditor[ií]a)\b", block, re.I))
    has_jobs  = bool(re.search(r"\b(batch|job|cron|cola|mensaje|broker|queue)\b", block, re.I))
    has_logs  = bool(re.search(r"\b(log|observabilidad|trazabilidad|alerta)\b", block, re.I))

    checks = _count_true(has_api, has_datos, has_perf, has_secu, has_jobs, has_logs)
    total  = 6
    frac   = _frac_banded(checks / max(1, total), 0.3, block)

    strengths = ["Comentarios técnicos provistos."] if frac >= 0.8 else []
    improvements = []
    if checks < total:
        improvements.append("Agregar detalle técnico: APIs, datos, performance, seguridad, jobs o logs/observabilidad.")

    return _mk_sr_frac(name, maxp, frac, strengths, improvements, [block[:220]])

# ==========================================================
# Entry point
# ==========================================================
def evaluate_docx_against_template(docx_path: str) -> QualityResult:
    tpl = _load_template()
    weights = tpl.get("weights", {})

    w_situacion       = _get_weight(weights, "situacion_actual")
    w_objetivo        = _get_weight(weights, "objetivo")
    w_funcionalidades = _get_weight(weights, "funcionalidades")
    w_config          = _get_weight(weights, "configuraciones")
    w_riesgos         = _get_weight(weights, "riesgos")
    w_sistemas        = _get_weight(weights, "sistemas_impactados")
    w_out             = _get_weight(weights, "fuera_alcance")
    w_casos           = _get_weight(weights, "casos_prueba")
    w_comentarios     = _get_weight(weights, "comentarios_tecnicos")

    required = tpl.get("required_sections", [])
    text = docx_to_text(docx_path)
    blocks = extract_section_blocks(text, required)
    tables = extract_tables(docx_path)

    results: List[SectionResult] = []

    # Eval por sección
    results.append(eval_situacion(_blk(blocks, "Situación Actual"), w_situacion))
    results.append(eval_objetivo(_blk(blocks, "Objetivo"), w_objetivo))
    results.append(eval_funcionalidades(_blk(blocks, "Funcionalidades Afectadas"), w_funcionalidades))
    results.append(eval_configuraciones(_blk(blocks, "Configuraciones"), w_config))
    results.append(eval_riesgos(_blk(blocks, "Riesgos"), w_riesgos))
    results.append(eval_sistemas(_blk(blocks, "Sistemas impactados"), w_sistemas))
    results.append(eval_out_of_scope(_blk(blocks, "Fuera de Alcance"), w_out))
    results.append(eval_casos_prueba(_blk(blocks, "Casos de Pruebas"), w_casos, tables))
    results.append(eval_comentarios(_blk(blocks, "Comentarios técnicos"), w_comentarios))

    total_score = round(sum(r.score for r in results), 2)

    # Cobertura: guardo un resumen por si querés mostrarlo en el front
    coverage: Dict[str, Any] = {"global_penalty": 0.0, "global_issues": []}
    cases_tbl = _find_cases_table(tables or [])
    if cases_tbl:
        coverage["cases_detected_rows"] = len(cases_tbl.get("rows") or [])
        coverage["cases_headers"] = cases_tbl.get("headers") or []

    return QualityResult(
        version=tpl.get("version", "template_df_v3"),
        total_score=total_score,
        section_results=results,
        coverage=coverage,
    )
