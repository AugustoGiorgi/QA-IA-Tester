import json
import re
import unicodedata
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
import os

from services.ai import complete

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB = os.getenv("MONGO_DB", "Life_Projects")

client = AsyncIOMotorClient(MONGO_URI)
db = client[MONGO_DB]

# ---------- Helpers ----------

def normalize(text: str) -> str:
    if not text:
        return ""
    text = text.lower().strip()
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return text

def extract_project_id(text: str) -> Optional[str]:
    m = re.search(r"(PRJ-\d{3})", text, re.IGNORECASE)
    if m:
        return m.group(1).upper()

    m2 = re.search(r"(proyecto|prj)\s*-?\s*(\d{1,3})", text, re.IGNORECASE)
    if m2:
        return f"PRJ-{int(m2.group(2)):03}"

    return None

async def find_project_flexible(message: str) -> Optional[str]:
    msg = normalize(message)

    async for p in db.Projects.find({}):
        if (
            normalize(p.get("name")) in msg or
            normalize(p.get("area")) in msg or
            normalize(p.get("_id")) in msg
        ):
            return p["_id"]

    return None

async def find_responsable_flexible(message: str) -> Optional[str]:
    msg = normalize(message)

    responsables = await db.Tasks.distinct("responsable")

    for r in responsables:
        if normalize(r) in msg:
            return r

    return None

# ---------- Parser ----------

async def parse_intent(message: str, context: Dict[str, Any]) -> Dict[str, Any]:

    parsed = {
        "intent": "resumen",
        "project_id": None,
        "filters": {
            "responsable": None
        }
    }

    pid = extract_project_id(message)
    if not pid:
        pid = await find_project_flexible(message)

    responsable = await find_responsable_flexible(message)

    if pid:
        parsed["project_id"] = pid

    if responsable:
        parsed["filters"]["responsable"] = responsable

    msg = normalize(message)

    # 🔥 FIX IMPORTANTE: orden correcto y palabras específicas
    if "por vencer" in msg or "proximo" in msg or "pronto" in msg or "vencen" in msg or "vencer" in msg:
        parsed["intent"] = "por_vencer"
    elif "vencidas" in msg or "atrasadas" in msg:
        parsed["intent"] = "vencidas"
    elif "cuantas" in msg or "cantidad" in msg:
        parsed["intent"] = "contar"
    elif "lista" in msg or "mostra" in msg:
        parsed["intent"] = "listar"
    elif "quien" in msg or "cargado" in msg:
        parsed["intent"] = "comparar"

    return parsed

# ---------- Query ----------

def build_query(parsed: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:

    project_id = parsed.get("project_id") or context.get("project_id")
    responsable = parsed["filters"].get("responsable")

    q = {}

    if project_id:
        q["project_id"] = {
            "$regex": f"^{project_id}$",
            "$options": "i"
        }

    if responsable:
        q["responsable"] = {
            "$regex": responsable,
            "$options": "i"
        }

    if not project_id and not responsable:
        return {"error": "Decime qué proyecto o persona querés consultar"}

    return q

# ---------- Ejecución ----------

async def exec_intent(parsed: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:

    q = build_query(parsed, context)

    if q.get("error"):
        return q

    intent = parsed["intent"]

    today = datetime.now().date()
    next_month = today + timedelta(days=30)

    tasks = []

    async for t in db.Tasks.find(q):

        fecha_str = t.get("fecha_fin")
        if not fecha_str:
            continue

        try:
            fecha = datetime.strptime(fecha_str, "%Y-%m-%d").date()
        except:
            continue

        es_vencida = fecha < today
        es_por_vencer = today <= fecha <= next_month

        if intent == "vencidas" and not es_vencida:
            continue

        if intent == "por_vencer" and not es_por_vencer:
            continue

        tasks.append({
            "title": t.get("title"),
            "responsable": t.get("responsable"),
            "estado": t.get("estado"),
            "progreso": int(t.get("progreso", 0) * 100),
            "fecha_fin": fecha_str
        })

    return {
        "intent": intent,
        "items": tasks,
        "count": len(tasks)
    }

# ---------- Respuesta ----------

def render_answer(message: str, data: Dict[str, Any]) -> str:

    if data.get("error"):
        return data["error"]

    if not data["items"]:
        return "No encontré resultados para esa consulta"

    return complete([
        {
            "role": "user",
            "content": f"""
Respondé claro y ordenado.

Datos:
{json.dumps(data)}

Pregunta:
{message}
"""
        }
    ])

# ---------- Contexto ----------

_SESSIONS = {}

def get_ctx(session_id):
    return _SESSIONS.get(session_id, {})

def save_ctx(session_id, ctx):
    _SESSIONS[session_id] = ctx

# ---------- MAIN ----------

async def chat_proyectos(message: str, session_id: str):

    try:
        ctx = get_ctx(session_id)

        parsed = await parse_intent(message, ctx)

        if parsed.get("project_id"):
            ctx["project_id"] = parsed["project_id"]

        save_ctx(session_id, ctx)

        data = await exec_intent(parsed, ctx)

        return render_answer(message, data)

    except Exception as e:
        print("ERROR:", e)
        return "Error interno en el sistema"