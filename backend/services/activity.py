from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from pymongo import ASCENDING, DESCENDING

from services.auth import _db, current_user, require_roles


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


@router.get("", dependencies=[Depends(require_roles("lider"))])
async def list_activity(
    username: Optional[str] = Query(None),
    module: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    limit: int = Query(120, ge=1, le=500),
):
    await _ensure_indexes()
    query: Dict[str, Any] = {}
    if username:
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
    docs = await _db()[COLLECTION].find(query).sort("created_at", DESCENDING).to_list(limit)
    users = await _db()[COLLECTION].distinct("username")
    modules = await _db()[COLLECTION].distinct("module")
    return {
        "items": [_public(doc) for doc in docs],
        "users": sorted([item for item in users if item]),
        "modules": sorted([item for item in modules if item]),
    }


@router.get("/{activity_id}", dependencies=[Depends(require_roles("lider"))])
async def get_activity(activity_id: str):
    if not ObjectId.is_valid(activity_id):
        raise HTTPException(status_code=400, detail="ID invalido.")
    doc = await _db()[COLLECTION].find_one({"_id": ObjectId(activity_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Movimiento no encontrado.")
    return {"item": _public(doc)}
