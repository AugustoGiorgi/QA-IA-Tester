from __future__ import annotations

import os
import smtplib
from datetime import date, datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from bson import ObjectId
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from pymongo import ASCENDING, DESCENDING

from services.auth import _db, current_user
from services.activity import record_activity
from services.files import safe_filename


router = APIRouter(prefix="/api/internal-tasks", tags=["internal-tasks"])

BACKEND_DIR = Path(__file__).resolve().parent.parent
ATTACHMENTS_DIR = BACKEND_DIR / "data" / "task_attachments"
ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)

TASK_STATUSES = {"pendiente", "en_progreso", "bloqueada", "en_revision", "resuelta", "cerrada"}
PRIORITIES = {"baja", "media", "alta", "critica"}
TASK_ROLES = {"qa", "lider"}


class TaskCreate(BaseModel):
    title: str
    description: Optional[str] = ""
    qa_responsible: str
    developer_name: Optional[str] = ""
    functional_name: Optional[str] = ""
    date_from: Optional[str] = ""
    estimated_until: Optional[str] = ""
    priority: str = "media"
    status: str = "pendiente"


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    qa_responsible: Optional[str] = None
    developer_name: Optional[str] = None
    functional_name: Optional[str] = None
    date_from: Optional[str] = None
    estimated_until: Optional[str] = None


class CommentIn(BaseModel):
    text: str
    important: bool = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _oid(value: str) -> ObjectId:
    if not ObjectId.is_valid(value):
        raise HTTPException(status_code=400, detail="ID invÃ¡lido.")
    return ObjectId(value)


def _clean_text(value: Optional[str], max_len: int = 4000) -> str:
    return (value or "").replace("\x00", "").strip()[:max_len]


def _clean_status(value: str) -> str:
    value = (value or "").strip().lower()
    if value not in TASK_STATUSES:
        raise HTTPException(status_code=400, detail="Estado invÃ¡lido.")
    return value


def _clean_priority(value: str) -> str:
    value = (value or "").strip().lower()
    if value not in PRIORITIES:
        raise HTTPException(status_code=400, detail="Prioridad invÃ¡lida.")
    return value


def _parse_iso_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


async def _ensure_indexes() -> None:
    db = _db()
    await db["InternalTasks"].create_index([("assigned_to", ASCENDING), ("status", ASCENDING)])
    await db["InternalTasks"].create_index([("developer_username", ASCENDING), ("status", ASCENDING)])
    await db["TaskNotifications"].create_index([("username", ASCENDING), ("read", ASCENDING), ("created_at", DESCENDING)])


async def _user(username: str) -> Dict[str, Any]:
    clean = (username or "").strip().lower()
    user = await _db()["Users"].find_one({"username": clean, "active": True})
    if not user:
        raise HTTPException(status_code=400, detail=f"Usuario inexistente o inactivo: {username}")
    return user


def _require_task_role(user: Dict[str, Any]) -> None:
    if user.get("role") not in TASK_ROLES:
        raise HTTPException(status_code=403, detail="Tareas disponible solo para QA o LÃ­der.")


def _can_view(task: Dict[str, Any], user: Dict[str, Any]) -> bool:
    role = user.get("role")
    username = user.get("username")
    if role == "lider":
        return True
    return username in {task.get("qa_responsible"), task.get("assigned_to"), task.get("creator")}


def _public_task(task: Dict[str, Any]) -> Dict[str, Any]:
    task = dict(task)
    task["qa_responsible"] = task.get("qa_responsible") or task.get("assigned_to") or ""
    task["developer_name"] = task.get("developer_name") or task.get("developer_username") or ""
    task["functional_name"] = task.get("functional_name") or ""
    task["date_from"] = task.get("date_from") or ""
    task["estimated_until"] = task.get("estimated_until") or ""
    task.setdefault("comments", [])
    task.setdefault("history", [])
    task.setdefault("bugs", [])
    task["id"] = str(task.pop("_id"))
    return task


def _public_notification(notification: Dict[str, Any]) -> Dict[str, Any]:
    notification = dict(notification)
    notification["id"] = str(notification.pop("_id"))
    return notification


async def _task_or_404(task_id: str, user: Dict[str, Any]) -> Dict[str, Any]:
    task = await _db()["InternalTasks"].find_one({"_id": _oid(task_id)})
    if not task:
        raise HTTPException(status_code=404, detail="Tarea no encontrada.")
    if not _can_view(task, user):
        raise HTTPException(status_code=403, detail="No tenÃ©s acceso a esta tarea.")
    return task


def _task_link(task_id: str) -> str:
    base = os.getenv("APP_BASE_URL", "http://localhost:8000")
    return f"{base}/app/index.html#tasks/{task_id}"


async def _send_email(to_email: str, subject: str, body: str) -> bool:
    host = os.getenv("SMTP_HOST")
    sender = os.getenv("SMTP_FROM")
    if not host or not sender or not to_email:
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to_email
    msg.set_content(body)

    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASSWORD")
    use_tls = os.getenv("SMTP_TLS", "true").lower() in {"1", "true", "yes", "si"}

    try:
        with smtplib.SMTP(host, port, timeout=10) as smtp:
            if use_tls:
                smtp.starttls()
            if user and password:
                smtp.login(user, password)
            smtp.send_message(msg)
        return True
    except Exception:
        return False


async def _notify(usernames: List[str], task: Dict[str, Any], action: str, actor: str, detail: str = "") -> None:
    db = _db()
    seen = set()
    for username in [u for u in usernames if u]:
        if username in seen:
            continue
        seen.add(username)
        target = await db["Users"].find_one({"username": username})
        if not target:
            continue
        title = f"{action}: {task.get('title')}"
        body = (
            f"Tarea: {task.get('title')}\n"
            f"AcciÃ³n: {action}\n"
            f"Usuario involucrado: {actor}\n"
            f"Detalle: {detail or '-'}\n"
            f"Link: {_task_link(str(task['_id']))}\n"
        )
        sent = await _send_email(target.get("email", ""), title, body)
        await db["TaskNotifications"].insert_one({
            "username": username,
            "task_id": str(task["_id"]),
            "title": title,
            "message": body,
            "action": action,
            "actor": actor,
            "read": False,
            "email_sent": sent,
            "created_at": _now(),
        })


def _history(actor: str, action: str, detail: str = "") -> Dict[str, Any]:
    return {"actor": actor, "action": action, "detail": detail, "at": _now()}


@router.get("/summary")
async def summary(user: Dict[str, Any] = Depends(current_user)):
    _require_task_role(user)
    await _ensure_indexes()
    query = {} if user["role"] == "lider" else {
        "$or": [
            {"qa_responsible": user["username"]},
            {"assigned_to": user["username"]},
            {"creator": user["username"]},
        ]
    }
    db = _db()
    tasks = await db["InternalTasks"].find(query).sort("updated_at", DESCENDING).limit(8).to_list(8)
    notifications = await db["TaskNotifications"].find({"username": user["username"]}).sort("created_at", DESCENDING).limit(8).to_list(8)
    counts = {
        status: await db["InternalTasks"].count_documents({**query, "status": status})
        for status in TASK_STATUSES
    }
    return {
        "tasks": [_public_task(t) for t in tasks],
        "notifications": [_public_notification(n) for n in notifications],
        "counts": counts,
        "role": user["role"],
    }


@router.get("/tasks")
async def list_tasks(user: Dict[str, Any] = Depends(current_user)):
    _require_task_role(user)
    await _ensure_indexes()
    query = {} if user["role"] == "lider" else {
        "$or": [
            {"qa_responsible": user["username"]},
            {"assigned_to": user["username"]},
            {"creator": user["username"]},
        ]
    }
    docs = await _db()["InternalTasks"].find(query).sort("updated_at", DESCENDING).to_list(300)
    return {"tasks": [_public_task(doc) for doc in docs]}


@router.get("/overdue")
async def overdue_tasks(user: Dict[str, Any] = Depends(current_user)):
    _require_task_role(user)
    if user["role"] != "qa":
        return {"tasks": []}

    await _ensure_indexes()
    docs = await _db()["InternalTasks"].find({
        "$or": [
            {"qa_responsible": user["username"]},
            {"assigned_to": user["username"]},
        ],
        "status": {"$nin": ["resuelta", "cerrada"]},
        "estimated_until": {"$nin": ["", None]},
    }).sort("estimated_until", ASCENDING).to_list(100)

    today = date.today()
    overdue = [
        doc for doc in docs
        if (due_date := _parse_iso_date(doc.get("estimated_until"))) and due_date < today
    ]
    return {"tasks": [_public_task(doc) for doc in overdue]}


@router.get("/users")
async def task_users(user: Dict[str, Any] = Depends(current_user)):
    _require_task_role(user)
    docs = await _db()["Users"].find({"active": True, "role": {"$in": ["qa", "lider"]}}, {"username": 1, "role": 1, "full_name": 1, "email": 1}).sort("username", ASCENDING).to_list(500)
    return {
        "users": [
            {
                "username": doc.get("username"),
                "role": doc.get("role"),
                "full_name": doc.get("full_name") or "",
                "email": doc.get("email") or "",
            }
            for doc in docs
        ]
    }


@router.post("/tasks")
async def create_task(payload: TaskCreate, user: Dict[str, Any] = Depends(current_user)):
    _require_task_role(user)
    await _ensure_indexes()
    qa_responsible = await _user(payload.qa_responsible)
    if qa_responsible.get("role") != "qa":
        raise HTTPException(status_code=400, detail="El QA responsable debe tener rol QA.")
    now = _now()
    doc = {
        "title": _clean_text(payload.title, 180),
        "description": _clean_text(payload.description),
        "status": _clean_status(payload.status),
        "priority": _clean_priority(payload.priority),
        "qa_responsible": qa_responsible["username"],
        "assigned_to": qa_responsible["username"],
        "creator": user["username"],
        "developer_name": _clean_text(payload.developer_name, 180),
        "functional_name": _clean_text(payload.functional_name, 180),
        "date_from": _clean_text(payload.date_from, 40),
        "estimated_until": _clean_text(payload.estimated_until, 40),
        "created_at": now,
        "updated_at": now,
        "comments": [],
        "bugs": [],
        "attachments": [],
        "history": [_history(user["username"], "creo la tarea", f"Creada por {user['username']}")],
    }
    result = await _db()["InternalTasks"].insert_one(doc)
    doc["_id"] = result.inserted_id
    await _notify([doc["qa_responsible"], doc.get("creator")], doc, "Tarea asignada", user["username"])
    await record_activity(
        user,
        "Creacion de tarea",
        "tareas",
        f"Creo la tarea {doc['title']}",
        {
            "task_id": str(result.inserted_id),
            "titulo": doc["title"],
            "estado": doc["status"],
            "qa_responsible": doc["qa_responsible"],
            "prioridad": doc["priority"],
        },
    )
    return {"task": _public_task(doc)}

@router.put("/tasks/{task_id}")
async def update_task(task_id: str, payload: TaskUpdate, user: Dict[str, Any] = Depends(current_user)):
    _require_task_role(user)
    await _ensure_indexes()
    task = await _task_or_404(task_id, user)
    before = _public_task(task)
    update: Dict[str, Any] = {"updated_at": _now()}

    for field, value in payload.model_dump(exclude_unset=True).items():
        if field == "status":
            update[field] = _clean_status(value)
        elif field == "priority":
            update[field] = _clean_priority(value)
        elif field in {"title", "description"}:
            update[field] = _clean_text(value, 180 if field == "title" else 4000)
        elif field == "qa_responsible":
            qa_user = await _user(value)
            if qa_user.get("role") != "qa":
                raise HTTPException(status_code=400, detail="El QA responsable debe tener rol QA.")
            update[field] = qa_user["username"]
            update["assigned_to"] = qa_user["username"]
        elif field in {"developer_name", "functional_name"}:
            update[field] = _clean_text(value, 180)
        elif field in {"date_from", "estimated_until"}:
            update[field] = _clean_text(value, 40)

    labels = {
        "title": "titulo",
        "description": "descripcion",
        "status": "estado",
        "priority": "prioridad",
        "qa_responsible": "QA responsable",
        "developer_name": "desarrollador",
        "functional_name": "funcional",
        "date_from": "fecha desde",
        "estimated_until": "fecha estimada hasta",
    }
    changed = []
    for key, value in update.items():
        if key in {"updated_at", "assigned_to"}:
            continue
        old = before.get(key)
        if old != value:
            changed.append(f"{labels.get(key, key)}: {old or '-'} -> {value or '-'}")
    if changed:
        update["history"] = task.get("history", []) + [_history(user["username"], "edito la tarea", "; ".join(changed))]
    await _db()["InternalTasks"].update_one({"_id": task["_id"]}, {"$set": update})
    task.update(update)
    notify_users = [task.get("qa_responsible"), task.get("assigned_to"), task.get("creator")]
    await _notify(notify_users, task, "Tarea actualizada", user["username"], "; ".join(changed))
    if changed:
        status_changed = any(item.startswith("estado:") for item in changed)
        await record_activity(
            user,
            "Cambio de estado de tarea" if status_changed else "Edicion de tarea",
            "tareas",
            f"Actualizo la tarea {task.get('title')}",
            {
                "task_id": str(task["_id"]),
                "titulo": task.get("title"),
                "cambios": changed,
                "estado_actual": task.get("status"),
            },
        )
    return {"task": _public_task(task)}

@router.post("/tasks/{task_id}/comments")
async def add_comment(task_id: str, payload: CommentIn, user: Dict[str, Any] = Depends(current_user)):
    _require_task_role(user)
    task = await _task_or_404(task_id, user)
    comment = {"author": user["username"], "text": _clean_text(payload.text), "important": payload.important, "at": _now()}
    history = task.get("history", []) + [_history(user["username"], "agregÃ³ comentario", comment["text"][:160])]
    await _db()["InternalTasks"].update_one({"_id": task["_id"]}, {"$push": {"comments": comment}, "$set": {"history": history, "updated_at": _now()}})
    task["history"] = history
    if payload.important:
        await _notify([task.get("qa_responsible"), task.get("assigned_to"), task.get("creator")], task, "Comentario importante", user["username"], comment["text"])
    return {"ok": True}


@router.post("/tasks/{task_id}/bugs")
async def report_bug(
    task_id: str,
    description: str = Form(...),
    technical_notes: str = Form(""),
    images: Optional[List[UploadFile]] = File(default=None),
    user: Dict[str, Any] = Depends(current_user),
):
    raise HTTPException(status_code=410, detail="La opcion Reportar bug fue eliminada.")
    task = await _task_or_404(task_id, user)
    bug_id = uuid4().hex
    task_dir = ATTACHMENTS_DIR / str(task["_id"])
    task_dir.mkdir(parents=True, exist_ok=True)
    attachments = []
    for image in images or []:
        if not image.content_type or not image.content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail="Solo se permiten imÃ¡genes como adjunto de bug.")
        filename = f"{bug_id}_{safe_filename(image.filename)}"
        path = task_dir / filename
        path.write_bytes(await image.read())
        attachments.append({"filename": filename, "url": f"/api/internal-tasks/attachments/{task['_id']}/{filename}"})

    bug = {
        "id": bug_id,
        "reporter": user["username"],
        "description": _clean_text(description),
        "technical_notes": _clean_text(technical_notes),
        "attachments": attachments,
        "created_at": _now(),
    }
    history = task.get("history", []) + [_history(user["username"], "reportÃ³ bug", bug["description"][:160])]
    await _db()["InternalTasks"].update_one({"_id": task["_id"]}, {"$push": {"bugs": bug}, "$set": {"history": history, "updated_at": _now(), "status": "bloqueada"}})
    task["status"] = "bloqueada"
    task["history"] = history
    await _notify([task.get("developer_username"), task.get("creator")], task, "Bug reportado", user["username"], bug["description"])
    return {"bug": bug}


@router.get("/notifications")
async def notifications(user: Dict[str, Any] = Depends(current_user)):
    docs = await _db()["TaskNotifications"].find({"username": user["username"]}).sort("created_at", DESCENDING).limit(50).to_list(50)
    return {"notifications": [_public_notification(doc) for doc in docs]}


@router.post("/notifications/{notification_id}/read")
async def mark_read(notification_id: str, user: Dict[str, Any] = Depends(current_user)):
    await _db()["TaskNotifications"].update_one({"_id": _oid(notification_id), "username": user["username"]}, {"$set": {"read": True}})
    return {"ok": True}


@router.get("/attachments/{task_id}/{filename}")
async def attachment(task_id: str, filename: str, user: Dict[str, Any] = Depends(current_user)):
    task = await _task_or_404(task_id, user)
    path = ATTACHMENTS_DIR / str(task["_id"]) / safe_filename(filename)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Adjunto no encontrado.")
    return FileResponse(path)


