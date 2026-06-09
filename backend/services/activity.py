from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from pymongo import ASCENDING, DESCENDING

from services.auth import _db, current_user


router = APIRouter(prefix="/api/activity", tags=["activity"])
COLLECTION = "UserActivity"


class ActivityIn(BaseModel):
    action: str
    module: Optional[str] = None
    detail: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


def _clean(value: Optional[str], fallback: str = "", max_len: int = 500) -> str:
    return ((value or fallback).replace("\x00", "").strip())[:max_len]


def _public(doc: Dict[str, Any]) -> Dict[str, Any]:
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    return doc


def _latest_task_activity(docs: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    result = []
    seen_tasks = set()
    for doc in docs:
        task_id = str((doc.get("metadata") or {}).get("task_id") or "").strip()
        if doc.get("module") == "tareas" and task_id:
            if task_id in seen_tasks:
                continue
            seen_tasks.add(task_id)
        result.append(doc)
        if len(result) >= limit:
            break
    return result


async def _ensure_indexes() -> None:
    col = _db()[COLLECTION]
    await col.create_index([("created_at", DESCENDING)])
    await col.create_index([("username", ASCENDING), ("created_at", DESCENDING)])
    await col.create_index([("module", ASCENDING), ("created_at", DESCENDING)])


async def record_activity(
    user: Dict[str, Any],
    action: str,
    module: str,
    detail: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    try:
        await _ensure_indexes()
        await _db()[COLLECTION].insert_one(
            {
                "username": user.get("username"),
                "role": user.get("role"),
                "full_name": user.get("full_name") or "",
                "action": _clean(action, "accion", 120),
                "module": _clean(module, "general", 120),
                "detail": _clean(detail, "", 1200),
                "metadata": metadata or {},
                "created_at": datetime.now(timezone.utc),
            }
        )
    except Exception:
        # La auditoria no debe romper el flujo principal de la app.
        return


@router.post("")
async def create_activity(payload: ActivityIn, user: Dict[str, Any] = Depends(current_user)):
    await record_activity(
        user,
        payload.action,
        payload.module or "frontend",
        payload.detail or "",
        payload.metadata or {},
    )
    return {"ok": True}


@router.get("")
async def list_activity(
    username: Optional[str] = Query(None),
    module: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    limit: int = Query(120, ge=1, le=500),
    user: Dict[str, Any] = Depends(current_user),
):
    await _ensure_indexes()
    query: Dict[str, Any] = {}
    can_view_all = user.get("role") == "lider"
    if not can_view_all:
        query["username"] = user["username"]
    elif username:
        query["username"] = username.strip().lower()
    if module:
        query["module"] = module.strip()
    if q:
        term = re.escape(q.strip())
        query["$or"] = [
            {"action": {"$regex": term, "$options": "i"}},
            {"detail": {"$regex": term, "$options": "i"}},
            {"username": {"$regex": term, "$options": "i"}},
            {"module": {"$regex": term, "$options": "i"}},
        ]
    query["action"] = {"$nin": ["Click", "Navegacion", "Playwright tab"], "$not": {"$regex": "^API "}}
    # Se consulta un margen amplio porque varias acciones de una misma tarea
    # se compactan en una sola entrada: siempre queda visible la mas reciente.
    fetch_limit = min(max(limit * 10, 500), 5000)
    raw_docs = await _db()[COLLECTION].find(query).sort("created_at", DESCENDING).to_list(fetch_limit)
    docs = _latest_task_activity(raw_docs, limit)
    scope = {} if can_view_all else {"username": user["username"]}
    users = await _db()[COLLECTION].distinct("username", scope)
    modules = await _db()[COLLECTION].distinct("module", scope)
    return {
        "items": [_public(doc) for doc in docs],
        "users": sorted([item for item in users if item]),
        "modules": sorted([item for item in modules if item]),
        "can_view_all": can_view_all,
    }


@router.get("/{activity_id}")
async def get_activity(activity_id: str, user: Dict[str, Any] = Depends(current_user)):
    if not ObjectId.is_valid(activity_id):
        raise HTTPException(status_code=400, detail="ID invalido.")
    query: Dict[str, Any] = {"_id": ObjectId(activity_id)}
    if user.get("role") != "lider":
        query["username"] = user["username"]
    doc = await _db()[COLLECTION].find_one(query)
    if not doc:
        raise HTTPException(status_code=404, detail="Movimiento no encontrado.")
    return {"item": _public(doc)}
