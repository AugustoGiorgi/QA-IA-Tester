import asyncio
import http.server
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bson import ObjectId
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.karate_generator import download_karate, generate_karate  # noqa: E402
from services.karate_project import build_karate_project  # noqa: E402
from services.postman_generator import _read_upload, build_intermediate_model  # noqa: E402


class Upload:
    def __init__(self, filename, data):
        self.filename = filename
        self.data = data

    async def read(self, size=-1):
        return self.data if size < 0 else self.data[:size]


def source(name, content, cases=False):
    return {"name": name, "extension": Path(name).suffix, "text": content,
            "size": len(content), "warning": "", "is_cases_file": cases}


def build(sources):
    sources = [asyncio.run(_read_upload(Upload(item["name"], item["text"].encode("utf-8"))))
               if item.get("is_cases_file") else item for item in sources]
    model = build_intermediate_model(sources)
    model["project_name"] = "Regresión API"
    payload, readiness = build_karate_project(model, model["project_name"])
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        files = {name: archive.read(name).decode("utf-8") for name in archive.namelist()}
    return model, files, readiness


class KarateProjectTests(unittest.TestCase):
    def test_exact_excel_cases_and_case_specific_data(self):
        request = {"request": {"method": "POST", "url": "https://api.test/polizas/:policyNumber?branch={{branch}}",
                               "auth": {"type": "bearer", "bearer": [{"key": "token", "value": "raw-secret-token"}]},
                               "body": {"mode": "raw", "raw": '{"amount":120,"active":true}'}}}
        cases = ("ID;Nombre del caso;Endpoint;Datos de prueba;Resultado esperado\n"
                 "CP-01;Alta válida;POST /polizas/:policyNumber;policyNumber=123, amount=150, branch=42;HTTP 201\n"
                 "CP-02;Alta inválida;POST /polizas/:policyNumber;policyNumber=999, amount=0, branch=43;HTTP 400\n")
        _, files, readiness = build([source("api.json", json.dumps(request)), source("casos.csv", cases, True)])
        features = [value for name, value in files.items() if name.endswith(".feature")]
        self.assertEqual(len(features), 1)
        self.assertEqual(features[0].count("Scenario:"), 2)
        self.assertIn("CP-01 - Alta válida", features[0])
        self.assertIn("CP-02 - Alta inválida", features[0])
        self.assertIn("Then status 201", features[0])
        self.assertIn("Then status 400", features[0])
        self.assertIn('vars["cp_01_policyNumber"]', features[0])
        self.assertIn('vars["cp_02_policyNumber"]', features[0])
        self.assertIn('Bearer ', features[0])
        self.assertEqual(readiness["scenario_count"], 2)
        self.assertEqual(readiness["request_count"], 2)
        self.assertNotIn("raw-secret-token", "\n".join(files.values()))
        self.assertEqual(json.loads(files["src/test/java/qa-data.json"])["bearerToken"], "")
        fixtures = [json.loads(value) for name, value in files.items() if name.startswith("src/test/java/data/")]
        self.assertEqual(len(fixtures), 2)
        self.assertTrue(any(value["amount"].startswith("#(+vars[") for value in fixtures))

    def test_missing_policy_routes_to_query_without_credentials(self):
        request = {"request": {"method": "GET", "url": "{{baseUrl}}/polizas/:policyNumber?token={{token}}"}}
        _, files, readiness = build([source("polizas.json", json.dumps(request))])
        self.assertIn("policyNumber", readiness["query_request"])
        self.assertNotIn("token:", readiness["query_request"])
        self.assertIn("No ejecutes la consulta", readiness["query_request"])
        self.assertIn("policyNumber", files["README.md"])
        self.assertIn("src/test/java/qa-data.json", files[".gitignore"])

    def test_two_hosts_create_two_features_with_distinct_base_urls(self):
        sources = [source(f"{name}.json", json.dumps({"request": {"method": "GET", "url": f"https://{host}/health"}}))
                   for name, host in (("one", "api.one.test"), ("two", "api.two.test"))]
        _, files, readiness = build(sources)
        features = [value for name, value in files.items() if name.endswith(".feature")]
        self.assertEqual(len(features), 2)
        self.assertTrue(any('vars["baseUrl2"]' in feature for feature in features))
        self.assertEqual(readiness["feature_count"], 2)

    def test_body_with_embedded_placeholder_and_karate_marker_is_not_executed(self):
        request = {"request": {"method": "POST", "url": "https://api.test/notes", "body": {
            "mode": "raw", "raw": '{"description":"ref-{{ref}}","unsafe":"#(Java.type(\\"java.lang.System\\"))"}'}}}
        _, files, readiness = build([source("notes.json", json.dumps(request))])
        features = [value for name, value in files.items() if name.endswith(".feature")]
        self.assertTrue(any("And request read(" in feature for feature in features))
        self.assertNotIn("Java.type", "\n".join(files.values()))
        self.assertTrue(any("expresión Karate ejecutable" in item for item in readiness["manual_checks"]))

    @unittest.skipUnless(os.environ.get("RUN_KARATE_SMOKE") == "1" and shutil.which("mvn"), "Requires local Maven smoke test")
    def test_generated_project_runs_get_and_post_against_local_server(self):
        received = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                received.append(("GET", self.path, None, self.headers.get("Authorization")))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"ok":true}')

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                received.append(("POST", self.path, body, self.headers.get("Authorization")))
                self.send_response(201)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"saved":true}')

            def log_message(self, *_args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = f"http://127.0.0.1:{server.server_port}"
        collection = {"info": {"name": "Smoke", "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},
                      "item": [{"name": "Read", "request": {"method": "GET", "url": base + "/health?branch={{branch}}"}},
                               {"name": "Create", "request": {"method": "POST", "url": base + "/orders",
                                                             "auth": {"type": "bearer", "bearer": [{"key": "token", "value": "source-secret-value"}]},
                                                             "body": {"mode": "raw", "raw": '{"amount":12,"active":true}'}}}]}
        cases = ("ID;Nombre del caso;Endpoint;Datos de prueba;Resultado esperado\n"
                 "CP-01;Health;GET /health;branch=8;HTTP 200\n"
                 "CP-02;Create;POST /orders;amount=15;HTTP 201\n")
        try:
            model, _, _ = build([source("api.json", json.dumps(collection)), source("cases.csv", cases, True)])
            payload, _ = build_karate_project(model, "Smoke")
            with tempfile.TemporaryDirectory(prefix="karate-generated-") as directory:
                with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                    archive.extractall(directory)
                result = subprocess.run([shutil.which("mvn"), "-q", "test"], cwd=directory,
                                        capture_output=True, text=True, timeout=150,
                                        env={**os.environ, "bearerToken": "smoke-token"})
                self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-3000:])
                reports = list(Path(directory, "target", "surefire-reports").glob("*.txt"))
                self.assertTrue(reports)
                self.assertIn("Tests run: 2, Failures: 0", reports[0].read_text())
            self.assertTrue(any(method == "GET" and "branch=8" in path for method, path, _, _ in received), received)
            self.assertTrue(any(method == "POST" and body == {"amount": 15, "active": True} and auth == "Bearer smoke-token"
                                for method, _, body, auth in received), received)
        finally:
            server.shutdown()
            server.server_close()


class KarateRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_route_generates_downloadable_zip(self):
        request = json.dumps({"request": {"method": "GET", "url": "https://api.test/polizas/:policyNumber"}}).encode()
        cases = b"ID;Nombre del caso;Endpoint;Resultado esperado\nCP-01;Consultar poliza;GET /polizas/:policyNumber;HTTP 200\n"
        collection = SimpleNamespace(insert_one=AsyncMock(return_value=SimpleNamespace(inserted_id=ObjectId())))
        user = {"username": "shared", "role": "qa", "shared": True}
        with patch("services.karate_generator._db", return_value={"KarateGenerationDrafts": collection}), \
                patch("services.karate_generator.record_activity", new=AsyncMock()):
            result = await generate_karate(files=[Upload("api.json", request)], cases_file=Upload("casos.csv", cases),
                                           manual_text="", project_name="Pólizas QA", user=user)
        self.assertEqual(result["draft"]["model"]["readiness"]["scenario_count"], 1)
        collection.insert_one.assert_awaited_once()
        doc = {"_id": ObjectId(result["draft"]["id"]), "project_name": "Pólizas QA", "model": result["draft"]["model"]}
        with patch("services.karate_generator._load_draft", new=AsyncMock(return_value=doc)), \
                patch("services.karate_generator.record_activity", new=AsyncMock()):
            response = await download_karate(result["draft"]["id"], user)
        payload = b"".join([chunk async for chunk in response.body_iterator])
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            self.assertIn("pom.xml", archive.namelist())
            self.assertEqual(len([name for name in archive.namelist() if name.endswith(".feature")]), 1)

    async def test_unmatched_case_rejected(self):
        request = json.dumps({"request": {"method": "GET", "url": "https://api.test/clientes"}}).encode()
        cases = b"ID;Nombre del caso;Endpoint;Resultado esperado\nCP-01;Emitir poliza;POST /polizas;HTTP 201\n"
        with self.assertRaises(HTTPException) as raised:
            await generate_karate(files=[Upload("api.json", request)], cases_file=Upload("casos.csv", cases),
                                  manual_text="", project_name="QA", user={"username": "shared", "role": "qa", "shared": True})
        self.assertEqual(raised.exception.status_code, 422)
        self.assertIn("CP-01", raised.exception.detail)


if __name__ == "__main__":
    unittest.main()
