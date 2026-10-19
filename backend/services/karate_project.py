from __future__ import annotations

import io
import json
import re
import zipfile
from collections import OrderedDict
from typing import Any, Dict, List, Tuple

from services.postman_generator import CREDENTIAL_RE, SECRET_RE, _build_artifacts


PLACEHOLDER = re.compile(r"\{\{([A-Za-z0-9_.-]+)\}\}")
STATUS = re.compile(r"(?i)(?:status(?:\s+HTTP)?|HTTP|respuesta|response)[^0-9]{0,12}([1-5][0-9]{2})|(?:esperad[oa]|expected)[^0-9]{0,20}([1-5][0-9]{2})")


def _slug(value: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", str(value or "api").lower())).strip("-") or "api"


def _line(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:180]


def _expr(value: Any) -> str:
    text = str(value if value is not None else "")
    pieces: List[str] = []
    cursor = 0
    for match in PLACEHOLDER.finditer(text):
        if match.start() > cursor:
            pieces.append(json.dumps(text[cursor:match.start()], ensure_ascii=False))
        pieces.append("vars[" + json.dumps(match.group(1)) + "]")
        cursor = match.end()
    if cursor < len(text):
        pieces.append(json.dumps(text[cursor:], ensure_ascii=False))
    return " + ".join(pieces) if pieces else json.dumps(text, ensure_ascii=False)


def _json_body(raw: str) -> Any:
    # Postman permits unquoted numeric placeholders; JSON fixtures need quoted Karate expressions.
    numeric = set()
    quoted_parts = []
    cursor = 0
    in_string = False
    escaped = False
    while cursor < len(raw):
        char = raw[cursor]
        if char == '"' and not escaped:
            in_string = not in_string
        if not in_string:
            match = PLACEHOLDER.match(raw, cursor)
            if match:
                numeric.add(match.group(1))
                quoted_parts.append('"' + match.group(0) + '"')
                cursor = match.end()
                escaped = False
                continue
        quoted_parts.append(char)
        escaped = char == "\\" and not escaped
        if char != "\\":
            escaped = False
        cursor += 1
    quoted = "".join(quoted_parts)
    parsed = json.loads(quoted)

    def walk(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: walk(item) for key, item in value.items()}
        if isinstance(value, list):
            return [walk(item) for item in value]
        if isinstance(value, str) and PLACEHOLDER.search(value):
            found = PLACEHOLDER.fullmatch(value)
            if found and found.group(1) in numeric:
                return "#(+vars[" + json.dumps(found.group(1)) + "])"
            return "#(" + _expr(value) + ")"
        return value

    return walk(parsed)


def _expected_status(case: Dict[str, Any], endpoint: Dict[str, Any]) -> str:
    explicit = str(case.get("expected_status") or "")
    if re.fullmatch(r"[1-5][0-9]{2}", explicit):
        return explicit
    expected = str(case.get("expected_result") or "")
    match = STATUS.search(expected)
    if match:
        return next(value for value in match.groups() if value)
    if not case:
        documented = [str(item.get("status")) for item in endpoint.get("responses", [])]
        return next((code for code in documented if re.fullmatch(r"2[0-9]{2}", code)), "")
    return ""


def _negative_case(case: Dict[str, Any]) -> bool:
    text = " ".join(str(case.get(key) or "") for key in ("name", "description", "expected_result", "test_type"))
    return bool(re.search(r"(?i)\b(?:error|negative|negativo|rechaz\w*|invalid\w*|inv[aá]lid\w*|sin\s+\w+|omitir|no\s+autorizado)\b", text))


def _sensitive_response(value: Any) -> bool:
    if isinstance(value, dict):
        return any(CREDENTIAL_RE.search(str(key)) or _sensitive_response(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_sensitive_response(item) for item in value)
    return isinstance(value, str) and bool(SECRET_RE.search(value))


def _request_lines(item: Dict[str, Any], case: Dict[str, Any], endpoint: Dict[str, Any],
                   fixtures: Dict[str, str], extras: Dict[str, Dict[str, Any]], checks: List[str],
                   basename: str) -> List[str]:
    request = item.get("request") or {}
    url = request.get("url") or {}
    host = (url.get("host") or ["{{baseUrl}}"]) if isinstance(url, dict) else ["{{baseUrl}}"]
    base = str(host[0] or "{{baseUrl}}")
    lines = ["    Given url " + _expr(base)]
    path = url.get("path") or []
    if path:
        lines.append("    And path " + ", ".join(_expr(part) for part in path))
    query = [entry for entry in url.get("query") or [] if entry.get("key") and not entry.get("disabled")]
    auth = request.get("auth") or {}
    mode = str(auth.get("type") or "").lower()
    headers = {str(entry.get("key")): str(entry.get("value") or "") for entry in request.get("header") or []
               if entry.get("key") and not entry.get("disabled")}
    if mode == "bearer":
        token = next((entry.get("value") for entry in auth.get("bearer") or [] if entry.get("key") == "token"), "{{bearerToken}}")
        headers["Authorization"] = "Bearer " + str(token)
    elif mode == "apikey":
        fields = {str(entry.get("key")): str(entry.get("value") or "") for entry in auth.get("apikey") or []}
        key = fields.get("key") or "X-API-Key"
        value = fields.get("value") or "{{apiKey}}"
        if fields.get("in") == "query":
            query.append({"key": key, "value": value})
        else:
            headers[key] = value
    elif mode not in {"", "inherit", "noauth"}:
        variable = "authorizationHeader"
        extras[variable] = {"key": variable, "value": "", "sensitive": True,
                            "reason": f"Cabecera Authorization para autenticación {mode}", "context": item.get("name", ""), "queryable": False}
        headers["Authorization"] = "{{authorizationHeader}}"
        checks.append(f"{item.get('name')}: completar Authorization para el modo {mode}; no se migró su flujo de autenticación.")
    for entry in query:
        key = str(entry["key"])
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", key):
            lines.append("    And param " + key + " = " + _expr(entry.get("value")))
        else:
            checks.append(f"{item.get('name')}: revisar el nombre no estándar del parámetro {key}.")
    body = request.get("body") or {}
    if body.get("mode") == "raw" and (body.get("options") or {}).get("raw", {}).get("language") == "json":
        headers.setdefault("Content-Type", "application/json")
    for key, value in headers.items():
        if re.fullmatch(r"[A-Za-z0-9!#$%&'*+.^_`|~-]+", key):
            lines.append("    And header " + key + " = " + _expr(value))
        else:
            checks.append(f"{item.get('name')}: revisar el nombre no estándar del header {key}.")
    mode_body = body.get("mode")
    if mode_body == "raw" and str(body.get("raw") or "").strip():
        raw = str(body["raw"])
        try:
            if "#(" in raw:
                raise ValueError("Se omite un marcador Karate proveniente de la fuente.")
            parsed = _json_body(raw)
            filename = f"data/{basename}.json"
            fixtures[filename] = json.dumps(parsed, ensure_ascii=False, indent=2) + "\n"
            lines.append(f"    And request read('classpath:{filename}')")
        except (TypeError, ValueError):
            lines.append("    And request " + _expr(raw))
            checks.append(f"{item.get('name')}: body sin formato JSON; revisar Content-Type y formato enviado.")
    elif mode_body == "urlencoded":
        fields = [entry for entry in body.get("urlencoded") or [] if entry.get("key") and not entry.get("disabled")]
        for entry in fields:
            key = str(entry["key"])
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", key):
                lines.append("    And form field " + key + " = " + _expr(entry.get("value")))
            else:
                checks.append(f"{item.get('name')}: revisar el nombre no estándar del campo {key}.")
    elif mode_body in {"formdata", "file"}:
        entries = body.get("formdata") or [] if mode_body == "formdata" else [{"key": "file", "type": "file"}]
        for entry in entries:
            key = str(entry.get("key") or "file")
            if entry.get("type") == "file":
                var = _slug(basename).replace("-", "_") + "_filePath"
                extras[var] = {"key": var, "value": "", "sensitive": False,
                               "reason": f"Ruta local del archivo multipart {key}", "context": item.get("name", ""), "queryable": False}
                lines.append("    And multipart file " + key + " = { read: vars[" + json.dumps(var) + "] }")
                checks.append(f"{item.get('name')}: indicar ruta local de archivo para el campo {key}.")
            else:
                lines.append("    And multipart field " + key + " = " + _expr(entry.get("value")))
    lines.append("    When method " + str(request.get("method") or "GET").lower())
    status = _expected_status(case, endpoint)
    if status:
        lines.append("    Then status " + status)
    else:
        family = 4 if _negative_case(case) else 2
        lines.append(f"    Then assert responseStatus >= {family}00 && responseStatus < {family + 1}00")
        checks.append(f"{item.get('name')}: confirmar el status HTTP exacto; se valida solo la familia {family}xx.")
    expected = case.get("expected_response")
    if expected:
        try:
            value = json.loads(expected) if isinstance(expected, str) else expected
            if "#(" in json.dumps(value, ensure_ascii=False) or _sensitive_response(value):
                checks.append(f"{item.get('name')}: revisar el resultado esperado; se omitió una expresión Karate o un dato sensible.")
            elif isinstance(value, (dict, list)):
                lines.append("    And match response contains " + json.dumps(value, ensure_ascii=False, separators=(",", ":")))
            else:
                checks.append(f"{item.get('name')}: revisar manualmente la respuesta esperada no estructurada.")
        except (TypeError, ValueError):
            checks.append(f"{item.get('name')}: respuesta esperada no parseable como JSON.")
    narrative = STATUS.sub("", str(case.get("expected_result") or ""))
    if re.sub(r"(?i)\b(?:http|status|c[oó]digo|resultado|respuesta|esperad[oa]|expected)\b|[^A-Za-zÀ-ÿ0-9]", "", narrative):
        if not expected:
            checks.append(f"{item.get('name')}: revisar el resultado de negocio descrito en el caso; no se generó una aserción para ese texto.")
    if any(event.get("listen") == "prerequest" for event in item.get("event") or []):
        checks.append(f"{item.get('name')}: revisar lógica de pre-request de Postman; no se ejecuta en Karate.")
    if len([event for event in item.get("event") or [] if event.get("listen") == "test"]) > 1:
        checks.append(f"{item.get('name')}: revisar scripts de test originales de Postman; se generaron solo las aserciones documentadas.")
    return lines


def build_karate_project(model: Dict[str, Any], project_name: str) -> Tuple[bytes, Dict[str, Any]]:
    collection, environment, postman_readiness = _build_artifacts(model, include_case_refs=True)
    endpoints = {entry.get("id"): entry for entry in model.get("endpoints", [])}
    cases = {entry.get("id"): entry for entry in model.get("test_cases", [])}
    variables = {entry["key"]: entry for entry in environment.get("values", [])}
    extras: Dict[str, Dict[str, Any]] = {}
    checks: List[str] = [str(item).replace("en Postman", "en Karate") for item in postman_readiness.get("manual_checks", [])
                         if "confirmar el status HTTP exacto" not in str(item)]
    fixtures: Dict[str, str] = {}
    groups: Dict[str, List[str]] = OrderedDict()
    used_by_group: Dict[str, set] = {}
    counts: Dict[str, int] = {}
    scenario_count = 0
    for folder in collection.get("item", []):
        for item in folder.get("item", []):
            endpoint = endpoints.get(item.get("_qa_endpoint_id")) or {}
            case = cases.get(item.get("_qa_case_id")) or {}
            method = str((item.get("request") or {}).get("method") or "GET").lower()
            stem = _slug(method + "-" + str(endpoint.get("path") or endpoint.get("name") or "request"))[:90]
            group_id = str(endpoint.get("id") or stem)
            if group_id not in groups:
                groups[group_id] = []
                used_by_group[group_id] = set()
            counts[group_id] = counts.get(group_id, 0) + 1
            basename = _slug(group_id)[:55] + "-" + str(counts[group_id])
            title = _line(item.get("name") or f"Caso {scenario_count + 1}")
            request_lines = _request_lines(item, case, endpoint, fixtures, extras, checks, basename)
            vars_used = set(PLACEHOLDER.findall(json.dumps(item.get("request") or {}, ensure_ascii=False)))
            vars_used.update(re.findall(r'vars\["([A-Za-z0-9_.-]+)"\]', "\n".join(request_lines)))
            used_by_group[group_id].update(vars_used)
            preflight = ["    * if (vars[" + json.dumps(key) + "] == null || vars[" + json.dumps(key) + "] === '') karate.fail(" + json.dumps("Falta dato para Karate: " + key, ensure_ascii=False) + ")"
                         for key in sorted(vars_used)]
            groups[group_id].append("  Scenario: " + title + "\n" + "\n".join(preflight + request_lines))
            scenario_count += 1
    if not scenario_count:
        raise ValueError("No hay casos asociados a endpoints para generar escenarios Karate.")
    for key, entry in extras.items():
        variables[key] = {"key": key, "value": "", "type": "secret" if entry["sensitive"] else "default",
                          "description": entry["reason"]}
    missing = {entry["key"]: entry for entry in postman_readiness.get("missing_data", [])}
    for used in used_by_group.values():
        for key in used:
            entry = variables.get(key)
            if not entry:
                entry = {"key": key, "value": "", "type": "default", "description": "Variable usada por el request"}
                variables[key] = entry
            if entry.get("value") in (None, "") and key not in missing:
                extra = extras.get(key) or {}
                missing[key] = {"key": key, "reason": extra.get("reason") or entry.get("description") or "Dato requerido por el request",
                                "context": extra.get("context") or "", "queryable": False}
    files: Dict[str, str] = {}
    used_names = set()
    for group_id, scenarios in groups.items():
        endpoint = endpoints.get(group_id) or {}
        stem = _slug(str(endpoint.get("method") or "GET") + "-" + str(endpoint.get("path") or endpoint.get("name") or group_id))[:90]
        name = stem
        suffix = 2
        while name in used_names:
            name = f"{stem}-{suffix}"
            suffix += 1
        used_names.add(name)
        path = f"src/test/java/features/{name}.feature"
        background = ["  Background:", "    * def vars = karate.get('vars')"]
        files[path] = "Feature: " + _line(project_name) + " - " + _line(endpoint.get("name") or name) + "\n\n" + "\n".join(background) + "\n\n" + "\n\n".join(scenarios) + "\n"
    data = {}
    for key, entry in variables.items():
        value = entry.get("value", "")
        if entry.get("type") == "secret":
            value = ""
        elif isinstance(value, str) and ("#(" in value or SECRET_RE.search(value) or re.search(r"(?i)https?://[^/@\s]+:[^/@\s]+@", value)):
            value = ""
            missing[key] = {"key": key, "reason": "Valor sensible o ejecutable omitido; cargar un dato seguro",
                            "context": "", "queryable": False}
            checks.append(f"{key}: se omitió un valor sensible o una expresión Karate ejecutable de la fuente.")
        data[key] = value
    files["src/test/java/qa-data.json"] = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    files["src/test/java/karate-config.js"] = (
        "function fn() {\n"
        "  var vars = karate.read('classpath:qa-data.json');\n"
        "  Object.keys(vars).forEach(function (key) {\n"
        "    var override = karate.properties[key] || java.lang.System.getenv(key);\n"
        "    if (override !== null && override !== undefined && override !== '') vars[key] = override;\n"
        "  });\n"
        "  return { vars: vars };\n"
        "}\n"
    )
    files["src/test/java/logback-test.xml"] = (
        '<configuration>\n'
        '  <appender name="STDOUT" class="ch.qos.logback.core.ConsoleAppender">\n'
        '    <encoder><pattern>%level %logger{24} - %msg%n</pattern></encoder>\n'
        '  </appender>\n'
        '  <root level="WARN"><appender-ref ref="STDOUT" /></root>\n'
        '</configuration>\n'
    )
    files["src/test/java/qa/generated/ApiTest.java"] = (
        "package qa.generated;\n\n"
        "import com.intuit.karate.junit5.Karate;\n"
        "\nclass ApiTest {\n"
        "  @Karate.Test Karate testApi() { return Karate.run(\"classpath:features\"); }\n"
        "}\n"
    )
    slug = _slug(project_name)
    files["pom.xml"] = f'''<project xmlns="http://maven.apache.org/POM/4.0.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>qa.generated</groupId><artifactId>{slug}</artifactId><version>1.0.0</version>
  <properties><maven.compiler.source>17</maven.compiler.source><maven.compiler.target>17</maven.compiler.target><project.build.sourceEncoding>UTF-8</project.build.sourceEncoding><karate.version>1.5.2</karate.version></properties>
  <dependencies><dependency><groupId>io.karatelabs</groupId><artifactId>karate-junit5</artifactId><version>${{karate.version}}</version><scope>test</scope></dependency></dependencies>
    <build><testResources><testResource><directory>src/test/java</directory><includes><include>**/*.feature</include><include>**/*.js</include><include>**/*.json</include><include>**/*.xml</include></includes></testResource></testResources>
    <plugins><plugin><groupId>org.apache.maven.plugins</groupId><artifactId>maven-surefire-plugin</artifactId><version>3.2.5</version></plugin></plugins>
  </build>
</project>
'''
    files.update({"src/test/java/" + name: content for name, content in fixtures.items()})
    query_request = str(postman_readiness.get("query_request") or "").replace("para la collection ", "para el proyecto Karate ")
    readiness = {**postman_readiness, "scenario_count": scenario_count, "feature_count": len(groups),
                 "missing_data": list(missing.values()), "manual_checks": list(dict.fromkeys(checks))}
    readiness["query_request"] = query_request
    pending_lines = [f"- {entry['key']}: {entry.get('reason', '')}" for entry in readiness["missing_data"]]
    check_lines = ["- " + check for check in readiness["manual_checks"]]
    files["README.md"] = "\n".join([
        "# Proyecto Karate API", "", "## Ejecutar", "",
        "1. Instalar Java 17+ y Maven.",
        "2. Completar las variables pendientes en `src/test/java/qa-data.json` (sin secretos).",
        "3. Definir credenciales como variables de entorno con el nombre exacto de la variable pendiente.",
        "4. Ejecutar `mvn test` desde la carpeta del proyecto. No se envian requests durante la generacion.",
        "", "Los escenarios quedan agrupados por endpoint y siguen los casos asociados del Excel, si se adjunto.",
        "La app no ejecuta consultas SQL ni obtiene datos de la base; las queries deben revisarse y ejecutarse manualmente.",
        "", "## Datos pendientes", "", *(pending_lines or ["Ninguno detectado."]),
        "", "## Revisiones manuales", "", *(check_lines or ["Ninguna detectada."]), "",
        "No guardar claves, tokens ni datos personales en Git. Revisar los reportes antes de compartirlos: pueden contener datos de requests y respuestas.", "",
    ])
    files[".gitignore"] = "target/\nsrc/test/java/qa-data.json\n"
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return output.getvalue(), readiness
