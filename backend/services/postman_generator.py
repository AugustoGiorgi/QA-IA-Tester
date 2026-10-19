from __future__ import annotations

import csv
import json
import re
import shlex
import tempfile
import unicodedata
import zipfile
from copy import deepcopy
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, quote, urlparse

import yaml
from bson import ObjectId
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse
from pydantic import BaseModel
from pymongo import DESCENDING
from starlette.concurrency import run_in_threadpool

from services.activity import record_activity
from services.auth import _db, current_user
from services.files import safe_filename
from services.parsing import docx_to_text


router = APIRouter(prefix="/api/postman", tags=["postman-generator"])
COLLECTION = "PostmanGenerationDrafts"
OUTPUT_NAME = "qa_postman_package"
POSTMAN_SCHEMA = "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
TEXT_EXTENSIONS = {".txt", ".md", ".json", ".yaml", ".yml", ".xml", ".csv", ".curl", ".http"}
MAX_SOURCE_BYTES = 20 * 1024 * 1024
MAX_SOURCE_TEXT_CHARS = 2_000_000
MAX_SPREADSHEET_ROWS = 10_000
MAX_SOURCE_FILES = 12
MAX_TOTAL_SOURCE_BYTES = 40 * 1024 * 1024
MAX_TOTAL_SOURCE_TEXT_CHARS = 4_000_000
SECRET_RE = re.compile(
    r"(?i)(bearer\s+[a-z0-9._\-]{12,}|api[_-]?key\s*[:=]\s*[^\s,;]{8,}|"
    r"password\s*[:=]\s*[^\s,;]{4,}|secret\s*[:=]\s*[^\s,;]{6,}|"
    r"token\s*[:=]\s*[^\s,;]{8,}|-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----)"
)


class DraftUpdate(BaseModel):
    endpoints: Optional[List[Dict[str, Any]]] = None
    test_cases: Optional[List[Dict[str, Any]]] = None
    associations: Optional[List[Dict[str, Any]]] = None
    variables: Optional[List[Dict[str, Any]]] = None
    warnings: Optional[List[Dict[str, Any]]] = None
    conflicts: Optional[List[Dict[str, Any]]] = None
    folders: Optional[List[str]] = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _oid(value: str) -> ObjectId:
    if not ObjectId.is_valid(value):
        raise HTTPException(status_code=400, detail="ID invalido.")
    return ObjectId(value)


def _clean(value: Any, max_len: int = 12000) -> str:
    return str(value or "").replace("\x00", "").strip()[:max_len]


def _safe_key(value: str, fallback: str = "variable") -> str:
    parts = re.findall(r"[A-Za-z0-9]+", value or "")
    if not parts:
        return fallback
    first, *rest = parts
    return first[:1].lower() + first[1:] + "".join(part[:1].upper() + part[1:] for part in rest)


def _source_ref(name: str, kind: str, location: str = "") -> Dict[str, str]:
    return {"source": name, "kind": kind, "location": location}


def _case_sheet_lines(rows: List[List[str]], seen_case_ids: set) -> List[str]:
    rows = [row for row in rows if any(row)]
    if not rows:
        return []
    known_headers = {
        "id y escenario", "id caso", "identificador", "id", "tc", "cp", "caso", "caso de prueba",
        "test case", "escenario", "nombre", "nombre del caso", "scenario", "test name", "titulo",
        "título", "descripcion", "descripción", "endpoint sugerido", "endpoint", "request", "url",
        "servicio", "resultado esperado", "expected result", "criterio final", "precondiciones",
        "precondicion", "precondición", "pasos", "steps", "datos de prueba", "categoria", "categoría",
    }
    header_row_index = next((index for index, row in enumerate(rows[:5])
                             if len({re.sub(r"\s+", " ", value.lower()) for value in row if value} & known_headers) >= 2), None)
    if header_row_index is None:
        return []
    headers = [re.sub(r"\s+", " ", value.strip().lower()) for value in rows[header_row_index]]
    columns = {header: index for index, header in enumerate(headers) if header}

    def cell(values: List[str], *keys: str) -> str:
        index = next((columns[key] for key in keys if key in columns), None)
        return values[index] if index is not None and index < len(values) else ""

    lines: List[str] = []
    for row_index, values in enumerate(rows[header_row_index + 1:], start=1):
        if not any(values):
            continue
        identifier = cell(values, "id y escenario", "id caso", "identificador", "id", "tc", "cp", "caso", "test case")
        if not identifier:
            identifier = next((value for value in values if re.match(r"(?i)^(?:CP|TC|CASE)[\s#:_-]*\d+", value)), "")
        case_id_match = re.match(r"(?i)^(?:CP|TC|CASE)[\s#:_-]*\d+", identifier)
        if case_id_match:
            case_key = re.sub(r"[^A-Z0-9]", "", case_id_match.group(0).upper())
            if case_key in seen_case_ids:
                continue
            seen_case_ids.add(case_key)
        scenario = cell(values, "escenario", "nombre", "nombre del caso", "scenario", "test name", "titulo", "título", "descripcion", "descripción", "caso de prueba")
        if not scenario and "id y escenario" not in columns:
            scenario = next((value for value in values if value and value != identifier and not re.match(r"(?i)^(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s", value)), "")
        identifier = re.sub(r"(?i)^caso\s+", "", identifier).strip()
        scenario = re.sub(rf"(?i)^\s*{re.escape(identifier)}\s*(?:[-:|]|\s)\s*", "", scenario).strip() if identifier else scenario
        case_title = " - ".join(part for part in (identifier, scenario) if part) or f"Caso {row_index}"
        endpoint = cell(values, "endpoint sugerido", "endpoint", "request", "url", "servicio")
        if not endpoint:
            endpoint = next((value for value in values if re.match(r"(?i)^(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+\S+", value)), "")
        category = cell(values, "categoria", "categoría", "tipo")
        context = cell(values, "contexto inicial", "precondiciones", "precondicion", "precondición")
        inputs = cell(values, "valores de entrada", "datos de prueba", "datos", "entrada")
        action = cell(values, "accion", "acción", "pasos", "steps")
        expected = cell(values, "criterio final", "resultado esperado", "expected result", "expected")
        used_values = {identifier, scenario, endpoint, category, context, inputs, action, expected}
        extra = [f"{headers[index]}: {value}" for index, value in enumerate(values)
                 if value and index < len(headers) and value not in used_values]
        lines.extend([
            f"CASO {case_title}", f"Endpoint: {endpoint}", f"Categoria: {category}",
            f"Precondiciones: {context}", f"Datos de prueba: {inputs}", f"Pasos: {action}",
            f"Resultado esperado: {expected}", *extra, "",
        ])
    return lines


async def _read_upload(file: UploadFile) -> Dict[str, Any]:
    filename = safe_filename(file.filename or "fuente.txt")
    raw = await file.read(MAX_SOURCE_BYTES + 1)
    if len(raw) > MAX_SOURCE_BYTES:
        raise HTTPException(status_code=413, detail=f"El archivo {filename} supera el límite de 20 MB.")
    return await run_in_threadpool(_decode_upload, filename, raw)


def _decode_upload(filename: str, raw: bytes) -> Dict[str, Any]:
    suffix = Path(filename).suffix.lower()
    text = ""
    parse_warning = ""
    try:
        if suffix == ".docx":
            tmp_dir = Path(__file__).resolve().parent.parent / "data"
            tmp_dir.mkdir(exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="wb", dir=tmp_dir, prefix="postman_", suffix=".docx", delete=False) as temporary:
                temporary.write(raw)
                tmp = Path(temporary.name)
            try:
                text = docx_to_text(str(tmp))
            finally:
                tmp.unlink(missing_ok=True)
        elif suffix == ".xlsx":
            workbook = None
            try:
                from openpyxl import load_workbook

                workbook = load_workbook(BytesIO(raw), read_only=True, data_only=True)
                lines: List[str] = []
                seen_case_ids = set()
                total_rows = 0
                for sheet in workbook.worksheets:
                    rows = []
                    for row in sheet.iter_rows(values_only=True):
                        total_rows += 1
                        if total_rows > MAX_SPREADSHEET_ROWS:
                            raise HTTPException(status_code=413, detail=f"El Excel {filename} supera las {MAX_SPREADSHEET_ROWS} filas.")
                        values = [str(value).strip() if value is not None else "" for value in row]
                        if any(values):
                            rows.append(values)
                    lines.extend(_case_sheet_lines(rows, seen_case_ids))
                text = "\n".join(lines)
            except HTTPException:
                raise
            except Exception:
                parse_warning = "No se pudo leer el Excel. Verifica que sea .xlsx valido."
            finally:
                if workbook is not None:
                    workbook.close()
        elif suffix == ".csv":
            try:
                try:
                    decoded = raw.decode("utf-8-sig")
                except UnicodeDecodeError:
                    decoded = raw.decode("cp1252")
                sample = decoded[:4096]
                try:
                    dialect = csv.Sniffer().sniff(sample, delimiters=",;\t") if sample else csv.excel
                except csv.Error:
                    dialect = csv.excel
                reader = csv.reader(decoded.splitlines(), dialect)
                rows = []
                for row in reader:
                    if len(rows) >= MAX_SPREADSHEET_ROWS:
                        raise HTTPException(status_code=413, detail=f"El CSV {filename} supera las {MAX_SPREADSHEET_ROWS} filas.")
                    rows.append([value.strip() for value in row])
                text = "\n".join(_case_sheet_lines(rows, set())) or decoded
            except HTTPException:
                raise
            except Exception:
                parse_warning = "No se pudo leer el CSV de casos. Verificá el formato y la codificación."
        elif suffix == ".pdf":
            try:
                from pypdf import PdfReader

                reader = PdfReader(BytesIO(raw))
                text = "\n".join(page.extract_text() or "" for page in reader.pages)
            except Exception:
                parse_warning = "No se pudo extraer texto del PDF. Si es escaneado, converti a texto o DOCX."
        elif suffix in TEXT_EXTENSIONS or not suffix:
            text = raw.decode("utf-8-sig", errors="replace")
        else:
            parse_warning = f"Formato {suffix} recibido; se conservara como fuente pero no se pudo leer como texto."
    except HTTPException:
        raise
    except Exception as exc:
        parse_warning = f"No se pudo leer {filename}: {type(exc).__name__}."
    if len(text) > MAX_SOURCE_TEXT_CHARS:
        raise HTTPException(status_code=413, detail=f"El contenido de {filename} es demasiado grande para analizarlo completo.")
    return {
        "name": filename,
        "extension": suffix,
        "text": _clean(text, MAX_SOURCE_TEXT_CHARS),
        "size": len(raw),
        "warning": parse_warning,
    }


def _try_load_structured(text: str) -> Optional[Any]:
    clean = (text or "").strip()
    if not clean or not re.match(r"(?i)^(?:\{|\[|---|openapi\s*:|swagger\s*:|paths\s*:|info\s*:)", clean):
        return None
    for loader in (json.loads, yaml.safe_load):
        try:
            data = loader(clean)
        except Exception:
            continue
        if isinstance(data, (dict, list)):
            return data
    return None


def _is_postman_collection(data: Any) -> bool:
    if not isinstance(data, dict) or not isinstance(data.get("item"), list):
        return False
    info = data.get("info") or {}
    schema = str(info.get("schema") or "") if isinstance(info, dict) else ""
    return "collection/" in schema or bool(info)


def _classify_source(source: Dict[str, Any]) -> List[str]:
    text = source.get("text", "")
    normalized = text.lower()
    data = _try_load_structured(text)
    kinds: List[str] = []
    if isinstance(data, dict) and ("openapi" in data or "swagger" in data or "paths" in data):
        kinds.append("api_definition")
    if _is_postman_collection(data):
        kinds.append("postman_collection")
    if isinstance(data, dict) and (isinstance(data.get("request"), dict) or (data.get("method") and data.get("url"))):
        kinds.append("postman_request")
    if "curl " in normalized:
        kinds.append("curl")
    if re.search(r"\b(get|post|put|patch|delete|head|options)\s+https?://", normalized):
        kinds.append("api_examples")
    if re.search(r"\b(caso|test case|prueba|expected|resultado esperado|precondicion)", normalized):
        kinds.append("test_cases")
    if re.search(r"\{\{[a-zA-Z0-9_.-]+\}\}|environment|base url|variable", normalized):
        kinds.append("variables")
    if SECRET_RE.search(text):
        kinds.append("sensitive_data")
    if not kinds:
        kinds.append("complementary_documentation")
    return list(dict.fromkeys(kinds))


def _infer_content_type(headers: List[Dict[str, str]], body: Any) -> str:
    for header in headers:
        if header.get("key", "").lower() == "content-type":
            return header.get("value", "")
    if isinstance(body, (dict, list)):
        return "application/json"
    if isinstance(body, str) and body.strip().startswith("<"):
        return "application/xml"
    if body not in (None, "", {}, []):
        return "text/plain"
    return ""


def _endpoint_id(method: str, path: str, index: int) -> str:
    return f"req_{method.lower()}_{_safe_key(path, 'path')}_{index}"


def _normalized_endpoint_key(endpoint: Dict[str, Any]) -> Tuple[str, str, str]:
    method = str(endpoint.get("method") or "GET").upper()
    base = str(endpoint.get("base_url") or "").lower().rstrip("/")
    path = str(endpoint.get("path") or endpoint.get("name") or "").split("?")[0].strip()
    path = re.sub(r"https?://[^/]+", "", path)
    path = re.sub(r"\{\{[^}]+\}\}", "{var}", path)
    path = re.sub(r":([A-Za-z0-9_]+)", "{var}", path)
    path = re.sub(r"\{[^}]+\}", "{var}", path)
    path = re.sub(r"/+", "/", path).rstrip("/") or "/"
    return method, base, path.lower()


def _dedupe_endpoints(endpoints: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    unique: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for endpoint in endpoints:
        key = _normalized_endpoint_key(endpoint)
        existing = unique.get(key)
        if not existing:
            unique[key] = endpoint
            continue
        if any(existing.get(field) != endpoint.get(field) for field in ("body", "headers", "query_params", "auth")):
            existing.setdefault("variants", []).append(endpoint)
        existing_refs = existing.setdefault("source_refs", [])
        seen_refs = {(ref.get("source"), ref.get("kind"), ref.get("location")) for ref in existing_refs}
        for ref in endpoint.get("source_refs", []):
            ref_key = (ref.get("source"), ref.get("kind"), ref.get("location"))
            if ref_key not in seen_refs:
                existing_refs.append(ref)
                seen_refs.add(ref_key)
        existing_responses = existing.setdefault("responses", [])
        seen_status = {str(resp.get("status")) for resp in existing_responses}
        for response in endpoint.get("responses", []):
            status = str(response.get("status"))
            if status not in seen_status:
                existing_responses.append(response)
                seen_status.add(status)
    return list(unique.values())


def _openapi_endpoints(data: Dict[str, Any], source: Dict[str, Any]) -> List[Dict[str, Any]]:
    servers = data.get("servers") or []
    base_url = ""
    if servers and isinstance(servers, list):
        base_url = str((servers[0] or {}).get("url") or "")
    endpoints: List[Dict[str, Any]] = []
    for path, path_item in (data.get("paths") or {}).items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            method_upper = str(method).upper()
            if method_upper not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            headers: List[Dict[str, str]] = []
            query: List[Dict[str, str]] = []
            path_params: List[Dict[str, str]] = []
            for param in operation.get("parameters") or []:
                if not isinstance(param, dict):
                    continue
                item = {
                    "key": str(param.get("name") or ""),
                    "value": "",
                    "description": str(param.get("description") or ""),
                    "required": bool(param.get("required")),
                }
                if param.get("in") == "header":
                    headers.append(item)
                elif param.get("in") == "query":
                    query.append(item)
                elif param.get("in") == "path":
                    path_params.append(item)
            body: Dict[str, Any] = {"mode": "none"}
            request_body = operation.get("requestBody") or {}
            content = request_body.get("content") or {}
            if content:
                content_type, content_data = next(iter(content.items()))
                example = content_data.get("example")
                if example is None:
                    examples = content_data.get("examples") or {}
                    if examples:
                        example = next(iter(examples.values())).get("value")
                if example is None and "schema" in content_data:
                    example = {"TODO": "Completar body segun schema"}
                body = {
                    "mode": "raw",
                    "content_type": content_type,
                    "raw": json.dumps(example, ensure_ascii=False, indent=2) if not isinstance(example, str) else example,
                }
            responses = []
            for status, response in (operation.get("responses") or {}).items():
                responses.append({
                    "status": str(status),
                    "description": str((response or {}).get("description") or ""),
                    "source": source["name"],
                })
            auth = {"type": "inherit"}
            security = operation.get("security", data.get("security"))
            if security == []:
                auth = {"type": "noauth"}
            endpoint = {
                "id": _endpoint_id(method_upper, path, len(endpoints) + 1),
                "name": operation.get("summary") or operation.get("operationId") or f"{method_upper} {path}",
                "description": operation.get("description") or "",
                "method": method_upper,
                "protocol": "https" if str(base_url).startswith("https") else "",
                "base_url": base_url,
                "path": str(path),
                "path_params": path_params,
                "query_params": query,
                "headers": headers,
                "cookies": [],
                "auth": auth,
                "body": body,
                "content_type": body.get("content_type") or _infer_content_type(headers, body.get("raw")),
                "responses": responses,
                "variables": [],
                "dependencies": [],
                "status": "active",
                "source_refs": [_source_ref(source["name"], "openapi", f"paths.{path}.{method}")],
            }
            if method_upper == "GET" and body.get("mode") != "none":
                endpoint.setdefault("warnings", []).append("GET con body definido explicitamente por la documentacion.")
            endpoints.append(endpoint)
    return endpoints


def _postman_items(
    items: List[Dict[str, Any]], source: Dict[str, Any], folder: str = "",
    inherited_auth: Optional[Dict[str, Any]] = None, inherited_events: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    endpoints: List[Dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        if "item" in item:
            events = (inherited_events or []) + [event for event in item.get("event") or [] if isinstance(event, dict) and event.get("listen") == "prerequest"]
            endpoints.extend(_postman_items(item.get("item") or [], source, item.get("name") or folder, item.get("auth") or inherited_auth, events))
            continue
        request = item.get("request") or {}
        if isinstance(request, str):
            continue
        url = request.get("url") or {}
        raw_url = url.get("raw") if isinstance(url, dict) else str(url)
        raw_url = str(raw_url or "")
        # Mask Postman variables while parsing so braces in {{baseUrl}} survive.
        placeholders: Dict[str, str] = {}
        def mask_placeholder(match: re.Match[str]) -> str:
            token = f"POSTMANVAR{len(placeholders)}"
            placeholders[token] = match.group(0)
            return token
        masked_url = re.sub(r"\{\{[A-Za-z0-9_.-]+\}\}", mask_placeholder, raw_url)
        parsed = urlparse(masked_url)
        restore = lambda value: re.sub(r"POSTMANVAR\d+", lambda match: placeholders.get(match.group(0), match.group(0)), value)
        base_url = restore(f"{parsed.scheme}://{parsed.netloc}") if parsed.scheme and parsed.netloc else ""
        path = restore(parsed.path or "")
        variable_prefix = re.match(r"^(\{\{[A-Za-z0-9_.-]+\}\})(/.*)?$", raw_url)
        if not base_url and variable_prefix:
            base_url = variable_prefix.group(1)
            path = variable_prefix.group(2) or "/"
        if not base_url and isinstance(url, dict) and url.get("host"):
            host_parts = url.get("host") or []
            host = ".".join(host_parts) if isinstance(host_parts, list) else str(host_parts)
            protocol = url.get("protocol") or "https"
            base_url = f"{protocol}://{host}" if host else ""
            path_parts = url.get("path") or []
            path = "/" + "/".join(str(part) for part in path_parts) if isinstance(path_parts, list) else "/" + str(path_parts).lstrip("/")
        path = re.sub(r":([A-Za-z_][A-Za-z0-9_]*)", r"{\1}", path)
        headers = [
            {"key": h.get("key", ""), "value": h.get("value", ""), "description": h.get("description", "")}
            for h in request.get("header") or []
            if isinstance(h, dict)
        ]
        if isinstance(url, dict) and url.get("query"):
            query = [
                {"key": str(item.get("key") or ""), "value": str(item.get("value") or ""), "description": item.get("description", "")}
                for item in url.get("query", []) if isinstance(item, dict) and not item.get("disabled")
            ]
        else:
            query = [{"key": k, "value": restore(v), "description": ""} for k, v in parse_qsl(parsed.query)]
        body_data = request.get("body") or {}
        body = {"mode": body_data.get("mode") or "none"}
        if body["mode"] == "raw":
            body.update({"raw": body_data.get("raw") or "", "content_type": _infer_content_type(headers, body_data.get("raw"))})
        elif body["mode"] in {"urlencoded", "formdata"}:
            body.update({"items": body_data.get(body["mode"]) or []})
        request_auth = request.get("auth") or {}
        if not request_auth or request_auth.get("type") == "inherit":
            request_auth = inherited_auth or {"type": "inherit"}
        endpoint = {
            "id": _endpoint_id(request.get("method", "GET").upper(), path or raw_url, len(endpoints) + 1),
            "name": item.get("name") or raw_url,
            "description": request.get("description") or "",
            "method": request.get("method", "GET").upper(),
            "protocol": parsed.scheme or (url.get("protocol", "") if isinstance(url, dict) else ""),
            "base_url": base_url,
            "path": path or raw_url,
            "path_params": [{"key": match.group(1), "value": "", "description": ""} for match in re.finditer(r"\{([A-Za-z0-9_.-]+)\}", path)],
            "query_params": query,
            "headers": headers,
            "cookies": [],
            "auth": request_auth,
            "body": body,
            "content_type": body.get("content_type") or _infer_content_type(headers, body.get("raw")),
            "responses": [],
            "variables": [],
            "dependencies": [],
            "status": "active",
            "folder": folder,
            "events": deepcopy(inherited_events or []) + deepcopy(item.get("event") or []),
            "source_refs": [_source_ref(source["name"], "postman_collection", item.get("name", ""))],
        }
        endpoints.append(endpoint)
    return endpoints


def _curl_endpoints(text: str, source: Dict[str, Any]) -> List[Dict[str, Any]]:
    endpoints: List[Dict[str, Any]] = []
    chunks = re.split(r"(?=\bcurl\s+)", text, flags=re.I)
    for chunk in chunks:
        if not chunk.strip().lower().startswith("curl"):
            continue
        try:
            parts = shlex.split(chunk.strip(), posix=False)
        except Exception:
            parts = chunk.strip().split()
        method = "GET"
        headers: List[Dict[str, str]] = []
        body_raw = ""
        url = ""
        index = 1
        while index < len(parts):
            part = parts[index].strip("'\"")
            next_part = parts[index + 1].strip("'\"") if index + 1 < len(parts) else ""
            if part in {"-X", "--request"} and next_part:
                method = next_part.upper()
                index += 2
                continue
            if part in {"-H", "--header"} and next_part:
                key, _, value = next_part.partition(":")
                headers.append({"key": key.strip(), "value": value.strip(), "description": ""})
                index += 2
                continue
            if part in {"-d", "--data", "--data-raw", "--data-binary"} and next_part:
                body_raw = next_part
                if method == "GET":
                    method = "POST"
                index += 2
                continue
            if part.startswith("http"):
                url = part
            index += 1
        if not url:
            continue
        parsed = urlparse(url)
        body = {"mode": "none"} if not body_raw else {"mode": "raw", "raw": body_raw, "content_type": _infer_content_type(headers, body_raw)}
        endpoints.append({
            "id": _endpoint_id(method, parsed.path or url, len(endpoints) + 1),
            "name": f"{method} {parsed.path or url}",
            "description": "Request detectado desde cURL.",
            "method": method,
            "protocol": parsed.scheme,
            "base_url": f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else "",
            "path": parsed.path or url,
            "path_params": [],
            "query_params": [{"key": k, "value": v, "description": ""} for k, v in parse_qsl(parsed.query)],
            "headers": headers,
            "cookies": [],
            "auth": _auth_from_headers(headers),
            "body": body,
            "content_type": body.get("content_type") or _infer_content_type(headers, body.get("raw")),
            "responses": [],
            "variables": [],
            "dependencies": [],
            "status": "active",
            "source_refs": [_source_ref(source["name"], "curl")],
        })
    return endpoints


def _text_endpoints(text: str, source: Dict[str, Any]) -> List[Dict[str, Any]]:
    endpoints: List[Dict[str, Any]] = []
    pattern = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+((?:https?://|/)[^\s)]+)", re.I)
    for match in pattern.finditer(text or ""):
        method = match.group(1).upper()
        raw_url = match.group(2).strip().rstrip(".,")
        parsed = urlparse(raw_url)
        endpoints.append({
            "id": _endpoint_id(method, parsed.path or raw_url, len(endpoints) + 1),
            "name": f"{method} {parsed.path or raw_url}",
            "description": "Endpoint detectado desde texto.",
            "method": method,
            "protocol": parsed.scheme,
            "base_url": f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else "",
            "path": parsed.path or raw_url,
            "path_params": [],
            "query_params": [{"key": k, "value": v, "description": ""} for k, v in parse_qsl(parsed.query)],
            "headers": [],
            "cookies": [],
            "auth": {"type": "inherit"},
            "body": {"mode": "none"},
            "content_type": "",
            "responses": [],
            "variables": [],
            "dependencies": [],
            "status": "active",
            "source_refs": [_source_ref(source["name"], "text", f"char {match.start()}")],
        })
    return endpoints


def _auth_from_headers(headers: List[Dict[str, str]]) -> Dict[str, Any]:
    for header in headers:
        key = header.get("key", "").lower()
        value = header.get("value", "")
        if key == "authorization":
            if value.lower().startswith("bearer"):
                return {"type": "bearer", "bearer": [{"key": "token", "value": "{{bearerToken}}", "type": "string"}]}
            if value.lower().startswith("basic"):
                return {"type": "basic"}
            return {"type": "apikey", "apikey": [{"key": "key", "value": "Authorization", "type": "string"}]}
        if "api" in key and "key" in key:
            return {"type": "apikey", "apikey": [{"key": "key", "value": header.get("key"), "type": "string"}]}
    return {"type": "inherit"}


def _extract_test_cases(text: str, source: Dict[str, Any]) -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []
    blocks = re.split(r"(?im)(?=^\s*(?:caso|test case|tc|cp)[\s#:_-]*\d*)", text or "")
    for idx, block in enumerate(blocks):
        clean = block.strip()
        if len(clean) < 25:
            continue
        if not re.search(r"(?i)\b(caso|test case|prueba|resultado esperado|expected|paso|step)", clean):
            continue
        first_line = clean.splitlines()[0][:160]
        case_id_match = re.search(r"(?i)\b(?:TC|CP|CASO|TEST CASE)[\s#:_-]*([A-Za-z0-9_.-]+)", first_line)
        case_id = case_id_match.group(1) if case_id_match else f"CASE-{len(cases) + 1:03d}"
        expected = ""
        expected_match = re.search(r"(?is)(resultado esperado|expected result|expected)\s*[:\-]\s*(.+?)(?:\n\s*\n|$)", clean)
        if expected_match:
            expected = expected_match.group(2).strip()[:1000]
        def field(label: str) -> str:
            boundary = r"(?:Endpoint|Request|Servicio|Categor[ií]a|Tipo|Precondiciones|Contexto inicial|Datos de prueba|Valores de entrada|Input data|Pasos|Steps|Resultado esperado|Criterio final|Expected result)"
            match = re.search(rf"(?ims)^[ \t]*{label}[ \t]*:[ \t]*(.*?)(?=^[ \t]*{boundary}[ \t]*:|\Z)", clean)
            return match.group(1).strip() if match else ""
        input_data = field(r"(?:Datos de prueba|Valores de entrada|Input data)")
        expected = field(r"(?:Resultado esperado|Criterio final|Expected result)") or expected
        status_match = re.search(r"(?i)\b(?:HTTP|status|c[oó]digo)\s*[:=]?\s*([1-5][0-9]{2})\b", expected)
        cases.append({
            "id": f"case_{len(cases) + 1}",
            "case_id": case_id,
            "name": re.sub(r"(?i)^(?:caso|test case)\s+", "", first_line.strip("# :-")) or f"Caso {len(cases) + 1}",
            "endpoint_hint": field(r"(?:Endpoint|Request|Servicio)"),
            "objective": "",
            "description": clean[:2500],
            "preconditions": [field(r"(?:Precondiciones|Contexto inicial)")] if field(r"(?:Precondiciones|Contexto inicial)") else [],
            "input_data": input_data,
            "steps": [line.strip(" -\t") for line in clean.splitlines()[1:] if line.strip()][:40],
            "expected_result": expected,
            "expected_status": status_match.group(1) if status_match else "",
            "postconditions": [],
            "priority": "",
            "test_type": _infer_case_type(field(r"(?:Categor[ií]a|Tipo)") + " " + clean),
            "dependencies": [],
            "created_or_modified_data": [],
            "related_request_ids": [],
            "source_refs": [_source_ref(source["name"], "test_case", first_line[:80])],
        })
    return cases


def _infer_case_type(text: str) -> str:
    normalized = (text or "").lower()
    for key in ("seguridad", "security", "autorizacion", "authorization", "negativo", "negative", "borde", "edge", "concurrencia", "idempotencia", "regresion"):
        if key in normalized:
            return key
    return "funcional"


def _extract_variables(sources: List[Dict[str, Any]], endpoints: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    variables: Dict[str, Dict[str, Any]] = {}
    warnings: List[Dict[str, Any]] = []
    base_urls: Dict[str, str] = {}

    def add_var(key: str, scope: str, source: str, value: str = "", sensitive: bool = False, reason: str = "") -> None:
        safe = _safe_key(key, "variable")
        if safe not in variables:
            variables[safe] = {
                "key": safe,
                "value": "" if sensitive else value,
                "scope": scope,
                "source": source,
                "sensitive": sensitive,
                "reason": reason,
                "enabled": True,
            }

    for endpoint in endpoints:
        if endpoint.get("base_url"):
            if not re.search(r"\{\{[A-Za-z0-9_.-]+\}\}", endpoint["base_url"]):
                url = endpoint["base_url"]
                endpoint["original_base_url"] = url
                key = base_urls.setdefault(url, "baseUrl" if not base_urls else f"baseUrl{len(base_urls) + 1}")
                add_var(key, "environment", endpoint["source_refs"][0]["source"], url, False, "Base URL detectada")
                endpoint["base_url"] = "{{" + key + "}}"
        for header in endpoint.get("headers", []):
            if re.search(r"(?i)(authorization|token|api[-_]?key|secret|cookie)", header.get("key", "")):
                add_var(header.get("key", "secret"), "environment", endpoint["source_refs"][0]["source"], "", True, "Header sensible")
                header["value"] = "{{" + _safe_key(header.get("key", "secret")) + "}}"
        for parameter in endpoint.get("path_params", []) + endpoint.get("query_params", []):
            key = str(parameter.get("key") or "").strip()
            if key:
                value = str(parameter.get("value") or "")
                placeholder = re.fullmatch(r"\{\{([A-Za-z0-9_.-]+)\}\}", value)
                add_var(placeholder.group(1) if placeholder else key, "environment", endpoint["source_refs"][0]["source"], "" if placeholder else value, False, "Parametro de request")

    for source in sources:
        text = source.get("text", "")
        structured = _try_load_structured(text)
        source_variables = []
        if isinstance(structured, dict):
            source_variables = structured.get("variable") or structured.get("values") or []
        for item in source_variables:
            if not isinstance(item, dict) or not item.get("key"):
                continue
            key = str(item["key"])
            value = str(item.get("value") or "")
            sensitive = bool(item.get("type") == "secret" or re.search(r"(?i)(token|secret|password|api.?key|authorization)", key))
            add_var(key, "environment", source["name"], value, sensitive, "Variable importada desde la fuente")
        for name in re.findall(r"\{\{([A-Za-z0-9_.-]+)\}\}", text):
            add_var(name, "environment", source["name"], "", False, "Variable marcada en documentacion")
        for secret in SECRET_RE.finditer(text):
            warnings.append({
                "severity": "high",
                "type": "possible_secret",
                "message": "Se detecto un posible secreto. No se exporta su valor por defecto.",
                "source": source["name"],
            })
    return list(variables.values()), warnings


def _associate(cases: List[Dict[str, Any]], endpoints: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def path_score(hint_path: str, endpoint_path: str) -> int:
        hint_segments = [segment for segment in hint_path.rstrip("/").lower().split("/") if segment]
        path_segments = [segment for segment in endpoint_path.rstrip("/").lower().split("/") if segment]
        if not hint_segments or len(path_segments) < len(hint_segments):
            return 0
        score = 0
        for hint, actual in zip(hint_segments, path_segments[-len(hint_segments):]):
            if hint == actual:
                score += 2
            elif re.fullmatch(r"(?::[a-z0-9_.-]+|\{\{?[a-z0-9_.-]+\}\}?)", actual):
                score += 1
            else:
                return 0
        return score

    associations: List[Dict[str, Any]] = []
    endpoint_by_id = {endpoint["id"]: endpoint for endpoint in endpoints}
    for case in cases:
        related = next((endpoint_by_id[request_id] for request_id in case.get("related_request_ids", []) if request_id in endpoint_by_id), None)
        if related:
            associations.append({"case_id": case["id"], "endpoint_id": related["id"], "confidence": "Alta", "score": 100,
                                 "evidence": ["Caso generado para este request"], "explanation": "Asociación directa.",
                                 "source": case["source_refs"][0]["source"], "confirmed": True})
            continue
        case_blob = " ".join([case.get("name", ""), case.get("description", ""), case.get("expected_result", "")]).lower()
        endpoint_hint = str(case.get("endpoint_hint") or "").strip()
        if not endpoint_hint and len(endpoints) == 1:
            associations.append({"case_id": case["id"], "endpoint_id": endpoints[0]["id"], "confidence": "Alta", "score": 90,
                                 "evidence": ["Único endpoint de la fuente"], "explanation": "Asociación por endpoint único.",
                                 "source": case["source_refs"][0]["source"], "confirmed": True})
            continue
        hint_match = re.match(r"(?i)^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(\S+)", endpoint_hint)
        if endpoint_hint and not hint_match:
            parsed_hint = urlparse(re.sub(r"^\{\{[^}]+\}\}", "", endpoint_hint))
            hint_path = parsed_hint.path.rstrip("/").lower()
            scored_paths = [(path_score(hint_path, str(endpoint.get("path") or "")), endpoint) for endpoint in endpoints
                            if hint_path and (not parsed_hint.netloc or parsed_hint.netloc.lower() in str(endpoint.get("original_base_url") or endpoint.get("base_url") or "").lower())]
            best_path_score = max((score for score, _ in scored_paths), default=0)
            path_matches = [endpoint for score, endpoint in scored_paths if score == best_path_score and score > 0]
            if len(path_matches) == 1:
                associations.append({
                    "case_id": case["id"], "endpoint_id": path_matches[0]["id"], "confidence": "Alta", "score": 90,
                    "evidence": ["Ruta única indicada en la planilla"], "explanation": "Asociación por la ruta del endpoint.",
                    "source": case["source_refs"][0]["source"], "confirmed": True,
                })
                continue
        if hint_match:
            parsed_hint = urlparse(re.sub(r"^\{\{[^}]+\}\}", "", hint_match.group(2)))
            hint_method, hint_path = hint_match.group(1).upper(), parsed_hint.path.rstrip("/").lower()
            exact_matches = []
            for endpoint in endpoints:
                path = str(endpoint.get("path", "")).rstrip("/").lower()
                path_segments = [segment for segment in path.split("/") if segment]
                if endpoint.get("method") != hint_method:
                    continue
                if parsed_hint.netloc and parsed_hint.netloc.lower() not in str(endpoint.get("original_base_url") or endpoint.get("base_url") or "").lower():
                    continue
                score = path_score(hint_path, path)
                if score:
                    exact_matches.append((len(path_segments), score, endpoint))
            if exact_matches:
                shortest = min(length for length, _, _ in exact_matches)
                best_score = max(score for length, score, _ in exact_matches if length == shortest)
                candidates = [endpoint for length, score, endpoint in exact_matches if length == shortest and score == best_score]
                if len(candidates) != 1:
                    associations.append({
                        "case_id": case["id"], "endpoint_id": "", "confidence": "Sin coincidencia", "score": 0,
                        "evidence": ["La ruta coincide con más de una API."],
                        "explanation": "Indicá una URL completa o el host para desambiguar el endpoint.",
                        "source": case["source_refs"][0]["source"], "confirmed": False,
                    })
                    continue
                endpoint = candidates[0]
                associations.append({
                    "case_id": case["id"],
                    "endpoint_id": endpoint["id"],
                    "confidence": "Alta",
                    "score": 100,
                    "evidence": ["Endpoint indicado en la planilla"],
                    "explanation": "Asociacion directa por el endpoint de la fila de casos.",
                    "source": case["source_refs"][0]["source"],
                    "confirmed": True,
                })
                continue
            associations.append({
                "case_id": case["id"],
                "endpoint_id": "",
                "confidence": "Sin coincidencia",
                "score": 0,
                "evidence": ["El endpoint indicado en la planilla no coincide con la collection."],
                "explanation": "Revisar el endpoint en la fila antes de exportar el caso.",
                "source": case["source_refs"][0]["source"],
                "confirmed": False,
            })
            continue
        best: Optional[Tuple[int, Dict[str, Any], List[str]]] = None
        for endpoint in endpoints:
            evidence: List[str] = []
            score = 0
            path_tokens = [token for token in re.split(r"[^a-zA-Z0-9]+", endpoint.get("path", "").lower()) if len(token) > 2]
            name_tokens = [token for token in re.split(r"[^a-zA-Z0-9]+", endpoint.get("name", "").lower()) if len(token) > 2]
            if endpoint.get("method", "").lower() in case_blob:
                score += 20
                evidence.append(f"Metodo {endpoint.get('method')} mencionado")
            matches = [token for token in set(path_tokens + name_tokens) if token in case_blob]
            if matches:
                score += min(60, len(matches) * 15)
                evidence.append("Coincidencias semanticas: " + ", ".join(matches[:6]))
            for response in endpoint.get("responses", []):
                status = str(response.get("status"))
                if status and status in case_blob:
                    score += 10
                    evidence.append(f"Status {status} mencionado")
            if not best or score > best[0]:
                best = (score, endpoint, evidence)
        if not best or best[0] < 40:
            associations.append({
                "case_id": case["id"],
                "endpoint_id": "",
                "confidence": "Sin coincidencia",
                "score": 0,
                "evidence": [],
                "explanation": "No se encontro evidencia suficiente para asociar este caso.",
                "source": case["source_refs"][0]["source"],
                "confirmed": False,
            })
        else:
            confidence = "Alta" if best[0] >= 70 else "Media" if best[0] >= 40 else "Baja"
            associations.append({
                "case_id": case["id"],
                "endpoint_id": best[1]["id"],
                "confidence": confidence,
                "score": best[0],
                "evidence": best[2],
                "explanation": f"Asociacion propuesta por coincidencias entre caso y request ({confidence}).",
                "source": case["source_refs"][0]["source"],
                "confirmed": confidence == "Alta",
            })
    return associations


def _case_blob(case: Dict[str, Any]) -> str:
    return " ".join([
        str(case.get("name", "")),
        str(case.get("description", "")),
        str(case.get("expected_result", "")),
        " ".join(str(step) for step in case.get("steps", [])),
    ]).lower()


def _generated_case(endpoint: Dict[str, Any], suffix: str, title: str, description: str, expected: str, case_number: int) -> Dict[str, Any]:
    method = endpoint.get("method", "GET")
    path = endpoint.get("path", endpoint.get("name", "endpoint"))
    return {
        "id": f"case_auto_{endpoint.get('id', case_number)}_{suffix}",
        "case_id": f"AUTO-{case_number:03d}",
        "name": f"{method} {path} - {title}",
        "objective": title,
        "description": description,
        "preconditions": [],
        "input_data": {},
        "steps": [
            f"Preparar request {method} {path}.",
            "Completar variables obligatorias con datos validos.",
            "Ejecutar request desde Postman.",
            "Validar status, estructura y mensaje de respuesta.",
        ],
        "expected_result": expected,
        "postconditions": [],
        "priority": "Media",
        "test_type": "funcional",
        "dependencies": [],
        "created_or_modified_data": [],
        "related_request_ids": [endpoint.get("id", "")],
        "source_refs": [_source_ref("generador_qa_senior", "generated_case", f"{method} {path}")],
        "generated": True,
    }


BUSINESS_RULE_RE = re.compile(
    r"(?i)(regla de negocio|logica de negocio|lógica de negocio|debe validar|no debe|solo si|"
    r"excepto|condicion|condición|estado|limite|límite|monto|fecha|vigente|vencid|"
    r"duplicad|permiso|habilitad|bloquead|aprobaci[oó]n|rechaz)"
)


def _endpoint_context(endpoint: Dict[str, Any], endpoint_cases: List[Dict[str, Any]], source_text: str = "") -> str:
    parts = [
        endpoint.get("name", ""),
        endpoint.get("description", ""),
        endpoint.get("path", ""),
        json.dumps(endpoint.get("responses") or [], ensure_ascii=False),
        " ".join(_case_blob(case) for case in endpoint_cases),
    ]
    path_tokens = [token for token in re.split(r"[^A-Za-z0-9]+", endpoint.get("path", "")) if len(token) > 3]
    if source_text and path_tokens:
        for line in source_text.splitlines():
            lowered = line.lower()
            if any(token.lower() in lowered for token in path_tokens) and BUSINESS_RULE_RE.search(line):
                parts.append(line)
    return " ".join(str(part) for part in parts if part)


def _complete_qa_case_coverage(
    endpoints: List[Dict[str, Any]],
    test_cases: List[Dict[str, Any]],
    associations: List[Dict[str, Any]],
    compare_excel: bool = False,
    source_text: str = "",
) -> Tuple[List[Dict[str, Any]], Dict[str, List[Dict[str, str]]]]:
    case_by_id = {case["id"]: case for case in test_cases}
    cases_by_endpoint: Dict[str, List[Dict[str, Any]]] = {}
    for assoc in associations:
        if assoc.get("endpoint_id") and assoc.get("case_id") in case_by_id:
            cases_by_endpoint.setdefault(assoc["endpoint_id"], []).append(case_by_id[assoc["case_id"]])

    generated: List[Dict[str, Any]] = []
    adjustments = {"added": [], "extra": []}
    case_number = len(test_cases) + 1

    for endpoint in endpoints:
        endpoint_cases = cases_by_endpoint.get(endpoint.get("id", ""), [])
        blobs = " ".join(_case_blob(case) for case in endpoint_cases)
        context = _endpoint_context(endpoint, endpoint_cases, source_text)
        method = endpoint.get("method", "GET")
        path = endpoint.get("path", endpoint.get("name", "endpoint"))

        needs = [
            (
                "ok",
                "caso base OK",
                f"Validar que el endpoint {method} {path} responda correctamente con datos validos.",
                "Respuesta exitosa y payload coherente con la documentacion.",
                not any(word in blobs for word in ("ok", "exito", "exitoso", "valido", "happy")),
            ),
            (
                "base_error",
                "caso base con error",
                f"Validar que el endpoint {method} {path} rechace una solicitud incorrecta o incompleta.",
                "La API responde con error controlado, sin guardar informacion invalida y con mensaje entendible.",
                not any(word in blobs for word in ("error", "invalido", "incorrecto", "400", "401", "403", "404", "negativo")),
            ),
        ]
        if BUSINESS_RULE_RE.search(context) and not any(word in blobs for word in ("negocio", "regla", "condicion", "condición")):
            needs.append((
                "business_rule",
                "logica de negocio",
                f"Validar la regla de negocio detectada para {method} {path}.",
                "La API aplica la regla de negocio documentada y responde correctamente ante escenarios limite.",
                True,
            ))

        for suffix, title, description, expected, should_add in needs:
            if not should_add:
                continue
            case = _generated_case(endpoint, suffix, title, description, expected, case_number)
            generated.append(case)
            adjustments["added"].append({"endpoint": f"{method} {path}", "case": case["name"]})
            case_number += 1

    if compare_excel:
        for assoc in associations:
            if not assoc.get("endpoint_id") and assoc.get("case_id") in case_by_id:
                case = case_by_id[assoc["case_id"]]
                adjustments["extra"].append({"case": case.get("name", case.get("case_id", "")), "reason": "No se encontro endpoint relacionado."})

    if not compare_excel:
        adjustments = {"added": [], "extra": []}
    return generated, adjustments


def _detect_conflicts(endpoints: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    conflicts: List[Dict[str, Any]] = []
    seen: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for endpoint in endpoints:
        key = (endpoint.get("method", ""), endpoint.get("path", ""))
        current = seen.get(key)
        if current and current.get("body") != endpoint.get("body"):
            conflicts.append({
                "type": "operation_body_conflict",
                "message": f"Distintas fuentes describen body diferente para {key[0]} {key[1]}.",
                "values": [
                    {"source": current["source_refs"][0]["source"], "value": current.get("body")},
                    {"source": endpoint["source_refs"][0]["source"], "value": endpoint.get("body")},
                ],
                "proposed_priority": "OpenAPI > Documentacion API > Postman > Casos > Inferencia IA",
                "resolved": False,
            })
        seen.setdefault(key, endpoint)
    return conflicts


def build_intermediate_model(sources: List[Dict[str, Any]], manual_text: str = "") -> Dict[str, Any]:
    sources = list(sources)
    if manual_text.strip():
        sources.append({"name": "texto_pegado", "extension": ".txt", "text": manual_text, "size": len(manual_text), "warning": ""})
    endpoints: List[Dict[str, Any]] = []
    test_cases: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    has_cases_file = any(source.get("is_cases_file") or source.get("extension", "").lower() in {".xlsx", ".csv"} for source in sources)
    has_api_source = any(source.get("extension", "").lower() in {".json", ".yaml", ".yml", ".curl", ".http"}
                         and not source.get("is_cases_file") for source in sources)
    for source in sources:
        source["kinds"] = _classify_source(source)
        if source.get("warning"):
            warnings.append({"severity": "medium", "type": "source_read", "message": source["warning"], "source": source["name"]})
        data = _try_load_structured(source.get("text", ""))
        if source.get("is_cases_file") or source.get("extension", "").lower() in {".xlsx", ".csv"}:
            source["is_cases_file"] = True
            test_cases.extend(_extract_test_cases(source.get("text", ""), source))
            continue
        if isinstance(data, dict) and ("openapi" in data or "swagger" in data or "paths" in data):
            endpoints.extend(_openapi_endpoints(data, source))
        elif _is_postman_collection(data):
            events = [event for event in data.get("event") or [] if isinstance(event, dict) and event.get("listen") == "prerequest"]
            items = _postman_items(data.get("item") or [], source, inherited_auth=data.get("auth"), inherited_events=events)
            source_variables = {str(variable.get("key")): str(variable.get("value") or "")
                                for variable in data.get("variable") or [] if isinstance(variable, dict) and variable.get("key")}
            for endpoint in items:
                base_match = re.fullmatch(r"\{\{([A-Za-z0-9_.-]+)\}\}", str(endpoint.get("base_url") or ""))
                resolved_base = source_variables.get(base_match.group(1), "") if base_match else ""
                if re.match(r"(?i)^https?://\S+$", resolved_base):
                    endpoint["base_url"] = resolved_base.rstrip("/")
            endpoints.extend(items)
        elif isinstance(data, dict) and isinstance(data.get("request"), dict):
            endpoints.extend(_postman_items([data], source))
        elif isinstance(data, dict) and data.get("method") and data.get("url"):
            endpoints.extend(_postman_items([{"name": data.get("name") or source["name"], "request": data}], source))
        elif isinstance(data, list) and all(isinstance(item, dict) for item in data):
            items = [item if "request" in item or "item" in item else {"name": item.get("name") or source["name"], "request": item} for item in data]
            endpoints.extend(_postman_items(items, source))
        else:
            endpoints.extend(_curl_endpoints(source.get("text", ""), source))
            if not has_api_source or source.get("extension", "").lower() not in {".docx", ".pdf", ".txt", ".md"}:
                endpoints.extend(_text_endpoints(source.get("text", ""), source))
            if not has_cases_file:
                test_cases.extend(_extract_test_cases(source.get("text", ""), source))
    endpoints = _dedupe_endpoints(endpoints)
    functional_texts = [(source.get("text", "").lower(), source.get("text", "").splitlines()) for source in sources
                        if not source.get("is_cases_file") and source.get("extension", "").lower() in {".docx", ".pdf", ".txt", ".md"}]
    for index, endpoint in enumerate(endpoints, start=1):
        endpoint["id"] = _endpoint_id(endpoint.get("method", "GET"), endpoint.get("path", "request"), index)
        path = str(endpoint.get("path") or "").lower().split("?")[0]
        if not path or path == "/":
            continue
        notes = []
        for lowered, lines in functional_texts:
            if path not in lowered:
                continue
            for line in lines:
                if path in line.lower() and len(line) <= 300 and not SECRET_RE.search(line):
                    notes.append(line.strip())
                    if len(notes) >= 4:
                        break
            if len(notes) >= 4:
                break
        if notes:
            endpoint["description"] = "\n".join(filter(None, [str(endpoint.get("description") or ""), "Reglas del funcional:", *notes]))
    variables, secret_warnings = _extract_variables(sources, endpoints)
    warnings.extend(secret_warnings)
    associations = _associate(test_cases, endpoints)
    coverage_source_text = "\n".join(source.get("text", "") for source in sources
                                     if source.get("extension", "").lower() in {".docx", ".pdf", ".txt", ".md"})
    if has_cases_file:
        generated_cases = []
        case_adjustments = {"added": [], "extra": []}
    else:
        generated_cases, case_adjustments = _complete_qa_case_coverage(
            endpoints,
            test_cases,
            associations,
            False,
            coverage_source_text,
        )
    if generated_cases:
        test_cases.extend(generated_cases)
        associations = _associate(test_cases, endpoints)
    endpoint_by_id = {endpoint.get("id"): endpoint for endpoint in endpoints}
    case_by_id = {case.get("id"): case for case in test_cases}
    for association in associations:
        case = case_by_id.get(association.get("case_id"), {})
        endpoint = endpoint_by_id.get(association.get("endpoint_id"), {})
        body_raw = str((endpoint.get("body") or {}).get("raw") or "")
        if case.get("generated") and (
            "error" in str(case.get("name", "")).lower()
            or re.search(r"(?i)todo|completar", body_raw)
        ):
            association["included"] = False
    endpoint_ids_with_case = {a["endpoint_id"] for a in associations if a.get("endpoint_id")}
    for endpoint in endpoints:
        if endpoint["id"] not in endpoint_ids_with_case:
            warnings.append({
                "severity": "low",
                "type": "endpoint_without_case",
                "message": f"Endpoint sin caso asociado: {endpoint['method']} {endpoint['path']}",
                "source": endpoint["source_refs"][0]["source"],
            })
    for assoc in associations:
        if not assoc.get("endpoint_id"):
            warnings.append({
                "severity": "medium",
                "type": "case_without_endpoint",
                "message": f"Caso sin endpoint asociado: {assoc['case_id']}",
                "source": assoc.get("source", ""),
            })
    return {
        "version": "postman_intermediate_v1",
        "sources": [{k: v for k, v in source.items() if k != "text"} for source in sources],
        "endpoints": endpoints,
        "test_cases": test_cases,
        "has_cases_file": has_cases_file,
        "associations": associations,
        "variables": variables,
        "dependencies": [],
        "warnings": warnings,
        "conflicts": _detect_conflicts(endpoints),
        "case_adjustments": case_adjustments,
        "folders": ["Endpoints", "Casos de prueba"],
        "validation": {},
    }


def _postman_url(endpoint: Dict[str, Any]) -> Dict[str, Any]:
    base = endpoint.get("base_url") or "{{baseUrl}}"
    path = endpoint.get("path", "")
    path = re.sub(r"(?<!\{)\{([A-Za-z0-9_.-]+)\}(?!\})", lambda match: "{{" + _safe_key(match.group(1)) + "}}", path)
    raw = base.rstrip("/") + "/" + path.lstrip("/")
    query = [
        {"key": item.get("key", ""), "value": item.get("value") or "{{" + _safe_key(str(item.get("key") or "param")) + "}}", "description": item.get("description", "")}
        for item in endpoint.get("query_params", [])
    ]
    if query:
        raw += "?" + "&".join(f"{quote(str(item['key']), safe='')}={quote(str(item['value']), safe='{}')}" for item in query)
    return {"raw": raw, "host": [base], "path": [part for part in path.strip("/").split("/") if part], "query": query}


def _postman_body(body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    mode = (body or {}).get("mode", "none")
    if mode == "none":
        return None
    if mode == "raw":
        return {
            "mode": "raw",
            "raw": body.get("raw", ""),
            "options": {"raw": {"language": "json" if "json" in body.get("content_type", "") else "text"}},
        }
    if mode in {"urlencoded", "formdata"}:
        return {"mode": mode, mode: body.get("items") or []}
    if mode == "file":
        return {"mode": "file", "file": {"src": body.get("src", "")}}
    return {"mode": "raw", "raw": str(body.get("raw", ""))}


PLACEHOLDER_RE = re.compile(r"\{\{([A-Za-z0-9_.-]+)\}\}")
CREDENTIAL_RE = re.compile(r"(?i)pass|contrase|secret|token|authorization|api.?key|cookie|credential")
PERSONAL_RE = re.compile(r"(?i)cuit|cuil|dni|document|email|client|cliente|asegurad|person|persona|first.?name|last.?name|nombre|apellido|phone|telefono|direccion|address")
QUERYABLE_RE = re.compile(r"(?i)poliz|policy|siniest|claim|client|cliente|asegurad|cuit|cuil|dni|ramo|branch|product|producto|certific|fecha|date|vigenc|cobertura|coverage|tomador|intermediar")
FIELD_ALIASES = {"password": ("contrasena", "clave"), "username": ("usuario", "user"), "policy": ("poliza",), "client": ("cliente",)}


def _normalized_field(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "").lower())
    return re.sub(r"[^a-z0-9]", "", "".join(char for char in normalized if not unicodedata.combining(char)))


def _matches_field(left: str, right: str) -> bool:
    first, second = _normalized_field(left), _normalized_field(right)
    if first == second:
        return True
    aliases = FIELD_ALIASES.get(first, ()) + FIELD_ALIASES.get(second, ())
    return first in aliases or second in aliases


def _case_inputs(case: Optional[Dict[str, Any]]) -> Tuple[Dict[str, Any], str]:
    raw = (case or {}).get("input_data") or {}
    if isinstance(raw, dict):
        return raw, ""
    if not isinstance(raw, str) or not raw.strip():
        return {}, ""
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed, ""
    except (TypeError, ValueError):
        pass
    values: Dict[str, str] = {}
    for segment in re.split(r";|\n|,(?=\s*[\wÀ-ÿ .-]+\s*[:=])", raw):
        match = re.match(r"^\s*([\wÀ-ÿ .-]+?)\s*[:=]\s*(.*?)\s*$", segment)
        if match:
            values[match.group(1).strip()] = match.group(2).strip()
    return values, "" if values else raw.strip()


def _case_omits(case: Optional[Dict[str, Any]], field: str) -> bool:
    if not case:
        return False
    text = " ".join((str(case.get("name") or ""), str(case.get("input_data") or ""), str(case.get("description") or "")))
    normalized = unicodedata.normalize("NFKD", text.lower())
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    candidates = [field, *FIELD_ALIASES.get(_normalized_field(field), ())]
    return any(re.search(r"\b(?:omitir|sin|no enviar|no incluir)\s+(?:(?:el|la|campo|dato)\s+)?" + re.escape(candidate.lower()) + r"\b", normalized) for candidate in candidates)


def _case_value(values: Dict[str, Any], field: str, used: set) -> Any:
    for key, value in values.items():
        if _matches_field(key, field):
            used.add(key)
            return value
    return None


def _register_variable(registry: Dict[str, Dict[str, Any]], key: str, value: Any, reason: str, context: str = "", sensitive: bool = False) -> str:
    key = key if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", key) else _safe_key(key)
    text_value = "" if value is None else str(value)
    if PLACEHOLDER_RE.fullmatch(text_value):
        text_value = ""
    secret = sensitive or bool(CREDENTIAL_RE.search(key) or PERSONAL_RE.search(key))
    if key not in registry:
        registry[key] = {"key": key, "value": "" if secret else text_value, "sensitive": secret,
                         "reason": reason, "context": context, "queryable": bool(QUERYABLE_RE.search(key) and not CREDENTIAL_RE.search(key))}
    return key


def _case_prefix(case: Optional[Dict[str, Any]], endpoint: Dict[str, Any]) -> str:
    identity = (case or {}).get("case_id") or endpoint.get("id") or "request"
    return re.sub(r"[^a-z0-9]+", "_", str(identity).lower()).strip("_") or "request"


def _choose_template(endpoint: Dict[str, Any], case: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    variants = [endpoint] + endpoint.get("variants", [])
    if not case or len(variants) == 1:
        return endpoint
    name = unicodedata.normalize("NFKD", str(case.get("name") or "").lower())
    case_words = set(re.findall(r"[a-z0-9]{4,}", "".join(char for char in name if not unicodedata.combining(char))))
    def score(variant: Dict[str, Any]) -> int:
        text = (variant.get("name", "") + " " + variant.get("folder", "")).lower()
        return sum(1 for word in re.findall(r"[a-z0-9]{4,}", text) if word in case_words)
    return max(variants, key=score)


def _prepare_body(body: Dict[str, Any], case: Optional[Dict[str, Any]], endpoint: Dict[str, Any],
                  case_values: Dict[str, Any], used_inputs: set, registry: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    result = _postman_body(body)
    if result is None:
        if case_values and endpoint.get("method") in {"POST", "PUT", "PATCH"} and isinstance((case or {}).get("input_data"), str):
            raw = (case or {}).get("input_data", "")
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    result = {"mode": "raw", "raw": json.dumps(parsed, ensure_ascii=False), "options": {"raw": {"language": "json"}}}
                    used_inputs.update(case_values)
            except (TypeError, ValueError):
                pass
        if result is None:
            return None
    prefix = _case_prefix(case, endpoint)
    if result.get("mode") == "raw":
        raw = result.get("raw") or ""
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            if raw and not PLACEHOLDER_RE.search(raw):
                key = _register_variable(registry, f"{prefix}_body", raw, "Body del request", (case or {}).get("name", endpoint.get("name", "")))
                result["raw"] = "{{" + key + "}}"
            return result
        numeric_keys = set()
        def walk(value: Any, path: List[str]) -> Any:
            if isinstance(value, dict):
                return {key: walk(item, path + [key]) for key, item in value.items() if not _case_omits(case, key)}
            if isinstance(value, list):
                return [walk(item, path + [str(index)]) for index, item in enumerate(value)]
            field = path[-1] if path else "body"
            override = _case_value(case_values, field, used_inputs)
            has_override = any(_matches_field(key, field) for key in case_values)
            selected = override if has_override else value
            if selected is None or isinstance(selected, bool):
                return selected
            placeholder = PLACEHOLDER_RE.fullmatch(str(selected))
            if placeholder:
                _register_variable(registry, placeholder.group(1), "", f"Campo {field}", (case or {}).get("name", ""))
                return str(selected)
            key = _register_variable(registry, f"{prefix}_{'_'.join(path)}", selected, f"Campo {field} del body", (case or {}).get("name", endpoint.get("name", "")))
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                numeric_keys.add(key)
            return "{{" + key + "}}"
        parsed = walk(parsed, [])
        result["raw"] = json.dumps(parsed, ensure_ascii=False, indent=2)
        for key in numeric_keys:
            result["raw"] = result["raw"].replace('"{{' + key + '}}"', '{{' + key + '}}')
    elif result.get("mode") in {"formdata", "urlencoded"}:
        for entry in result.get(result["mode"], []):
            if entry.get("type") == "file":
                continue
            field = str(entry.get("key") or "dato")
            override = _case_value(case_values, field, used_inputs)
            selected = override if override is not None else entry.get("value", "")
            placeholder = PLACEHOLDER_RE.fullmatch(str(selected))
            key = placeholder.group(1) if placeholder else f"{prefix}_{field}"
            key = _register_variable(registry, key, "" if placeholder else selected, f"Campo {field} del formulario", (case or {}).get("name", ""))
            entry["value"] = "{{" + key + "}}"
    return result


def _prepare_auth(auth: Dict[str, Any], registry: Dict[str, Dict[str, Any]], context: str) -> Dict[str, Any]:
    result = deepcopy(auth or {"type": "inherit"})
    mode = result.get("type")
    for field in ("bearer", "basic", "apikey", "oauth2"):
        for entry in result.get(field) or []:
            if not isinstance(entry, dict) or "value" not in entry:
                continue
            name = str(entry.get("key") or field)
            if mode == "bearer" and name == "token":
                name = "bearerToken"
            if mode == "apikey" and name == "value":
                name = "apiKey"
            value = str(entry.get("value") or "")
            if mode == "apikey" and entry.get("key") in {"key", "in"}:
                continue
            placeholder = PLACEHOLDER_RE.fullmatch(value)
            key = _register_variable(registry, placeholder.group(1) if placeholder else name, "", f"Autenticación {mode}", context, sensitive=True)
            entry["value"] = "{{" + key + "}}"
    return result


def _tests_for_endpoint(endpoint: Dict[str, Any], case: Optional[Dict[str, Any]] = None) -> str:
    statuses = {
        str(resp.get("status"))
        for resp in endpoint.get("responses", [])
        if str(resp.get("status", "")).isdigit()
    }
    lines: List[str] = []
    case_status = str((case or {}).get("expected_status") or "").strip()
    if not case_status:
        result_match = re.search(r"(?i)(?:status(?:\s+HTTP)?|HTTP|respuesta|response)[^0-9]{0,12}([1-5][0-9]{2})|(?:esperad[oa]|expected)[^0-9]{0,20}([1-5][0-9]{2})", str((case or {}).get("expected_result") or ""))
        case_status = next((value for value in result_match.groups() if value), "") if result_match else ""
    if case_status:
        lines.extend([
            "pm.test('La respuesta tiene el status esperado para este caso', function () {",
            f"  pm.response.to.have.status({case_status});",
            "});",
        ])
    elif case:
        case_text = _case_blob(case)
        negative = case.get("test_type") in {"negativo", "negative", "seguridad", "security"} or bool(re.search(r"(?i)\b(?:error|rechaz\w*|inv[aá]lid\w*|incorrect\w*|sin\s+\w+|omitir|no autorizado)\b", case_text))
        status_family = 4 if negative else 2
        lines.extend([
            f"pm.test('La respuesta pertenece a HTTP {status_family}xx', function () {{",
            f"  pm.expect(pm.response.code).to.be.within({status_family}00, {status_family}99);",
            "});",
        ])
    elif statuses:
        lines.extend([
            "pm.test('La respuesta coincide con un status documentado', function () {",
            f"  pm.expect([{', '.join(sorted(statuses))}]).to.include(pm.response.code);",
            "});",
        ])
    expected_response = (case or {}).get("expected_response")
    if expected_response:
        try:
            expected_json = json.loads(expected_response) if isinstance(expected_response, str) else expected_response
            lines.extend([
                "pm.test('El body contiene los datos esperados', function () {",
                f"  pm.expect(pm.response.json()).to.deep.include({json.dumps(expected_json, ensure_ascii=False)});",
                "});",
            ])
        except (TypeError, ValueError):
            lines.append("// Pendiente QA: la respuesta esperada no contiene JSON valido.")
    if case:
        lines.extend(["", f"// Caso QA: {str(case.get('case_id') or case.get('name') or '').replace(chr(10), ' ')}"])
    if not lines:
        lines.extend(["", "// Pendiente QA: completar el resultado esperado antes de considerar este caso validado."])
    return "\n".join(lines)


def _build_artifacts(model: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    associations = model.get("associations", [])
    cases = model.get("test_cases", [])
    case_by_id = {case.get("id"): case for case in cases}
    cases_by_endpoint: Dict[str, List[Dict[str, Any]]] = {}
    associated_endpoint_ids = set()
    for association in associations:
        case = case_by_id.get(association.get("case_id"))
        endpoint_id = association.get("endpoint_id")
        if case and endpoint_id:
            associated_endpoint_ids.add(endpoint_id)
        if case and endpoint_id and association.get("included", True):
            cases_by_endpoint.setdefault(endpoint_id, []).append(case)
    registry: Dict[str, Dict[str, Any]] = {}
    for variable in model.get("variables", []):
        _register_variable(registry, str(variable.get("key") or "variable"), variable.get("value"),
                           str(variable.get("reason") or "Variable de la fuente"), str(variable.get("source") or ""),
                           bool(variable.get("sensitive")))
    folders: Dict[str, List[Dict[str, Any]]] = {}
    issues: List[str] = []
    used_vars = {var["key"] for var in registry.values() if var.get("reason") == "Variable importada desde la fuente"}
    supplied_by_script = set()
    request_count = 0
    for endpoint in model.get("endpoints", []):
        if endpoint.get("status") in {"disabled", "blocked"}:
            continue
        endpoint_cases = cases_by_endpoint.get(endpoint.get("id"), [])
        if model.get("has_cases_file") and not endpoint_cases:
            continue
        if endpoint.get("id") in associated_endpoint_ids and not endpoint_cases:
            continue
        request_cases = endpoint_cases or [None]
        for case in request_cases:
            template = deepcopy(_choose_template(endpoint, case))
            inputs, opaque_inputs = _case_inputs(case)
            used_inputs: set = set()
            prefix = _case_prefix(case, endpoint)
            for parameter in template.get("path_params", []):
                field = str(parameter.get("key") or "")
                if not field:
                    continue
                override = _case_value(inputs, field, used_inputs)
                if override is not None:
                    key = _register_variable(registry, f"{prefix}_{field}", override, f"Parámetro de ruta {field}", (case or {}).get("name", ""))
                    template["path"] = template["path"].replace("{" + field + "}", "{{" + key + "}}")
            query_params = []
            for parameter in template.get("query_params", []):
                field = str(parameter.get("key") or "")
                if _case_omits(case, field):
                    continue
                value = str(parameter.get("value") or "")
                override = _case_value(inputs, field, used_inputs)
                placeholder = PLACEHOLDER_RE.fullmatch(value)
                key = f"{prefix}_{field}" if override is not None else placeholder.group(1) if placeholder else field
                key = _register_variable(registry, key, override if override is not None else "" if placeholder else value,
                                         f"Parámetro de consulta {field}", (case or {}).get("name", endpoint.get("name", "")))
                query_params.append({**parameter, "value": "{{" + key + "}}"})
            template["query_params"] = query_params
            headers = []
            for header in template.get("headers", []):
                field = str(header.get("key") or "")
                if _case_omits(case, field):
                    continue
                value = str(header.get("value") or "")
                if CREDENTIAL_RE.search(field) or PERSONAL_RE.search(field):
                    placeholder = PLACEHOLDER_RE.fullmatch(value)
                    key = _register_variable(registry, placeholder.group(1) if placeholder else field, "", f"Header {field}", (case or {}).get("name", ""), True)
                    value = "{{" + key + "}}"
                headers.append({**header, "value": value})
            request: Dict[str, Any] = {
                "method": template.get("method", "GET"),
                "header": headers,
                "url": _postman_url(template),
                "description": "\n\n".join(filter(None, [template.get("description", ""), (case or {}).get("objective", ""), (case or {}).get("expected_result", "")])),
            }
            case_body = (case or {}).get("request_body")
            body_source = template.get("body") or {}
            if case_body:
                try:
                    parsed_body = json.loads(case_body) if isinstance(case_body, str) else case_body
                    body_source = {"mode": "raw", "raw": json.dumps(parsed_body, ensure_ascii=False, indent=2), "content_type": "application/json"}
                except (TypeError, ValueError):
                    body_source = {"mode": "raw", "raw": str(case_body), "content_type": "application/json"}
            body = _prepare_body(body_source, case, template, inputs, used_inputs, registry)
            if body:
                request["body"] = body
                if body.get("mode") == "file" or any(entry.get("type") == "file" for entry in body.get("formdata", [])):
                    issues.append(f"{(case or {}).get('name', template.get('name', 'Request'))}: seleccionar archivo local en Postman.")
            auth = _prepare_auth(template.get("auth") or {}, registry, template.get("name", ""))
            if auth.get("type") and auth.get("type") != "inherit":
                request["auth"] = auth
            endpoint_name = template.get("name") or f"{template.get('method')} {template.get('path')}"
            case_name = (case or {}).get("name") or "Pendiente: asociar caso y expectativa"
            events = []
            for event in template.get("events") or []:
                if not isinstance(event, dict):
                    continue
                script = event.get("script") or {}
                code = "\n".join(script.get("exec") or []) if isinstance(script.get("exec"), list) else str(script.get("exec") or "")
                if SECRET_RE.search(code):
                    issues.append(f"{case_name}: un script de origen contiene un posible secreto y requiere revisión manual.")
                    continue
                supplied_by_script.update(re.findall(r"pm\.(?:environment|collectionVariables)\.set\(\s*['\"]([A-Za-z0-9_.-]+)['\"]", code))
                events.append(deepcopy(event))
            events.append({"listen": "test", "script": {"type": "text/javascript", "exec": _tests_for_endpoint(template, case).splitlines()}})
            item = {
                "name": case_name if case else f"Pendiente QA - {endpoint_name}",
                "request": request,
                "event": events,
            }
            if case and opaque_inputs and not any(_case_omits(case, field) for field in re.findall(r"[A-Za-z0-9_]+", json.dumps(template, ensure_ascii=False))):
                issues.append(f"{case_name}: revisar cómo aplicar los datos de prueba: {opaque_inputs[:120]}")
            unmatched = [key for key in inputs if key not in used_inputs]
            if unmatched:
                issues.append(f"{case_name}: datos sin campo equivalente en el request: {', '.join(unmatched[:8])}.")
            if case and not case.get("expected_status"):
                issues.append(f"{case_name}: confirmar el status HTTP exacto; se agregó una validación general 2xx/4xx.")
            used_vars.update(PLACEHOLDER_RE.findall(json.dumps(request, ensure_ascii=False)))
            for event in events:
                script = event.get("script") or {}
                code = "\n".join(script.get("exec") or []) if isinstance(script.get("exec"), list) else str(script.get("exec") or "")
                used_vars.update(re.findall(r"pm\.(?:environment|collectionVariables)\.get\(\s*['\"]([A-Za-z0-9_.-]+)['\"]", code))
            folder = template.get("folder") or "Casos de prueba"
            folders.setdefault(folder, []).append(item)
            request_count += 1
    for key in used_vars:
        if key not in registry:
            _register_variable(registry, key, "", "Valor usado por el request")
    variables = [registry[key] for key in sorted(used_vars) if key in registry]
    missing_data = [{"key": var["key"], "reason": var["reason"], "context": var["context"], "queryable": var["queryable"]}
                    for var in variables if not var["value"] and var["key"] not in supplied_by_script]
    queryable = [item for item in missing_data if item["queryable"]]
    query_request = ""
    if queryable:
        items = [f"- {item['key']}: {item['reason']}" for item in queryable[:30]]
        query_request = ("Necesito una consulta Oracle SELECT de solo lectura para encontrar datos de prueba para la collection "
                         + str(model.get("project_name") or "API") + ". No ejecutes la consulta; devolvé solo la query para revisión manual. "
                         "Usá únicamente tablas y relaciones del catálogo y las definiciones funcionales guardadas. "
                         "Si falta una regla necesaria, preguntámela. Datos pendientes:\n" + "\n".join(items))[:8000]
    collection = {
        "info": {
            "name": model.get("project_name") or "Coleccion QA generada",
            "schema": POSTMAN_SCHEMA,
            "description": "Generada por QA Doc Analyzer desde documentacion, casos y fuentes adjuntas.",
        },
        "item": [{"name": name, "item": items} for name, items in folders.items()],
        "variable": [],
    }
    environment = {
        "name": f"{model.get('project_name') or 'QA'} Environment",
        "values": [{"key": var["key"], "value": var["value"], "type": "secret" if var["sensitive"] else "default",
                    "enabled": True, "description": ("Pendiente: " if not var["value"] else "") + var["reason"] + (" | " + var["context"] if var["context"] else "")}
                   for var in variables],
        "_postman_variable_scope": "environment",
        "_postman_exported_using": "QA Doc Analyzer",
    }
    readiness = {"request_count": request_count, "missing_data": missing_data, "manual_checks": list(dict.fromkeys(issues)),
                 "query_request": query_request}
    return collection, environment, readiness


def build_collection(model: Dict[str, Any]) -> Dict[str, Any]:
    return _build_artifacts(model)[0]


def build_environment(model: Dict[str, Any]) -> Dict[str, Any]:
    return _build_artifacts(model)[1]


def build_readiness(model: Dict[str, Any]) -> Dict[str, Any]:
    return _build_artifacts(model)[2]


def build_traceability(model: Dict[str, Any]) -> str:
    endpoint_by_id = {item["id"]: item for item in model.get("endpoints", [])}
    case_by_id = {item["id"]: item for item in model.get("test_cases", [])}
    lines = ["# Informe de trazabilidad", "", "| Caso | Request | Confianza | Evidencia |", "|---|---|---|---|"]
    for assoc in model.get("associations", []):
        case = case_by_id.get(assoc.get("case_id"), {})
        endpoint = endpoint_by_id.get(assoc.get("endpoint_id"), {})
        req = f"{endpoint.get('method', '')} {endpoint.get('path', '')}".strip() or "Sin coincidencia"
        lines.append(
            f"| {case.get('case_id', assoc.get('case_id'))} - {case.get('name', '')} | "
            f"{req} | {assoc.get('confidence')} | {'; '.join(assoc.get('evidence') or [])} |"
        )
    return "\n".join(lines)


def build_readme(model: Dict[str, Any]) -> str:
    warnings = model.get("warnings", [])
    conflicts = model.get("conflicts", [])
    return "\n".join([
        "# Coleccion Postman generada",
        "",
        "## Importacion",
        "1. Importar `collection.json` en Postman.",
        "2. Importar `environment.json` si fue generado.",
        "3. Revisar variables vacias antes de ejecutar.",
        "",
        "## Autenticacion",
        "La autenticacion fue conservada solamente cuando aparecio evidencia en las fuentes. Requests sin evidencia heredan la configuracion o quedan sin auth especifica.",
        "",
        "## Orden de ejecucion",
        "Ejecutar requests individuales o por carpeta. Revisar la trazabilidad para flujos multi-step y dependencias pendientes.",
        "",
        "## Variables pendientes",
        *[f"- `{var['key']}` ({var.get('scope', 'environment')}): {var.get('reason', '')}" for var in model.get("variables", [])],
        "",
        "## Advertencias",
        *(f"- [{w.get('severity')}] {w.get('message')} ({w.get('source', '')})" for w in warnings),
        "",
        "## Conflictos",
        *(f"- {c.get('message')} - resuelto: {c.get('resolved', False)}" for c in conflicts),
    ])


def validate_model(model: Dict[str, Any]) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []
    for endpoint in model.get("endpoints", []):
        if endpoint.get("method") not in HTTP_METHODS:
            errors.append(f"Metodo invalido en {endpoint.get('name')}.")
        if not endpoint.get("path"):
            errors.append(f"Request sin path/url: {endpoint.get('name')}.")
        if endpoint.get("method") == "GET" and (endpoint.get("body") or {}).get("mode") not in {None, "none"}:
            warnings.append(f"GET con body en {endpoint.get('name')}; revisar antes de exportar.")
        content_type = endpoint.get("content_type") or ""
        body = endpoint.get("body") or {}
        if body.get("mode") == "raw" and "json" in content_type:
            try:
                json.loads(body.get("raw") or "{}")
            except Exception:
                warnings.append(f"Body JSON no parseable en {endpoint.get('name')}.")
    declared_vars = {var["key"] for var in model.get("variables", [])}
    used_vars = set(re.findall(r"\{\{([A-Za-z0-9_.-]+)\}\}", json.dumps(model, ensure_ascii=False)))
    missing_vars = sorted(used_vars - declared_vars)
    if missing_vars:
        warnings.append("Variables usadas no declaradas: " + ", ".join(missing_vars))
    if any(w.get("type") == "possible_secret" for w in model.get("warnings", [])):
        warnings.append("Hay posibles secretos detectados; confirmar antes de compartir la coleccion.")
    try:
        json.dumps(build_collection(model))
        json.dumps(build_environment(model))
    except Exception as exc:
        errors.append(f"No se pudo serializar Postman: {type(exc).__name__}.")
    return {"valid": not errors, "errors": errors, "warnings": warnings}


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


def _require_qa(user: Dict[str, Any]) -> None:
    if user.get("role") != "qa":
        raise HTTPException(status_code=403, detail="Postman disponible solo para QA.")


async def _get_draft(draft_id: str, user: Dict[str, Any]) -> Dict[str, Any]:
    doc = await _db()[COLLECTION].find_one({"_id": _oid(draft_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Draft no encontrado.")
    if not user.get("shared") and doc.get("created_by") != user.get("username"):
        raise HTTPException(status_code=403, detail="No podes acceder a este draft.")
    return doc


@router.post("/analyze")
async def analyze_postman_sources(
    files: Optional[List[UploadFile]] = File(default=None),
    test_cases_file: Optional[UploadFile] = File(default=None),
    manual_text: str = Form(default=""),
    project_name: str = Form(default=""),
    user: Dict[str, Any] = Depends(current_user),
):
    _require_qa(user)
    if len(manual_text) > MAX_TOTAL_SOURCE_TEXT_CHARS:
        raise HTTPException(status_code=413, detail="El texto pegado es demasiado grande para analizarlo completo.")
    if len(files or []) + bool(test_cases_file and test_cases_file.filename) > MAX_SOURCE_FILES:
        raise HTTPException(status_code=413, detail=f"Cargá como máximo {MAX_SOURCE_FILES} archivos.")
    sources = []
    total_bytes = 0
    total_text_chars = len(manual_text)
    for file in files or []:
        source = await _read_upload(file)
        if source.get("warning") or not source.get("text", "").strip():
            raise HTTPException(status_code=422, detail=f"No pude leer {source['name']} por completo. Revisá el formato del archivo.")
        total_bytes += source["size"]
        total_text_chars += len(source["text"])
        if total_bytes > MAX_TOTAL_SOURCE_BYTES:
            raise HTTPException(status_code=413, detail="El conjunto de archivos supera el límite de 40 MB.")
        if total_text_chars > MAX_TOTAL_SOURCE_TEXT_CHARS:
            raise HTTPException(status_code=413, detail="El conjunto de documentos contiene demasiado texto para analizarlo completo.")
        sources.append(source)
    case_source = None
    if test_cases_file and test_cases_file.filename:
        case_source = await _read_upload(test_cases_file)
        if case_source.get("warning") or not case_source.get("text", "").strip():
            raise HTTPException(status_code=422, detail=f"No pude leer el archivo de casos {case_source['name']}.")
        if total_bytes + case_source["size"] > MAX_TOTAL_SOURCE_BYTES:
            raise HTTPException(status_code=413, detail="El conjunto de archivos supera el límite de 40 MB.")
        if total_text_chars + len(case_source["text"]) > MAX_TOTAL_SOURCE_TEXT_CHARS:
            raise HTTPException(status_code=413, detail="El conjunto de documentos contiene demasiado texto para analizarlo completo.")
        case_source["is_cases_file"] = True
        sources.append(case_source)
    if not sources and not manual_text.strip():
        raise HTTPException(status_code=400, detail="Carga al menos un archivo o texto.")
    model = await run_in_threadpool(build_intermediate_model, sources, manual_text)
    if not model["endpoints"]:
        raise HTTPException(status_code=422, detail="No encontré endpoints en los archivos. Subí una collection, un request JSON, OpenAPI o cURL válido.")
    if case_source and not model["test_cases"]:
        raise HTTPException(status_code=422, detail=f"No encontré casos de prueba en {case_source['name']}. Revisá encabezados e IDs del Excel/CSV.")
    if case_source:
        case_by_id = {case["id"]: case for case in model["test_cases"]}
        unmatched = [case_by_id[item["case_id"]].get("name", item["case_id"])
                     for item in model["associations"] if (not item.get("endpoint_id") or not item.get("confirmed")) and item.get("case_id") in case_by_id]
        if unmatched:
            raise HTTPException(status_code=422, detail="No pude asociar estos casos a endpoints: " + "; ".join(unmatched[:10]) + ". Agregá método y ruta en la columna Endpoint.")
    model["project_name"] = _clean(project_name, 180) or "Proyecto API"
    if case_source:
        model["cases_source"] = {
            "name": case_source["name"],
            "detected_cases": len([case for case in model["test_cases"] if case.get("source_refs", [{}])[0].get("source") == case_source["name"]]),
        }
    model["validation"] = await run_in_threadpool(validate_model, model)
    if not model["validation"]["valid"]:
        raise HTTPException(status_code=422, detail="La collection no es válida: " + "; ".join(model["validation"]["errors"][:5]))
    model["readiness"] = await run_in_threadpool(build_readiness, model)
    now = _now()
    doc = {
        "project_name": _clean(project_name, 180) or "Proyecto API",
        "model": model,
        "created_by": user["username"],
        "created_at": now,
        "updated_at": now,
    }
    result = await _db()[COLLECTION].insert_one(doc)
    doc["_id"] = result.inserted_id
    await record_activity(
        user,
        "Generacion Postman",
        "postman",
        f"Analizo fuentes para Postman: {doc['project_name']}",
        {
            "draft_id": str(result.inserted_id),
            "fuentes": [source["name"] for source in sources if not source.get("is_cases_file")],
            "archivo_casos": case_source["name"] if case_source else "",
            "endpoints": len(model["endpoints"]),
            "casos": len(model["test_cases"]),
            "warnings": len(model["warnings"]),
        },
    )
    return {"draft": _public(doc)}


@router.get("/drafts")
async def list_drafts(user: Dict[str, Any] = Depends(current_user)):
    _require_qa(user)
    query = {} if user.get("shared") else {"created_by": user["username"]}
    docs = await _db()[COLLECTION].find(query).sort("created_at", DESCENDING).to_list(100)
    return {"drafts": [_public(doc) for doc in docs]}


@router.get("/drafts/{draft_id}")
async def get_draft(draft_id: str, user: Dict[str, Any] = Depends(current_user)):
    _require_qa(user)
    return {"draft": _public(await _get_draft(draft_id, user))}


@router.put("/drafts/{draft_id}")
async def update_draft(draft_id: str, payload: DraftUpdate, user: Dict[str, Any] = Depends(current_user)):
    _require_qa(user)
    doc = await _get_draft(draft_id, user)
    model = dict(doc.get("model") or {})
    update_data = payload.model_dump(exclude_none=True)
    model.update(update_data)
    model["project_name"] = doc.get("project_name") or model.get("project_name") or "Proyecto API"
    model["validation"] = validate_model(model)
    model["readiness"] = build_readiness(model)
    await _db()[COLLECTION].update_one({"_id": doc["_id"]}, {"$set": {"model": model, "updated_at": _now()}})
    doc["model"] = model
    await record_activity(
        user,
        "Revision Postman",
        "postman",
        f"Actualizo revision de Postman: {doc.get('project_name', 'Proyecto API')}",
        {
            "draft_id": draft_id,
            "project_name": doc.get("project_name"),
            "endpoints": len(model.get("endpoints", [])),
            "casos": len(model.get("test_cases", [])),
            "asociaciones": len([item for item in model.get("associations", []) if item.get("endpoint_id")]),
        },
    )
    return {"draft": _public(doc)}


def _file_payload(kind: str, model: Dict[str, Any]) -> Tuple[str, bytes, str]:
    if kind == "collection":
        return "collection.json", json.dumps(build_collection(model), ensure_ascii=False, indent=2).encode("utf-8"), "application/json"
    if kind == "environment":
        return "environment.json", json.dumps(build_environment(model), ensure_ascii=False, indent=2).encode("utf-8"), "application/json"
    if kind == "readme":
        return "README.md", build_readme(model).encode("utf-8"), "text/markdown"
    if kind == "traceability":
        return "traceability.md", build_traceability(model).encode("utf-8"), "text/markdown"
    raise HTTPException(status_code=404, detail="Archivo no soportado.")


@router.get("/drafts/{draft_id}/download/{kind}")
async def download_generated(kind: str, draft_id: str, user: Dict[str, Any] = Depends(current_user)):
    _require_qa(user)
    doc = await _get_draft(draft_id, user)
    model = doc.get("model") or {}
    label = "Collection y environment" if kind == "bundle" else "ZIP completo" if kind == "zip" else kind
    await record_activity(
        user,
        "Descarga Postman",
        "postman",
        f"Descargo {label} de Postman: {doc.get('project_name', 'Proyecto API')}",
        {
            "draft_id": draft_id,
            "project_name": doc.get("project_name"),
            "tipo_descarga": kind,
            "endpoints": len(model.get("endpoints", [])),
            "casos": len(model.get("test_cases", [])),
        },
    )
    if kind in {"zip", "bundle"}:
        output = BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
            kinds = ("collection", "environment") if kind == "bundle" else ("collection", "environment", "readme", "traceability")
            for item_kind in kinds:
                filename, content, _ = _file_payload(item_kind, model)
                zf.writestr(filename, content)
        output.seek(0)
        return StreamingResponse(
            output,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{OUTPUT_NAME if kind == "zip" else "postman_collection_environment"}.zip"'},
        )
    filename, content, media_type = _file_payload(kind, model)
    if media_type.startswith("text"):
        return PlainTextResponse(content.decode("utf-8"), media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    return Response(content, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/drafts/{draft_id}/validate")
async def validate_draft(draft_id: str, user: Dict[str, Any] = Depends(current_user)):
    _require_qa(user)
    doc = await _get_draft(draft_id, user)
    validation = validate_model(doc.get("model") or {})
    await _db()[COLLECTION].update_one({"_id": doc["_id"]}, {"$set": {"model.validation": validation, "updated_at": _now()}})
    await record_activity(
        user,
        "Validacion Postman",
        "postman",
        f"Valido coleccion Postman: {doc.get('project_name', 'Proyecto API')}",
        {
            "draft_id": draft_id,
            "project_name": doc.get("project_name"),
            "valid": validation.get("valid"),
            "errores": len(validation.get("errors", [])),
            "advertencias": len(validation.get("warnings", [])),
        },
    )
    return {"validation": validation}
