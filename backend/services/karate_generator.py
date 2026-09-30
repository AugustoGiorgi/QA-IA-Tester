from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from pymongo import DESCENDING

from services.activity import record_activity
from services.auth import _db, current_user
from services.files import safe_filename
from services.postman_generator import _read_upload, build_intermediate_model


router = APIRouter(prefix="/api/karate", tags=["karate-generator"])
COLLECTION = "KarateGenerationDrafts"


class DraftUpdate(BaseModel):
    test_cases: Optional[List[Dict[str, Any]]] = None
    associations: Optional[List[Dict[str, Any]]] = None
    variables: Optional[List[Dict[str, Any]]] = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _slug(value: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", (value or "proyecto-api").lower())).strip("-") or "proyecto-api"


def _variable_key(value: str) -> str:
    parts = re.findall(r"[A-Za-z0-9]+", value or "")
    if not parts:
        return "variable"
    return parts[0][:1].lower() + parts[0][1:] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


def _public(doc: Dict[str, Any]) -> Dict[str, Any]:
    public = dict(doc)
    public["id"] = str(public.pop("_id"))
    model = dict(public.get("model") or {})
    model["variables"] = [
        {**variable, "value": ""} if variable.get("sensitive") else variable
        for variable in model.get("variables", [])
    ]
    public["model"] = model
    return public


def _get_role(user: Dict[str, Any]) -> None:
    if user.get("role") != "qa":
        raise HTTPException(status_code=403, detail="Karate esta disponible solo para QA.")


def _path_expression(path: str) -> str:
    pieces: List[str] = []
    cursor = 0
    for match in re.finditer(r"\{([A-Za-z0-9_]+)\}", path or ""):
        literal = path[cursor:match.start()]
        if literal:
            pieces.append(json.dumps(literal))
        pieces.append(_variable_key(match.group(1)))
        cursor = match.end()
    if path[cursor:]:
        pieces.append(json.dumps(path[cursor:]))
    return " + ".join(pieces) or "'/'"


def _feature(model: Dict[str, Any], project_name: str) -> str:
    endpoints = {item.get("id"): item for item in model.get("endpoints", [])}
    cases = {item.get("id"): item for item in model.get("test_cases", [])}
    associations = model.get("associations", [])
    lines = [f"Feature: {project_name}", "  Background:", "    * url baseUrl", ""]
    scenario_count = 0
    for association in associations:
        case = cases.get(association.get("case_id"))
        endpoint = endpoints.get(association.get("endpoint_id"))
        if not case or not endpoint or not association.get("included", True):
            continue
        generated = bool(case.get("generated"))
        expected = str(case.get("expected_status") or "").strip()
        if expected and not re.fullmatch(r"[1-5][0-9]{2}", expected):
            expected = ""
        negative_base = generated and "error" in str(case.get("name", "")).lower()
        if not expected and not negative_base:
            expected_match = re.search(r"(?i)(?:status(?:\s+HTTP)?|HTTP|respuesta|response)[^0-9]{0,12}([1-5][0-9]{2})|(?:esperad[oa]|expected)[^0-9]{0,20}([1-5][0-9]{2})", case.get("expected_result", ""))
            expected = next((value for value in expected_match.groups() if value), "") if expected_match else ""
        if not expected and not negative_base:
            documented = [str(item.get("status", "")) for item in endpoint.get("responses", []) if str(item.get("status", "")).isdigit()]
            preferred = next((code for code in documented if code.startswith("2")), "")
            expected = preferred
        case_body = case.get("request_body")
        if case_body is None:
            case_body = (endpoint.get("body") or {}).get("raw")
        missing_parameters = any(
            not str(variable.get("value") or "").strip() or re.search(r"(?i)todo|completar", str(variable.get("value") or ""))
            for variable in model.get("variables", [])
            if variable.get("key") in {
                _variable_key(str(parameter.get("key") or ""))
                for parameter in endpoint.get("path_params", []) + endpoint.get("query_params", [])
            }
        )
        pending = (
            not expected
            or bool(re.search(r"(?i)todo|completar", str(case_body or "")))
            or missing_parameters
            or negative_base and not case.get("request_body")
        )
        if pending:
            lines.append("  @ignore @pendiente_qa")
        scenario_name = re.sub(r"[\r\n]+", " ", f"{case.get('case_id', case.get('name', 'Caso'))} - {case.get('name', 'Caso API')}")
        lines.append(f"  Scenario: {scenario_name}")
        lines.extend([
            f"    # Estado: {'completar expectativa/datos antes de ejecutar' if pending else 'expectativa documentada o confirmada'}",
            f"    Given url baseUrl + {_path_expression(str(endpoint.get('path') or '/'))}",
        ])
        for header in endpoint.get("headers", []):
            key = str(header.get("key") or "")
            if key and key.lower() not in {"authorization", "content-type"}:
                value = str(header.get("value") or "")
                variable = re.fullmatch(r"\{\{([A-Za-z0-9_.-]+)\}\}", value)
                expression = _variable_key(variable.group(1)) if variable else json.dumps(value)
                lines.append(f"    And header {key} = {expression}")
        if any(str(item.get("key", "")).lower() == "authorization" for item in endpoint.get("headers", [])):
            lines.append("    And header Authorization = 'Bearer ' + authToken")
        if case_body:
            content_type = (endpoint.get("body") or {}).get("content_type") or endpoint.get("content_type") or "application/json"
            lines.append(f"    And header Content-Type = {json.dumps(content_type)}")
        for parameter in endpoint.get("query_params", []):
            key = str(parameter.get("key") or "")
            if key:
                value = str(parameter.get("value") or "")
                variable = re.fullmatch(r"\{\{([A-Za-z0-9_.-]+)\}\}", value)
                expression = _variable_key(variable.group(1)) if variable else json.dumps(value)
                lines.append(f"    And param {key} = {expression}")
        body = case_body
        if body and str(body).strip() not in {"", "{}"}:
            try:
                body = json.loads(body) if isinstance(body, str) else body
                lines.append("    And request " + json.dumps(body, ensure_ascii=False, separators=(",", ":")))
            except (TypeError, ValueError):
                lines.append("    # Pendiente QA: completar el body desde el formulario de revision.")
        lines.extend([
            f"    When method {str(endpoint.get('method') or 'GET').lower()}",
            f"    Then status {expected}" if expected else "    # Pendiente QA: indicar el status esperado.",
        ])
        expected_response = case.get("expected_response")
        if expected_response:
            try:
                parsed_expected = json.loads(expected_response) if isinstance(expected_response, str) else expected_response
                lines.append("    And match response contains " + json.dumps(parsed_expected, ensure_ascii=False, separators=(",", ":")))
            except (TypeError, ValueError):
                lines.append("    # Pendiente QA: respuesta esperada no es JSON valido.")
        lines.append("")
        scenario_count += 1
    if not scenario_count:
        lines.extend(["  @ignore @pendiente_qa", "  Scenario: Sin casos asociados", "    # Asociar casos con endpoints antes de generar el proyecto.", ""])
    return "\n".join(lines)


def _project_zip(doc: Dict[str, Any]) -> bytes:
    model = doc.get("model") or {}
    project = str(doc.get("project_name") or "Proyecto API")
    slug = _slug(project)
    config_values = {item.get("key"): ("" if item.get("sensitive") else item.get("value", "")) for item in model.get("variables", []) if item.get("key")}
    config_values.setdefault("baseUrl", "https://completar-ambiente")
    config_values.setdefault("authToken", "COMPLETAR_TOKEN")
    for endpoint in model.get("endpoints", []):
        path = str(endpoint.get("path") or "")
        for key in re.findall(r"\{([A-Za-z0-9_.-]+)\}", path):
            config_values.setdefault(_variable_key(key), "TODO_COMPLETAR")
        for parameter in endpoint.get("path_params", []) + endpoint.get("query_params", []):
            key = str(parameter.get("key") or "")
            if key:
                config_values.setdefault(_variable_key(key), parameter.get("value") or "TODO_COMPLETAR")
    config_lines = [
        "function fn() {",
        "  var config = " + json.dumps(config_values, ensure_ascii=False, indent=2) + ";",
        "  config.baseUrl = karate.properties['baseUrl'] || java.lang.System.getenv('baseUrl') || config.baseUrl;",
    ]
    for variable in model.get("variables", []):
        key = str(variable.get("key") or "")
        if variable.get("sensitive") and key:
            quoted = json.dumps(key)
            config_lines.append(f"  config[{quoted}] = karate.properties[{quoted}] || java.lang.System.getenv({quoted}) || config[{quoted}];")
    config_lines.extend(["  return config;", "}"])
    config_js = "\n".join(config_lines) + "\n"
    version = "1.5.2"
    pom = f"""<project xmlns="http://maven.apache.org/POM/4.0.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>qa.generated</groupId><artifactId>{slug}</artifactId><version>1.0.0</version>
  <properties><maven.compiler.source>11</maven.compiler.source><maven.compiler.target>11</maven.compiler.target><project.build.sourceEncoding>UTF-8</project.build.sourceEncoding><karate.version>{version}</karate.version><junit.version>5.10.2</junit.version></properties>
  <dependencies>
    <dependency><groupId>com.intuit.karate</groupId><artifactId>karate-junit5</artifactId><version>${{karate.version}}</version><scope>test</scope></dependency>
    <dependency><groupId>org.junit.jupiter</groupId><artifactId>junit-jupiter</artifactId><version>${{junit.version}}</version><scope>test</scope></dependency>
  </dependencies>
  <build><testResources><testResource><directory>src/test/java</directory><includes><include>**/*.feature</include><include>**/*.js</include></includes></testResource></testResources>
    <plugins><plugin><groupId>org.apache.maven.plugins</groupId><artifactId>maven-surefire-plugin</artifactId><version>3.2.5</version><configuration><includes><include>**/*Runner.java</include></includes></configuration></plugin></plugins>
  </build>
</project>
"""
    runner = """package qa.generated;

import com.intuit.karate.junit5.Karate;
import org.junit.jupiter.api.Test;

class ApiRunner {
    @Test
    Karate testApi() {
        return Karate.run("classpath:features");
    }
}
"""
    readme = """# Automatizacion API con Karate

## Preparacion (una sola vez)
1. Instalar Java 11 o superior y Maven.
2. Abrir una terminal en esta carpeta y ejecutar `mvn test`.
3. Completar el ambiente y credenciales por propiedades, sin guardarlas en Git:
   - Windows PowerShell: `$env:baseUrl='https://...'; $env:authToken='...'; mvn test`
   - macOS/Linux: `baseUrl=https://... authToken=... mvn test`

## Antes de tomar el resultado como valido
Los escenarios `@ignore @pendiente_qa` necesitan expectativa o datos que no estaban confirmados en las fuentes. Edita los valores en `karate-config.js` o revisa la asociacion de casos en la app y vuelve a descargar. La generacion no ejecuta requests ni valida el ambiente. Nunca agregues secretos al proyecto.

## Archivos
- `src/test/java/features/api.feature`: escenarios Gherkin de API.
- `src/test/java/qa/generated/ApiRunner.java`: runner JUnit 5.
- `src/test/java/karate-config.js`: valores de ambiente.
- `pom.xml`: proyecto Maven con Karate.
"""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("pom.xml", pom)
        archive.writestr("src/test/java/features/api.feature", _feature(model, project))
        archive.writestr("src/test/java/qa/generated/ApiRunner.java", runner)
        archive.writestr("src/test/java/karate-config.js", config_js)
        archive.writestr("README.md", readme)
    output.seek(0)
    return output.getvalue()


async def _load_draft(draft_id: str, user: Dict[str, Any]) -> Dict[str, Any]:
    if not ObjectId.is_valid(draft_id):
        raise HTTPException(status_code=400, detail="ID invalido.")
    doc = await _db()[COLLECTION].find_one({"_id": ObjectId(draft_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado.")
    if not user.get("shared") and doc.get("created_by") != user.get("username"):
        raise HTTPException(status_code=403, detail="No podes acceder a este proyecto.")
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
    sources = [await _read_upload(file) for file in (files or [])]
    if cases_file and cases_file.filename:
        source = await _read_upload(cases_file)
        source["is_cases_file"] = True
        sources.append(source)
    if not sources and not manual_text.strip():
        raise HTTPException(status_code=400, detail="Carga documentos de API, casos o texto de referencia.")
    model = build_intermediate_model(sources, manual_text)
    variables = model.get("variables", [])
    existing_keys = {item.get("key") for item in variables}
    if "baseUrl" not in existing_keys:
        variables.append({"key": "baseUrl", "value": "", "scope": "environment", "sensitive": False, "reason": "URL del ambiente"})
    if "authToken" not in existing_keys:
        variables.append({"key": "authToken", "value": "", "scope": "environment", "sensitive": True, "reason": "Token de autenticacion; se lee del entorno local"})
    model["variables"] = variables
    model["validation"] = {"valid": bool(model.get("endpoints")), "warnings": [item.get("message", "") for item in model.get("warnings", [])]}
    name = (project_name or "Proyecto API").strip()[:180]
    now = _now()
    doc = {"project_name": name, "model": model, "created_by": user.get("username", "shared"), "created_at": now, "updated_at": now}
    inserted = await _db()[COLLECTION].insert_one(doc)
    doc["_id"] = inserted.inserted_id
    await record_activity(user, "Generacion Karate", "karate", f"Genero proyecto API Karate: {name}", {"draft_id": str(inserted.inserted_id), "endpoints": len(model.get("endpoints", [])), "casos": len(model.get("test_cases", []))})
    return {"draft": _public(doc)}


@router.put("/drafts/{draft_id}")
async def update_karate(draft_id: str, payload: DraftUpdate, user: Dict[str, Any] = Depends(current_user)):
    _get_role(user)
    doc = await _load_draft(draft_id, user)
    model = dict(doc.get("model") or {})
    model.update(payload.model_dump(exclude_none=True))
    await _db()[COLLECTION].update_one({"_id": doc["_id"]}, {"$set": {"model": model, "updated_at": _now()}})
    doc["model"] = model
    doc["updated_at"] = _now()
    await record_activity(user, "Revision Karate", "karate", f"Reviso proyecto API Karate: {doc.get('project_name')}", {"draft_id": draft_id})
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


@router.get("/drafts/{draft_id}/download")
async def download_karate(draft_id: str, user: Dict[str, Any] = Depends(current_user)):
    _get_role(user)
    doc = await _load_draft(draft_id, user)
    await record_activity(user, "Descarga Karate", "karate", f"Descargo proyecto Karate: {doc.get('project_name')}", {"draft_id": draft_id})
    filename = _slug(doc.get("project_name") or "proyecto-api-karate") + ".zip"
    return StreamingResponse(io.BytesIO(_project_zip(doc)), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{safe_filename(filename)}"'})
