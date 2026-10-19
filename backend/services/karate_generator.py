from __future__ import annotations

import io
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from pymongo import DESCENDING
from starlette.concurrency import run_in_threadpool

from services.activity import record_activity
from services.auth import _db, current_user
from services.files import safe_filename
from services.karate_project import build_karate_project
from services.postman_generator import (
    MAX_SOURCE_FILES, MAX_TOTAL_SOURCE_BYTES, MAX_TOTAL_SOURCE_TEXT_CHARS,
    _read_upload, build_intermediate_model, validate_model,
)


router = APIRouter(prefix="/api/karate", tags=["karate-generator"])
COLLECTION = "KarateGenerationDrafts"


class DraftUpdate(BaseModel):
    test_cases: Optional[List[Dict[str, Any]]] = None
    associations: Optional[List[Dict[str, Any]]] = None
    variables: Optional[List[Dict[str, Any]]] = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _slug(value: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", (value or "karate-api").lower())).strip("-") or "karate-api"


def _public(doc: Dict[str, Any]) -> Dict[str, Any]:
    public = dict(doc)
    public["id"] = str(public.pop("_id"))
    model = dict(public.get("model") or {})
    model["variables"] = [{**variable, "value": ""} if variable.get("sensitive") else variable
                          for variable in model.get("variables", [])]
    public["model"] = model
    return public


def _get_role(user: Dict[str, Any]) -> None:
    if user.get("role") != "qa":
        raise HTTPException(status_code=403, detail="Karate está disponible solo para QA.")


async def _load_draft(draft_id: str, user: Dict[str, Any]) -> Dict[str, Any]:
    if not ObjectId.is_valid(draft_id):
        raise HTTPException(status_code=400, detail="ID inválido.")
    doc = await _db()[COLLECTION].find_one({"_id": ObjectId(draft_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado.")
    if not user.get("shared") and doc.get("created_by") != user.get("username"):
        raise HTTPException(status_code=403, detail="No podés acceder a este proyecto.")
    return doc


@router.post("/generate")
async def generate_karate(
    files: Optional[List[UploadFile]] = File(default=None),
    cases_file: Optional[UploadFile] = File(default=None),
    manual_text: str = Form(default=""),
    project_name: str = Form(default=""),
    user: Dict[str, Any] = Depends(current_user),
):
    _get_role(user)
    if len(manual_text) > MAX_TOTAL_SOURCE_TEXT_CHARS:
        raise HTTPException(status_code=413, detail="El texto de comentarios es demasiado grande.")
    if len(files or []) + bool(cases_file and cases_file.filename) > MAX_SOURCE_FILES:
        raise HTTPException(status_code=413, detail=f"Cargá como máximo {MAX_SOURCE_FILES} archivos.")
    sources = []
    total_bytes = 0
    total_chars = len(manual_text)
    for upload in [*(files or []), *([cases_file] if cases_file and cases_file.filename else [])]:
        source = await _read_upload(upload)
        if source.get("warning") or not source.get("text", "").strip():
            raise HTTPException(status_code=422, detail=f"No pude leer el archivo {source['name']}.")
        total_bytes += source["size"]
        total_chars += len(source["text"])
        if total_bytes > MAX_TOTAL_SOURCE_BYTES:
            raise HTTPException(status_code=413, detail="Los archivos superan el límite de 40 MB.")
        if total_chars > MAX_TOTAL_SOURCE_TEXT_CHARS:
            raise HTTPException(status_code=413, detail="Los archivos contienen demasiado texto para analizarlo completo.")
        if upload is cases_file or source.get("extension") in {".xlsx", ".csv"}:
            source["is_cases_file"] = True
        sources.append(source)
    if not sources:
        raise HTTPException(status_code=400, detail="Cargá una collection o documentos con endpoints.")
    model = await run_in_threadpool(build_intermediate_model, sources, manual_text)
    if not model.get("endpoints"):
        raise HTTPException(status_code=422, detail="No encontré endpoints. Cargá una collection, request JSON, OpenAPI o cURL válido.")
    case_sources = [source for source in sources if source.get("is_cases_file")]
    if len(case_sources) > 1:
        raise HTTPException(status_code=422, detail="Cargá un solo Excel o CSV de casos por proyecto.")
    case_source = case_sources[0] if case_sources else None
    if case_source and not model.get("test_cases"):
        raise HTTPException(status_code=422, detail=f"No encontré casos en {case_source['name']}. Revisá encabezados e IDs del Excel/CSV.")
    if case_source:
        case_by_id = {case["id"]: case for case in model["test_cases"]}
        unmatched = [case_by_id[item["case_id"]].get("name", item["case_id"])
                     for item in model["associations"]
                     if (not item.get("endpoint_id") or not item.get("confirmed")) and item.get("case_id") in case_by_id]
        if unmatched:
            raise HTTPException(status_code=422, detail="No pude asociar estos casos a endpoints: " + "; ".join(unmatched[:10]) + ". Agregá método y ruta en la columna Endpoint.")
    name = project_name.strip()[:180] or "Proyecto API"
    model["project_name"] = name
    model["validation"] = await run_in_threadpool(validate_model, model)
    if not model["validation"]["valid"]:
        raise HTTPException(status_code=422, detail="Las fuentes no son válidas: " + "; ".join(model["validation"]["errors"][:5]))
    try:
        _, model["readiness"] = await run_in_threadpool(build_karate_project, model, name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    now = _now()
    doc = {"project_name": name, "model": model, "created_by": user.get("username", "shared"),
           "created_at": now, "updated_at": now}
    result = await _db()[COLLECTION].insert_one(doc)
    doc["_id"] = result.inserted_id
    await record_activity(user, "Generación Karate", "karate", f"Generó proyecto Karate: {name}",
                          {"draft_id": str(result.inserted_id), "endpoints": len(model["endpoints"]),
                           "casos": model["readiness"]["scenario_count"]})
    return {"draft": _public(doc)}


@router.get("/drafts")
async def list_karate(user: Dict[str, Any] = Depends(current_user)):
    _get_role(user)
    query = {} if user.get("shared") else {"created_by": user.get("username")}
    docs = await _db()[COLLECTION].find(query).sort("created_at", DESCENDING).to_list(100)
    return {"drafts": [_public(doc) for doc in docs]}


@router.get("/drafts/{draft_id}")
async def get_karate(draft_id: str, user: Dict[str, Any] = Depends(current_user)):
    _get_role(user)
    return {"draft": _public(await _load_draft(draft_id, user))}


@router.put("/drafts/{draft_id}")
async def update_karate(draft_id: str, payload: DraftUpdate, user: Dict[str, Any] = Depends(current_user)):
    _get_role(user)
    doc = await _load_draft(draft_id, user)
    model = dict(doc.get("model") or {})
    model.update(payload.model_dump(exclude_none=True))
    _, model["readiness"] = await run_in_threadpool(build_karate_project, model, doc.get("project_name") or "Proyecto API")
    await _db()[COLLECTION].update_one({"_id": doc["_id"]}, {"$set": {"model": model, "updated_at": _now()}})
    doc["model"] = model
    doc["updated_at"] = _now()
    await record_activity(user, "Revisión Karate", "karate", f"Revisó proyecto Karate: {doc.get('project_name')}", {"draft_id": draft_id})
    return {"draft": _public(doc)}


@router.get("/drafts/{draft_id}/download")
async def download_karate(draft_id: str, user: Dict[str, Any] = Depends(current_user)):
    _get_role(user)
    doc = await _load_draft(draft_id, user)
    try:
        payload, _ = await run_in_threadpool(build_karate_project, doc.get("model") or {}, doc.get("project_name") or "Proyecto API")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await record_activity(user, "Descarga Karate", "karate", f"Descargó proyecto Karate: {doc.get('project_name')}", {"draft_id": draft_id})
    filename = _slug(doc.get("project_name") or "karate-api") + ".zip"
    return StreamingResponse(io.BytesIO(payload), media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{safe_filename(filename)}"'})
