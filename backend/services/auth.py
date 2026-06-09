from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel
from pymongo import ASCENDING


load_dotenv()

router = APIRouter(prefix="/api/auth", tags=["auth"])

MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB = os.getenv("MONGO_DB", "Life_Projects")
PBKDF2_ITERATIONS = 120_000
SESSION_TTL_HOURS = int(os.getenv("SESSION_TTL_HOURS", "8"))
BOOTSTRAP_USER = os.getenv("BOOTSTRAP_ADMIN_USER", "lider")
BOOTSTRAP_PASSWORD = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "lider123")
VALID_ROLES = {"qa", "funcional", "lider"}

_client: Optional[AsyncIOMotorClient] = None
_sessions: Dict[str, Dict[str, Any]] = {}


class LoginIn(BaseModel):
    username: str
    password: str


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str
    confirm_password: str


class UserIn(BaseModel):
    username: str
    email: str
    password: Optional[str] = None
    role: str
    active: bool = True
    full_name: Optional[str] = None


class UserUpdateIn(BaseModel):
    email: Optional[str] = None
    password: Optional[str] = None
    role: Optional[str] = None
    active: Optional[bool] = None
    full_name: Optional[str] = None


def _db():
    global _client
    if not MONGO_URI:
        raise HTTPException(status_code=500, detail="MONGO_URI no está configurado.")
    if _client is None:
        _client = AsyncIOMotorClient(MONGO_URI)
    return _client[MONGO_DB]


def _normalize_username(username: str) -> str:
    value = (username or "").strip().lower()
    if not value or len(value) > 80:
        raise HTTPException(status_code=400, detail="Usuario inválido.")
    return value


def _normalize_role(role: str) -> str:
    value = (role or "").strip().lower()
    if value not in VALID_ROLES:
        raise HTTPException(status_code=400, detail="Rol inválido.")
    return value


def _normalize_email(email: str) -> str:
    value = (email or "").strip().lower()
    if "@" not in value or len(value) > 180:
        raise HTTPException(status_code=400, detail="Email inválido.")
    return value


def _hash_password(password: str, salt: Optional[str] = None) -> Dict[str, str]:
    if not password or len(password) < 6:
        raise HTTPException(status_code=400, detail="La contraseña debe tener al menos 6 caracteres.")
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return {"salt": salt, "password_hash": digest.hex()}


def _verify_password(password: str, user: Dict[str, Any]) -> bool:
    salt = user.get("salt")
    expected = user.get("password_hash")
    if not salt or not expected:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return hmac.compare_digest(digest.hex(), expected)


async def _ensure_users_collection() -> None:
    db = _db()
    users = db["Users"]
    await users.create_index([("username", ASCENDING)], unique=True, name="uniq_username")
    count = await users.count_documents({})
    if count == 0:
        secret = _hash_password(BOOTSTRAP_PASSWORD)
        await users.insert_one({
            "username": _normalize_username(BOOTSTRAP_USER),
            "email": os.getenv("BOOTSTRAP_ADMIN_EMAIL", "lider@local"),
            "role": "lider",
            "active": True,
            "full_name": "Usuario líder inicial",
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
            **secret,
        })


def _public_user(user: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(user.get("_id", "")),
        "username": user.get("username"),
        "email": user.get("email") or "",
        "role": user.get("role"),
        "active": bool(user.get("active", True)),
        "full_name": user.get("full_name") or "",
    }


async def current_user(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="No autenticado.")
    token = authorization.split(" ", 1)[1].strip()
    session = _sessions.get(token)
    if not session or session["expires_at"] < datetime.utcnow():
        _sessions.pop(token, None)
        raise HTTPException(status_code=401, detail="Sesión expirada.")

    await _ensure_users_collection()
    user = await _db()["Users"].find_one({"username": session["username"], "active": True})
    if not user:
        raise HTTPException(status_code=401, detail="Usuario inactivo o inexistente.")
    return user


def require_roles(*roles: str) -> Callable:
    allowed = {role.lower() for role in roles}

    async def dependency(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
        if user.get("role") not in allowed:
            raise HTTPException(status_code=403, detail="No tenés permisos para esta operación.")
        return user

    return dependency


@router.post("/login")
async def login(payload: LoginIn):
    await _ensure_users_collection()
    username = _normalize_username(payload.username)
    user = await _db()["Users"].find_one({"username": username, "active": True})
    if not user or not _verify_password(payload.password, user):
        raise HTTPException(status_code=401, detail="Usuario o contraseña inválidos.")
    if user.get("role") not in VALID_ROLES:
        raise HTTPException(status_code=403, detail="Perfil no habilitado.")

    token = secrets.token_urlsafe(32)
    _sessions[token] = {
        "username": username,
        "role": user["role"],
        "expires_at": datetime.utcnow() + timedelta(hours=SESSION_TTL_HOURS),
    }
    return {"token": token, "user": _public_user(user)}


@router.get("/me")
async def me(user: Dict[str, Any] = Depends(current_user)):
    return {"user": _public_user(user)}


@router.post("/logout")
async def logout(authorization: Optional[str] = Header(None)):
    if authorization and authorization.lower().startswith("bearer "):
        _sessions.pop(authorization.split(" ", 1)[1].strip(), None)
    return {"ok": True}


@router.post("/change-password")
async def change_password(payload: PasswordChangeIn, user: Dict[str, Any] = Depends(current_user)):
    if not _verify_password(payload.current_password, user):
        raise HTTPException(status_code=400, detail="La contrasena actual no es correcta.")
    if payload.new_password != payload.confirm_password:
        raise HTTPException(status_code=400, detail="Las contrasenas nuevas no coinciden.")
    if payload.new_password == payload.current_password:
        raise HTTPException(status_code=400, detail="La nueva contrasena debe ser diferente a la actual.")

    secret = _hash_password(payload.new_password)
    await _db()["Users"].update_one(
        {"_id": user["_id"]},
        {"$set": {**secret, "updated_at": datetime.utcnow()}},
    )
    username = user.get("username")
    for token, session in list(_sessions.items()):
        if session.get("username") == username:
            _sessions.pop(token, None)
    return {"ok": True}


@router.get("/users", dependencies=[Depends(require_roles("lider"))])
async def list_users():
    await _ensure_users_collection()
    docs = await _db()["Users"].find({"role": {"$in": list(VALID_ROLES)}}).sort("username", ASCENDING).to_list(length=500)
    return {"users": [_public_user(doc) for doc in docs]}


@router.post("/users", dependencies=[Depends(require_roles("lider"))])
async def create_user(payload: UserIn):
    await _ensure_users_collection()
    username = _normalize_username(payload.username)
    email = _normalize_email(payload.email)
    role = _normalize_role(payload.role)
    if not payload.password:
        raise HTTPException(status_code=400, detail="La contraseña es obligatoria para crear usuarios.")
    secret = _hash_password(payload.password)
    try:
        await _db()["Users"].insert_one({
            "username": username,
            "email": email,
            "role": role,
            "active": bool(payload.active),
            "full_name": (payload.full_name or "").strip(),
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
            **secret,
        })
    except Exception as exc:
        if "duplicate" in str(exc).lower():
            raise HTTPException(status_code=409, detail="Ya existe un usuario con ese nombre.")
        raise
    return {"ok": True}


@router.put("/users/{username}", dependencies=[Depends(require_roles("lider"))])
async def update_user(username: str, payload: UserUpdateIn):
    await _ensure_users_collection()
    normalized = _normalize_username(username)
    update: Dict[str, Any] = {"updated_at": datetime.utcnow()}
    if payload.role is not None:
        update["role"] = _normalize_role(payload.role)
    if payload.email is not None:
        update["email"] = _normalize_email(payload.email)
    if payload.active is not None:
        update["active"] = bool(payload.active)
    if payload.full_name is not None:
        update["full_name"] = payload.full_name.strip()
    if payload.password:
        update.update(_hash_password(payload.password))
    result = await _db()["Users"].update_one({"username": normalized}, {"$set": update})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    return {"ok": True}


@router.delete("/users/{username}", dependencies=[Depends(require_roles("lider"))])
async def delete_user(username: str, user: Dict[str, Any] = Depends(current_user)):
    await _ensure_users_collection()
    normalized = _normalize_username(username)
    if normalized == user.get("username"):
        raise HTTPException(status_code=400, detail="No podés eliminar tu propio usuario activo.")
    result = await _db()["Users"].delete_one({"username": normalized})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    for token, session in list(_sessions.items()):
        if session.get("username") == normalized:
            _sessions.pop(token, None)
    return {"ok": True}
