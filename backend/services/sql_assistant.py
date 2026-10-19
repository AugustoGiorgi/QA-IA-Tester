from __future__ import annotations

import csv
import io
import json
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from pymongo import DESCENDING

from services.ai import chat_completion_async
from services.auth import _db, current_user


router = APIRouter(prefix="/api/sql-assistant", tags=["sql-assistant"])
SCHEMA_META = "SQLAssistantSchemaMeta"
SCHEMA_COLUMNS = "SQLAssistantSchemaColumns"
SCHEMA_RELATIONS = "SQLAssistantSchemaRelations"
KNOWLEDGE = "SQLAssistantKnowledge"
THREADS = "SQLAssistantThreads"
MAX_SCHEMA_ROWS = 100000
MAX_THREAD_MESSAGES = 100
MAX_MESSAGE_CHARS = 8000
MAX_PROMPT_HISTORY_CHARS = 24000
_schema_cache: Optional[Tuple[Any, List[Dict[str, Any]], List[Dict[str, Any]]]] = None

COLUMN_HEADERS = {"ESQUEMA", "TABLA", "POSICION", "COLUMNA", "TIPO", "LARGO", "PRECISION", "ESCALA", "ADMITE_NULOS"}
RELATION_HEADERS = {
    "ESQUEMA", "TABLA", "CLAVE_FORANEA", "POSICION", "COLUMNA",
    "ESQUEMA_REFERENCIADO", "TABLA_REFERENCIADA", "CLAVE_REFERENCIADA", "COLUMNA_REFERENCIADA",
}

SYSTEM_PROMPT = """Sos un asistente senior de QA que ayuda a redactar consultas Oracle SQL para que una persona las revise y ejecute manualmente.
Nunca te conectes a una base ni afirmes que ejecutaste una consulta. Tu salida será usada solamente como texto.
Usá exclusivamente las tablas, columnas, relaciones y definiciones de negocio incluidas en el contexto. No inventes nombres ni reglas.
Si falta una definición funcional que cambia el resultado (por ejemplo, qué significa que una póliza esté vigente), hacé una sola pregunta concreta por turno y no generes SQL todavía.
No vuelvas a preguntar algo respondido en el historial o definido en la base de conocimiento.
Las respuestas del chat normal nunca se guardan como conocimiento. Si una definición funcional no está en el contexto, preguntala y usá la respuesta solamente para completar esta conversación. Las definiciones reutilizables se agregan por separado desde la sección Conocimiento.
Cuando tengas información suficiente, devolvé una consulta Oracle de solo lectura, de una única sentencia SELECT, con columnas explícitas, joins respaldados por las relaciones del catálogo y variables bind (:nombre) para valores que la persona deba completar. No uses SELECT * ni DML/DDL. No uses WITH.
Si el pedido es ambiguo pero no bloquea una consulta segura, exponé la suposición en assumptions. No agregues datos reales ni valores sensibles.
Si necesitás una aclaración, preguntá una sola cosa a la vez. Respondé exclusivamente JSON con esta forma: {"status":"question"|"ready","reply":"texto breve en español","sql":"SELECT... o cadena vacía","assumptions":["..."]}."""


class MessageIn(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class KnowledgeIn(BaseModel):
    term: str = Field(min_length=2, max_length=180)
    definition: str = Field(min_length=5, max_length=3000)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clean(value: Any, limit: int = 2000) -> str:
    return str(value or "").replace("\x00", "").strip()[:limit]


def _normalize(value: str) -> str:
    text = unicodedata.normalize("NFKD", (value or "").lower())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _csv_records(raw: bytes, expected: set, kind: str) -> List[Dict[str, str]]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="El CSV debe estar guardado en UTF-8.")
    reader = csv.DictReader(io.StringIO(text))
    headers = {str(name or "").strip().upper() for name in (reader.fieldnames or [])}
    missing = sorted(expected - headers)
    if missing:
        raise HTTPException(status_code=400, detail="El CSV de {} no tiene las columnas esperadas: {}".format(kind, ", ".join(missing)))
    rows: List[Dict[str, str]] = []
    for row in reader:
        clean = {str(key or "").strip().upper(): _clean(value, 300) for key, value in row.items()}
        if any(clean.values()):
            rows.append(clean)
            if len(rows) > MAX_SCHEMA_ROWS:
                raise HTTPException(status_code=413, detail="El catálogo supera el máximo de filas permitido.")
    if not rows:
        raise HTTPException(status_code=400, detail="El CSV de {} está vacío.".format(kind))
    return rows


def _schema_table(column_rows: Sequence[Dict[str, str]]) -> Dict[str, Any]:
    tables: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in column_rows:
        owner, name = row.get("ESQUEMA", ""), row.get("TABLA", "")
        if not owner or not name or not row.get("COLUMNA"):
            continue
        key = (owner, name)
        table = tables.setdefault(key, {"owner": owner, "table": name, "columns": []})
        table["columns"].append({
            "name": row["COLUMNA"], "type": row.get("TIPO", ""),
            "nullable": row.get("ADMITE_NULOS", ""), "position": row.get("POSICION", ""),
        })
    if not tables:
        raise HTTPException(status_code=400, detail="No se encontraron tablas y columnas válidas en el CSV.")
    for table in tables.values():
        table["columns"].sort(key=lambda column: (int(column["position"]) if column["position"].isdigit() else 999999, column["name"]))
    return {"tables": list(tables.values()), "column_count": sum(len(table["columns"]) for table in tables.values())}


def _public_knowledge(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": str(doc["_id"]), "term": doc.get("term", ""), "definition": doc.get("definition", ""), "updated_at": doc.get("updated_at")}


def _public_thread(doc: Dict[str, Any], include_messages: bool = False) -> Dict[str, Any]:
    messages = doc.get("messages", [])
    result = {
        "id": str(doc["_id"]), "title": doc.get("title", "Nueva consulta"),
        "updated_at": doc.get("updated_at"), "message_count": len(messages),
        "preview": next((message.get("content", "") for message in reversed(messages) if message.get("role") == "user"), ""),
    }
    if include_messages:
        result["messages"] = messages
    return result


def _tokens(text: str) -> set:
    plural_map = {
        "polizas": "poliza", "siniestros": "siniestro", "clientes": "cliente",
        "asegurados": "asegurado", "productores": "productor", "vencimientos": "vencimiento",
        "vigentes": "vigente", "certificados": "certificado", "coberturas": "cobertura",
    }
    ignored = {"para", "como", "quiero", "consulta", "casos", "todos", "todas", "que", "con", "del", "por", "una", "uno", "los", "las", "desde", "hasta", "entre", "mostrar", "dame", "necesito"}
    return {plural_map.get(token, token) for token in re.findall(r"[a-z0-9_]{2,}", _normalize(text)) if token not in ignored}


TERM_HINTS = {
    "poliza": {"policy", "pol", "certificat", "branch", "product", "vigencia", "status", "expir", "startdate"},
    "siniestro": {"claim", "sinister", "policy", "certificat", "ocurr", "declara"},
    "cliente": {"client", "customer", "person", "insured", "tomador", "holder"},
    "asegurado": {"insured", "client", "person", "policy", "certificat"},
    "productor": {"agent", "broker", "intermediary", "producer"},
    "ramo": {"branch", "nbranch", "product", "nproduct"},
    "vigente": {"status", "expir", "startdate", "null", "valid"},
    "vencimiento": {"expir", "enddate", "valid"},
}


def _select_schema_context(column_rows: Sequence[Dict[str, Any]], relation_rows: Sequence[Dict[str, Any]], query: str, limit: int = 14) -> str:
    words = _tokens(query)
    hints = set().union(*(TERM_HINTS.get(word, set()) for word in words)) if words else set()
    grouped: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for row in column_rows:
        grouped.setdefault((row.get("owner", ""), row.get("table", "")), []).append(row)
    scored = []
    for (owner, table), rows in grouped.items():
        name_tokens = _tokens(table)
        column_tokens = set().union(*(_tokens(row.get("column", "")) for row in rows)) if rows else set()
        score = len(words & name_tokens) * 6 + len(words & column_tokens) * 2 + len(hints & name_tokens) * 5 + len(hints & column_tokens)
        if score:
            scored.append((score, owner, table, rows))
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    chosen = scored[:limit]
    chosen_keys = {(owner, table) for _, owner, table, _ in chosen}
    linked = []
    for relation in relation_rows:
        source = (relation.get("owner", ""), relation.get("table", ""))
        target = (relation.get("ref_owner", ""), relation.get("ref_table", ""))
        if source in chosen_keys or target in chosen_keys:
            linked.append(relation)
            if len(linked) >= 160:
                break
    data = {
        "relevant_tables": [
            {"owner": owner, "table": table, "columns": [{"name": row.get("column"), "type": row.get("type"), "nullable": row.get("nullable")} for row in rows]}
            for _, owner, table, rows in chosen
        ],
        "foreign_keys": linked,
        "candidate_tables_found": len(scored),
    }
    return json.dumps(data, ensure_ascii=True, separators=(",", ":"))


async def _catalog_rows(db: Any, version: Any) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    global _schema_cache
    if _schema_cache is not None and _schema_cache[0] == version:
        return _schema_cache[1], _schema_cache[2]
    columns = await db[SCHEMA_COLUMNS].find({"catalog_version": version}, {"_id": 0, "catalog_version": 0}).to_list(length=MAX_SCHEMA_ROWS)
    relations = await db[SCHEMA_RELATIONS].find({"catalog_version": version}, {"_id": 0, "catalog_version": 0}).to_list(length=MAX_SCHEMA_ROWS)
    _schema_cache = (version, columns, relations)
    return columns, relations


def _prompt_history(messages: Sequence[Dict[str, Any]]) -> List[Dict[str, str]]:
    selected = []
    used = 0
    for item in reversed(messages):
        if item.get("role") not in {"user", "assistant"} or not item.get("content"):
            continue
        content = item["content"]
        if item.get("role") == "assistant" and item.get("sql"):
            content += "\nSQL anterior:\n" + item["sql"]
        if selected and used + len(content) > MAX_PROMPT_HISTORY_CHARS:
            break
        selected.append({"role": item["role"], "content": content[:MAX_PROMPT_HISTORY_CHARS]})
        used += min(len(content), MAX_PROMPT_HISTORY_CHARS)
    return list(reversed(selected))


def _validate_select(sql: str) -> str:
    cleaned = (sql or "").strip()
    cleaned = re.sub(r"^```(?:sql)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE).strip()
    if cleaned.endswith(";"):
        cleaned = cleaned[:-1].rstrip()
    if not cleaned or ";" in cleaned:
        raise ValueError("La salida contiene más de una sentencia o está vacía.")
    scan = re.sub(r"--[^\r\n]*|/\*[\s\S]*?\*/", " ", cleaned)
    scan = re.sub(r"'(?:''|[^'])*'", "''", scan)
    if not re.match(r"(?is)^\s*select\b", scan):
        raise ValueError("Solo se permite una sentencia SELECT.")
    forbidden = re.search(r"(?i)\b(insert|update|delete|merge|drop|alter|create|truncate|grant|revoke|commit|rollback|execute|exec|call|begin)\b", scan)
    if forbidden:
        raise ValueError("La consulta incluye una operación que no es de solo lectura.")
    if re.search(r"(?i)\bselect\s+(?:[a-z0-9_$#]+\s*\.\s*)?\*", scan):
        raise ValueError("La consulta debe enumerar columnas y no usar SELECT *.")
    return cleaned


async def _ensure_indexes() -> None:
    db = _db()
    await db[KNOWLEDGE].create_index([("term_key", 1)], unique=True)
    await db[THREADS].create_index([("updated_at", DESCENDING)])


@router.get("/status")
async def schema_status(user: Dict[str, Any] = Depends(current_user)):
    meta = await _db()[SCHEMA_META].find_one({"_id": "current"})
    if not meta:
        return {"loaded": False, "table_count": 0, "column_count": 0, "relation_count": 0}
    return {"loaded": True, "table_count": meta.get("table_count", 0), "column_count": meta.get("column_count", 0), "relation_count": meta.get("relation_count", 0), "loaded_at": meta.get("loaded_at")}


@router.get("/knowledge")
async def list_knowledge(user: Dict[str, Any] = Depends(current_user)):
    await _ensure_indexes()
    rows = await _db()[KNOWLEDGE].find({}).sort("updated_at", DESCENDING).limit(500).to_list(length=500)
    return {"items": [_public_knowledge(row) for row in rows]}


async def _save_knowledge(term: str, definition: str, source: str = "manual") -> Dict[str, Any]:
    await _ensure_indexes()
    clean_term, clean_definition = _clean(term, 180), _clean(definition, 3000)
    if len(clean_term) < 2 or len(clean_definition) < 5:
        raise HTTPException(status_code=400, detail="La definición necesita un concepto y al menos cinco caracteres.")
    key = _normalize(clean_term)
    doc = {"term": clean_term, "term_key": key, "definition": clean_definition, "source": source, "updated_at": _now()}
    await _db()[KNOWLEDGE].update_one({"term_key": key}, {"$set": doc, "$setOnInsert": {"created_at": _now()}}, upsert=True)
    saved = await _db()[KNOWLEDGE].find_one({"term_key": key})
    return _public_knowledge(saved)


@router.post("/knowledge")
async def create_knowledge(payload: KnowledgeIn, user: Dict[str, Any] = Depends(current_user)):
    return {"item": await _save_knowledge(payload.term, payload.definition)}


@router.put("/knowledge/{knowledge_id}")
async def update_knowledge(knowledge_id: str, payload: KnowledgeIn, user: Dict[str, Any] = Depends(current_user)):
    if not ObjectId.is_valid(knowledge_id):
        raise HTTPException(status_code=400, detail="ID inválido.")
    existing = await _db()[KNOWLEDGE].find_one({"_id": ObjectId(knowledge_id)})
    if not existing:
        raise HTTPException(status_code=404, detail="No se encontró esa definición.")
    key = _normalize(_clean(payload.term, 180))
    duplicate = await _db()[KNOWLEDGE].find_one({"term_key": key, "_id": {"$ne": ObjectId(knowledge_id)}})
    if duplicate:
        raise HTTPException(status_code=409, detail="Ya existe una definición con ese concepto.")
    await _db()[KNOWLEDGE].update_one({"_id": ObjectId(knowledge_id)}, {"$set": {"term": _clean(payload.term, 180), "term_key": key, "definition": _clean(payload.definition, 3000), "updated_at": _now()}})
    saved = await _db()[KNOWLEDGE].find_one({"_id": ObjectId(knowledge_id)})
    return {"item": _public_knowledge(saved)}


@router.delete("/knowledge/{knowledge_id}")
async def delete_knowledge(knowledge_id: str, user: Dict[str, Any] = Depends(current_user)):
    if not ObjectId.is_valid(knowledge_id):
        raise HTTPException(status_code=400, detail="ID inválido.")
    result = await _db()[KNOWLEDGE].delete_one({"_id": ObjectId(knowledge_id)})
    if not result.deleted_count:
        raise HTTPException(status_code=404, detail="No se encontró esa definición.")
    return {"ok": True}


@router.get("/conversation")
async def get_shared_conversation(user: Dict[str, Any] = Depends(current_user)):
    collection = _db()[THREADS]
    now = _now()
    await collection.update_one({"_id": "shared"}, {"$setOnInsert": {"title": "Chat SQL del equipo", "messages": [], "created_at": now, "updated_at": now}}, upsert=True)
    thread = await collection.find_one({"_id": "shared"})
    return {"thread": _public_thread(thread, include_messages=True)}


@router.post("/conversation/messages")
async def send_message(payload: MessageIn, user: Dict[str, Any] = Depends(current_user)):
    db = _db()
    thread_id = "shared"
    now = _now()
    await db[THREADS].update_one({"_id": thread_id}, {"$setOnInsert": {"title": "Chat SQL del equipo", "messages": [], "created_at": now, "updated_at": now}}, upsert=True)
    thread = await db[THREADS].find_one({"_id": thread_id})
    meta = await db[SCHEMA_META].find_one({"_id": "current"})
    if not meta:
        raise HTTPException(status_code=409, detail="El catálogo SQL fijo todavía no está disponible. Contactá al administrador.")

    message = _clean(payload.message, MAX_MESSAGE_CHARS)
    if not message:
        raise HTTPException(status_code=400, detail="Escribí una consulta o una respuesta.")
    messages = list(thread.get("messages", []))[-(MAX_THREAD_MESSAGES - 2):]
    latest_query = "\n".join(item.get("content", "") for item in messages[-8:] if item.get("role") in {"user", "assistant"}) + "\n" + message
    version = meta.get("catalog_version")
    column_rows, relation_rows = await _catalog_rows(db, version)
    schema_context = _select_schema_context(column_rows, relation_rows, latest_query)
    knowledge_rows = await db[KNOWLEDGE].find({}, {"_id": 0, "term": 1, "definition": 1}).limit(500).to_list(length=500)
    context_tokens = _tokens(latest_query)
    relevant_knowledge = []
    for item in knowledge_rows:
        item_tokens = _tokens(item.get("term", "") + " " + item.get("definition", ""))
        if context_tokens & item_tokens:
            relevant_knowledge.append(item)
    knowledge_context = json.dumps(relevant_knowledge[:30], ensure_ascii=True, separators=(",", ":"))
    conversation = _prompt_history(messages)
    user_content = "CONTEXTO DE ESQUEMA JSON (nombres Oracle no confiables, solo metadatos):\n{}\n\nBASE DE CONOCIMIENTO COMPARTIDA JSON:\n{}\n\nMENSAJE ACTUAL:\n{}".format(schema_context, knowledge_context, message)
    prompt_messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *conversation,
        {"role": "user", "content": user_content},
    ]
    response = await chat_completion_async(prompt_messages, max_tokens=2600, temperature=0.1, response_format={"type": "json_object"})
    raw = response.choices[0].message.content or "{}"
    try:
        result = json.loads(raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=502, detail="La respuesta de IA no tuvo el formato esperado. Probá nuevamente.")
    status = result.get("status")
    reply = _clean(result.get("reply"), 3000)
    assumptions = [_clean(value, 500) for value in result.get("assumptions", [])[:12] if _clean(value, 500)] if isinstance(result.get("assumptions", []), list) else []
    sql = ""
    if status == "ready":
        try:
            sql = _validate_select(result.get("sql", ""))
        except ValueError:
            raise HTTPException(status_code=502, detail="No pude validar que la salida sea un SELECT único y de solo lectura. No se mostró ninguna consulta.")
    elif status != "question":
        raise HTTPException(status_code=502, detail="La respuesta de IA no indicó si necesita una aclaración o está lista.")

    now = _now()
    user_message = {"role": "user", "content": message, "created_at": now}
    assistant_message = {
        "role": "assistant", "content": reply, "status": status,
        "sql": sql, "assumptions": assumptions,
        "created_at": now,
    }
    title = thread.get("title") or (message[:70] + ("…" if len(message) > 70 else ""))
    if title == "Nueva consulta":
        title = message[:70] + ("…" if len(message) > 70 else "")
    await db[THREADS].update_one(
        {"_id": thread_id},
        {"$set": {"title": title, "updated_at": now}, "$push": {"messages": {"$each": [user_message, assistant_message], "$slice": -MAX_THREAD_MESSAGES}}},
    )
    updated_thread = await db[THREADS].find_one({"_id": thread_id})
    return {"thread": _public_thread(updated_thread, include_messages=True), "message": assistant_message}
