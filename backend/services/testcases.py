from typing import List, Optional
from services.ai import build_messages, complete
from services.markdown_table import first_table, merge_tables, normalize_first_table, rows_signature

# =============== Prompt QA Senior ===============
TESTCASES_PROMPT = (
    "Actuá como QA Senior especializado en análisis funcional y diseño de casos de prueba.\n"
    "Leé el Documento Funcional (DF) provisto y diseñá casos de prueba claros, ejecutables y útiles para QA.\n"
    "El objetivo es cubrir correctamente el comportamiento funcional sin inventar requisitos.\n\n"

    "ENTREGABLE ÚNICO — FORMATO ESTRICTO:\n"
    "- Devolvé SOLAMENTE una tabla en Markdown con estas 5 columnas, en este orden y con estos nombres exactos:\n"
    "  número | objetivo de la prueba | funcionalidad | resultado esperado | observaciones\n"
    "- Sin texto antes o después de la tabla.\n"
    "- La columna 'número' debe ser numérica y secuencial desde 1.\n\n"

    "PRINCIPIO RECTOR — NO INVENTAR:\n"
    "- No inventes requisitos, pantallas, campos, reglas, mensajes, endpoints, estructuras de respuesta ni códigos de error que no estén en el DF.\n"
    "- No inventes validaciones técnicas típicas si el DF no las menciona o no se desprenden directamente de una regla funcional.\n"
    "- Podés generar casos negativos, alternativos o de borde solamente cuando estén explícitos en el DF o sean lógicamente necesarios para probar una regla indicada por el DF.\n"
    "- Si una validación se desprende directamente de una regla del DF, podés cubrirla, pero sin agregar comportamiento no mencionado.\n"
    "- No generar validaciones de formato, longitud, caracteres especiales, valores negativos, decimales, campos vacíos, permisos, roles, mensajes de error o integraciones si el DF no los menciona explícitamente o no los requiere funcionalmente.\n\n"

    "ENFOQUE QA:\n"
    "- Cada caso debe validar un comportamiento específico.\n"
    "- No generes casos genéricos como 'validar funcionamiento', 'validar pantalla', 'validar proceso' o 'validar carga correcta'.\n"
    "- No agrupes múltiples comportamientos distintos dentro de un mismo caso si deberían probarse por separado.\n"
    "- No repitas casos que validen exactamente lo mismo con otra redacción.\n"
    "- Generá la cantidad de casos necesaria para cubrir bien el DF, sin mínimo fijo y sin máximo fijo.\n"
    "- La cobertura no debe considerarse completa solo por mencionar una regla: evaluá si esa regla necesita escenarios positivos, negativos, alternativos, de borde, condicionales o de consistencia.\n\n"

    "COBERTURA FUNCIONAL:\n"
    "- Cubrí cada flujo, regla, validación y comportamiento explícito del DF.\n"
    "- Para cada comportamiento relevante, analizá naturalmente si corresponde probar:\n"
    "  1) escenario esperado o positivo,\n"
    "  2) escenario negativo explícito o lógicamente necesario,\n"
    "  3) escenario alternativo,\n"
    "  4) escenario borde mencionado o directamente derivado del DF,\n"
    "  5) consistencia entre pantallas, transacciones o procesos, si aplica,\n"
    "  6) comportamiento dinámico ante cambios de valores dentro del mismo flujo, si aplica.\n"
    "- Si el DF menciona varias pantallas, transacciones, módulos o procesos alcanzados, generá casos representativos por cada uno cuando el comportamiento pueda variar.\n"
    "- Si varias pantallas, transacciones o procesos comparten exactamente el mismo comportamiento, podés agrupar la validación solo cuando no se pierda cobertura.\n"
    "- Si una pantalla, transacción, módulo o proceso tiene un comportamiento particular, debe tener su propio caso.\n"
    "- Si el DF menciona que ciertos campos, secciones o acciones aparecen luego de una acción previa, contemplá ese comportamiento condicional.\n"
    "- Si el DF menciona autocompletado, cálculo, filtrado, habilitación, deshabilitación, actualización, persistencia, rechazo, aprobación o cualquier otro comportamiento automático, validá cuándo corresponde y cuándo no corresponde ejecutarlo, si el DF lo permite.\n"
    "- Si un valor depende de una selección previa, considerá si corresponde validar que el sistema actualice correctamente los datos asociados cuando esa selección cambia.\n"
    "- No limitarse a validar solo la carga inicial si el comportamiento del DF depende de cambios posteriores dentro del mismo flujo.\n"
    "- Si el DF menciona validaciones por duplicidad, relación única, existencia, estado, configuración, asociación o datos relacionados, cubrilo como escenarios separados cuando corresponda.\n"
    "- No agregues escenarios típicos de validación de input si no están definidos o directamente implicados por el DF.\n\n"

    "COMPORTAMIENTOS DINÁMICOS Y DEPENDENCIAS:\n"
    "- Si un campo o resultado depende de otro dato ingresado o seleccionado, validá la relación entre ambos.\n"
    "- Si el usuario cambia el dato base, validá que los datos dependientes se actualicen, se limpien o se recalculen solo cuando eso sea coherente con el DF.\n"
    "- Si una acción previa habilita, despliega o condiciona campos posteriores, reflejalo en los pasos del caso.\n"
    "- Si el DF indica filtrado de datos, validá tanto que se muestren los datos correctos como que no se muestren datos que no correspondan, siempre que eso se desprenda de la regla.\n\n"

    "REDACCIÓN DE COLUMNAS:\n"
    "- 'objetivo de la prueba': indicá concretamente qué se valida.\n"
    "- 'funcionalidad': indicá la regla, pantalla, transacción, módulo o proceso del DF que se está verificando.\n"
    "- 'resultado esperado': describí el efecto funcional esperado según el DF, sin inventar payloads, códigos técnicos ni mensajes no definidos.\n"
    "- 'observaciones': usala para aclarar variantes, datos necesarios, condiciones del caso, equivalencias o dependencias. No la conviertas en una columna de riesgo o prioridad.\n\n"

    "CALIDAD FINAL:\n"
    "- Antes de responder, revisá internamente que cada caso trace a una regla, flujo, validación o comportamiento del DF.\n"
    "- Eliminá casos redundantes.\n"
    "- Eliminá casos que dependan de reglas inventadas.\n"
    "- Eliminá casos genéricos.\n"
    "- Si una validación puede cubrirse como variante menor de otro caso, consolidala en observaciones en vez de crear una fila nueva.\n"
    "- Asegurate de que la tabla tenga exactamente las 5 columnas solicitadas.\n"
    "- Asegurate de que la numeración inicie en 1 y sea secuencial.\n\n"

    "SALIDA:\n"
    "- Únicamente la tabla con columnas: número | objetivo de la prueba | funcionalidad | resultado esperado | observaciones\n"
    "- Sin texto adicional.\n\n"

    "DOCUMENTO FUNCIONAL (DF):\n"
    "```\n{DF}\n```"
)


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
def generate_testcases_markdown(doc_text: str, feedback_snippets: Optional[List[str]] = None) -> str:
    """
    Genera casos de prueba en tabla Markdown.
    No fuerza mínimo ni máximo.
    Hace continuación solo si aparece cobertura real nueva.
    Corta si la IA no encuentra reglas explícitas pendientes.
    """
    msgs = build_messages(TESTCASES_PROMPT, doc_text, feedback_snippets)
    out = complete(msgs)

    out = normalize_first_table(out)

    table = first_table(out)
    headers, rows = table.headers, table.rows
    prev_sig = rows_signature(rows)

    if not headers:
        return out

    MAX_CONTINUATIONS = 4

    followup = (
        "Revisá la tabla anterior contra el DF. "
        "Agregá nuevas filas SOLO si identificás una regla, flujo, validación, comportamiento condicional, "
        "comportamiento dinámico o dependencia explícita del DF que no esté cubierta. "
        "No agregues casos de formato, longitud, caracteres especiales, campos vacíos, valores negativos, decimales, "
        "permisos, roles, datos usados en otra transacción ni mensajes de error, salvo que el DF los mencione explícitamente "
        "o sean indispensables para probar una regla funcional indicada por el DF. "
        "No agregues casos genéricos. "
        "No repitas casos equivalentes. "
        "No agregues casos por intuición QA si no trazan claramente al DF. "
        "Si no hay reglas explícitas pendientes, respondé exactamente: SIN_CAMBIOS. "
        "Si hay reglas pendientes, continuá la MISMA tabla Markdown EXACTA con estas CINCO columnas y los mismos encabezados, "
        "en el MISMO orden: "
        "'número', 'objetivo de la prueba', 'funcionalidad', 'resultado esperado', 'observaciones'. "
        "Continuá la numeración desde el último número usado. "
        "No agregues texto fuera de la tabla."
    )

    parts = [out]
    i = 0

    while i < MAX_CONTINUATIONS:
        probe_msgs = msgs + [
            {
                "role": "assistant",
                "content": "\n\n".join(parts),
            },
            {
                "role": "user",
                "content": followup,
            },
        ]

        nxt = complete(probe_msgs)

        if _is_invalid_followup_response(nxt):
            break

        nxt = normalize_first_table(nxt)

        combined = "\n\n".join(parts + [nxt])
        merged = merge_tables(combined)
        new_sig = rows_signature(first_table(merged).rows)

        if new_sig <= prev_sig:
            break

        parts.append(nxt)
        prev_sig = new_sig
        i += 1

    return merge_tables("\n\n".join(parts))
