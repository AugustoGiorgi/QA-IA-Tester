import logging
import time
from typing import List, Optional
from services.ai import AIResponseError, build_messages, complete_async
from services.markdown_table import first_table, normalize_first_table


logger = logging.getLogger("qa_doc_analyzer.testcases")


def case_rows_from_markdown(markdown: str) -> List[dict]:
    table = first_table(markdown)
    headers = set(table.headers)
    required = {"objetivo de la prueba", "funcionalidad", "resultado esperado", "observaciones"}
    if not table.rows or not required.issubset(headers) or not ({"número", "numero"} & headers):
        raise AIResponseError("La IA no devolvió una tabla de casos válida. Intentá generar nuevamente.")
    rows = []
    for row_map in table.rows:
        objective = row_map.get("objetivo de la prueba", "").strip()
        expected = row_map.get("resultado esperado", "").strip()
        if not objective or not expected:
            raise AIResponseError("La IA devolvió casos incompletos. Intentá generar nuevamente.")
        rows.append({
            "Caso de Prueba": row_map.get("número", row_map.get("numero", "")) or str(len(rows) + 1),
            "Objetivo": objective,
            "Paso a Paso": "",
            "Resultado Esperado": expected,
            "Precondiciones": "",
            "Prioridad": "",
            "Datos de Prueba": "",
            "Funcionalidad": row_map.get("funcionalidad", ""),
            "Observaciones": row_map.get("observaciones", ""),
        })
    return rows

# =============== Parsers utilitarios ===============
def _parse_rows(md: str):
    """Devuelve (headers_lower, rows_list) a partir de una tabla Markdown simple."""
    lines = [ln.rstrip() for ln in md.splitlines() if ln.strip()]
    if not lines:
        return [], []

    sep_idx = None

    for i in range(len(lines) - 1):
        current = lines[i].strip()
        next_line = lines[i + 1].replace("|", "").replace(":", "").replace(" ", "").strip()

        if current.startswith("|") and next_line and set(next_line) == {"-"}:
            sep_idx = i
            break

    if sep_idx is None:
        return [], []

    headers = [h.strip().lower() for h in lines[sep_idx].strip().strip("|").split("|")]
    rows = []

    for ln in lines[sep_idx + 2:]:
        if not ln.strip().startswith("|"):
            continue

        cols = [c.strip() for c in ln.strip().strip("|").split("|")]

        if len(cols) != len(headers):
            continue

        rows.append(dict(zip(headers, cols)))

    return headers, rows


def _rows_signature(rows: List[dict]) -> int:
    """
    Firma de cobertura.
    Incluye observaciones para no perder casos parecidos pero con variantes reales.
    """
    keys_seen = set()

    for r in rows:
        k = (
            r.get("objetivo de la prueba", "").strip().lower(),
            r.get("funcionalidad", "").strip().lower(),
            r.get("resultado esperado", "").strip().lower(),
            r.get("observaciones", "").strip().lower(),
        )
        keys_seen.add(k)

    return len(keys_seen)


def _dedupe_table(md: str) -> str:
    """Deduplica filas exactas manteniendo el header."""
    lines = [ln for ln in md.splitlines()]
    if len(lines) < 3:
        return md

    header_idx = None

    for i in range(len(lines) - 1):
        current = lines[i].strip()
        next_line = lines[i + 1].replace("|", "").replace(":", "").replace(" ", "").strip()

        if current.startswith("|") and next_line and set(next_line) == {"-"}:
            header_idx = i
            break

    if header_idx is None:
        return md

    head = lines[:header_idx + 2]
    body = lines[header_idx + 2:]

    seen = set()
    new_body = []

    for ln in body:
        key = ln.strip().lower()

        if key and key not in seen:
            seen.add(key)
            new_body.append(ln)

    return "\n".join(head + new_body)


def _normalize_markdown_table(md: str) -> str:
    """
    Limpia texto extra y devuelve únicamente la primera tabla Markdown encontrada.
    Si no encuentra tabla, devuelve el texto original.
    """
    lines = [ln.rstrip() for ln in md.splitlines() if ln.strip()]

    header_idx = None

    for i in range(len(lines) - 1):
        current = lines[i].strip()
        next_line = lines[i + 1].replace("|", "").replace(":", "").replace(" ", "").strip()

        if current.startswith("|") and next_line and set(next_line) == {"-"}:
            header_idx = i
            break

    if header_idx is None:
        return md

    table_lines = lines[header_idx:]

    clean = []

    for ln in table_lines:
        if ln.strip().startswith("|"):
            clean.append(ln)
        else:
            break

    return "\n".join(clean)


def _renumber_table(md: str) -> str:
    """
    Renumera la primera columna de la tabla desde 1.
    Sirve para corregir continuaciones que repitan o salteen números.
    """
    lines = [ln for ln in md.splitlines()]
    if len(lines) < 3:
        return md

    header_idx = None

    for i in range(len(lines) - 1):
        current = lines[i].strip()
        next_line = lines[i + 1].replace("|", "").replace(":", "").replace(" ", "").strip()

        if current.startswith("|") and next_line and set(next_line) == {"-"}:
            header_idx = i
            break

    if header_idx is None:
        return md

    result = lines[:header_idx + 2]
    counter = 1

    for ln in lines[header_idx + 2:]:
        if not ln.strip().startswith("|"):
            continue

        cols = [c.strip() for c in ln.strip().strip("|").split("|")]

        if len(cols) < 5:
            continue

        cols[0] = str(counter)
        counter += 1

        result.append("| " + " | ".join(cols) + " |")

    return "\n".join(result)


def _is_invalid_followup_response(text: str) -> bool:
    """Detecta si el modelo indicó que no hay más cobertura real."""
    if not text:
        return True

    normalized = text.strip().upper()

    return (
        normalized == "SIN_CAMBIOS"
        or normalized.startswith("SIN_CAMBIOS")
        or "NO HAY REGLAS" in normalized
        or "NO HAY CASOS" in normalized
    )


# =============== Generación principal ===============
async def generate_testcases_markdown(
    doc_text: str,
    feedback_snippets: Optional[List[str]] = None,
    request_id: str = "",
) -> str:
    """
    Genera en una sola llamada para evitar continuaciones seriales que reenvian
    el documento y la tabla completa sin aportar cobertura consistente.
    """
    started_at = time.perf_counter()
    msgs = build_messages("casos-prueba", "Documento funcional:\n" + doc_text, feedback_snippets)
    logger.info(
        "testcase_generation_started request_id=%s document_chars=%d feedback_snippets=%d",
        request_id,
        len(doc_text),
        len(feedback_snippets or []),
    )
    try:
        out = await complete_async(msgs)
    except Exception:
        logger.exception(
            "testcase_generation_failed request_id=%s elapsed_seconds=%.2f",
            request_id,
            time.perf_counter() - started_at,
        )
        raise
    out = normalize_first_table(out)
    rows = first_table(out).rows
    logger.info(
        "testcase_generation_completed request_id=%s model_response_seconds=%.2f document_chars=%d feedback_snippets=%d generated_cases=%d",
        request_id,
        time.perf_counter() - started_at,
        len(doc_text),
        len(feedback_snippets or []),
        len(rows),
    )
    return out
