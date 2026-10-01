import json
import sys
import unittest
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.postman_generator import (  # noqa: E402
    _read_upload,
    build_collection,
    build_environment,
    build_intermediate_model,
    validate_model,
)


class AsyncUpload:
    def __init__(self, filename, data):
        self.filename = filename
        self._data = data

    async def read(self):
        return self._data


class PostmanGeneratorTests(unittest.TestCase):
    def test_openapi_users_domain_generates_collection(self):
        openapi = """
openapi: 3.0.0
servers:
  - url: https://api.example.test
paths:
  /users/{userId}:
    get:
      summary: Obtener usuario
      parameters:
        - name: userId
          in: path
          required: true
      responses:
        "200":
          description: Usuario encontrado
"""
        model = build_intermediate_model(
            [{"name": "users.yaml", "extension": ".yaml", "text": openapi, "size": len(openapi), "warning": ""}],
            "CASO 1: Obtener usuario existente\nResultado esperado: HTTP 200",
        )
        self.assertEqual(len(model["endpoints"]), 1)
        self.assertEqual(model["endpoints"][0]["method"], "GET")
        collection = build_collection(model)
        self.assertEqual(collection["info"]["schema"], "https://schema.getpostman.com/json/collection/v2.1.0/collection.json")
        self.assertEqual(collection["item"][0]["item"][0]["request"]["method"], "GET")

    def test_curl_inventory_domain_detects_secret_and_environment(self):
        text = "curl -X POST https://stock.example.test/items -H 'X-API-Key: abc123456789' -H 'Content-Type: application/json' -d '{\"sku\":\"A1\"}'"
        model = build_intermediate_model(
            [{"name": "inventory.txt", "extension": ".txt", "text": text, "size": len(text), "warning": ""}]
        )
        self.assertEqual(model["endpoints"][0]["method"], "POST")
        self.assertTrue(any(w["type"] == "possible_secret" for w in model["warnings"]))
        environment = build_environment(model)
        keys = {item["key"] for item in environment["values"]}
        self.assertIn("baseUrl", keys)

    def test_file_processing_domain_keeps_unmatched_case_visible(self):
        docs = """
CASO FP-01 Procesar archivo invalido
Pasos:
1. Enviar archivo corrupto al proceso batch.
Resultado esperado: Rechazo controlado.
"""
        model = build_intermediate_model(
            [{"name": "file-cases.md", "extension": ".md", "text": docs, "size": len(docs), "warning": ""}]
        )
        self.assertEqual(len(model["test_cases"]), 1)
        self.assertEqual(model["associations"][0]["confidence"], "Sin coincidencia")
        validation = validate_model(model)
        self.assertTrue(validation["valid"])
        self.assertIn("collection", json.dumps(build_collection(model)))

    def test_xlsx_cases_file_is_readable(self):
        from openpyxl import Workbook

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Casos"
        sheet.append(["ID", "Nombre", "Resultado esperado"])
        sheet.append(["CP-01", "Crear siniestro", "HTTP 201"])
        buffer = BytesIO()
        workbook.save(buffer)
        upload = AsyncUpload("casos.xlsx", buffer.getvalue())

        import asyncio

        source = asyncio.run(_read_upload(upload))
        self.assertEqual(source["extension"], ".xlsx")
        self.assertIn("Crear siniestro", source["text"])

    def test_cases_spreadsheet_exports_only_its_named_cases(self):
        from openpyxl import Workbook

        collection = {
            "info": {"name": "API", "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},
            "item": [
                {"name": "Autenticacion", "request": {"method": "POST", "url": {"raw": "https://api.example.test/api/auth"}}},
                {"name": "Consultar", "request": {"method": "GET", "url": {"raw": "https://api.example.test/api/customer"}}},
            ],
        }
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["Matriz de casos de prueba"])
        sheet.append(["ID", "Nombre del caso", "Endpoint", "Categoria", "Precondicion", "Datos de prueba", "Pasos", "Resultado esperado"])
        sheet.append(["CP-24", "CP-24 - Autenticacion sin contraseña", "POST /api/auth", "Negativo", "Usuario habilitado", "Omitir password", "Enviar request", "Rechazo controlado"])
        buffer = BytesIO()
        workbook.save(buffer)

        import asyncio

        collection_source = {"name": "collection.json", "extension": ".json", "text": json.dumps(collection), "size": 1, "warning": ""}
        cases_source = asyncio.run(_read_upload(AsyncUpload("casos.xlsx", buffer.getvalue())))
        model = build_intermediate_model([collection_source, cases_source])
        requests = build_collection(model)["item"][0]["item"]

        self.assertEqual(len(model["test_cases"]), 1)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["name"], "CP-24 - Autenticacion sin contraseña")
        self.assertEqual(requests[0]["request"]["method"], "POST")

    def test_endpoint_without_excel_gets_base_ok_and_base_error_cases(self):
        openapi = """
openapi: 3.0.0
paths:
  /polizas/{id}:
    post:
      summary: Actualizar poliza
      requestBody:
        content:
          application/json:
            example:
              estado: activa
      responses:
        "200":
          description: OK
"""
        model = build_intermediate_model(
            [{"name": "polizas.yaml", "extension": ".yaml", "text": openapi, "size": len(openapi), "warning": ""}]
        )
        generated = [case for case in model["test_cases"] if case.get("generated")]
        self.assertEqual(len(generated), 2)
        self.assertTrue(any("caso base OK" in case["name"] for case in generated))
        self.assertTrue(any("caso base con error" in case["name"] for case in generated))

    def test_business_rule_case_is_generated_only_when_documented(self):
        openapi = """
openapi: 3.0.0
paths:
  /cotizaciones:
    post:
      summary: Crear cotizacion
      description: Debe validar regla de negocio de monto limite y estado vigente.
      responses:
        "200":
          description: OK
"""
        model = build_intermediate_model(
            [{"name": "cotizaciones.yaml", "extension": ".yaml", "text": openapi, "size": len(openapi), "warning": ""}]
        )
        generated = [case for case in model["test_cases"] if case.get("generated")]
        self.assertEqual(len(generated), 3)
        self.assertTrue(any("logica de negocio" in case["name"] for case in generated))

    def test_postman_collection_repeated_requests_count_as_unique_endpoint(self):
        collection = {
            "info": {"schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},
            "item": [
                {"name": "CP-01", "item": [
                    {"name": "Consultar asegurado OK", "request": {"method": "GET", "url": {"raw": "{{baseUrl}}/api/v1/Global/consultarasegurado?cuit={{cuit}}"}}},
                ]},
                {"name": "CP-02", "item": [
                    {"name": "Consultar asegurado error", "request": {"method": "GET", "url": {"raw": "{{baseUrl}}/api/v1/Global/consultarasegurado?cuit={{cuitInvalido}}"}}},
                ]},
            ],
        }
        text = json.dumps(collection)
        model = build_intermediate_model(
            [{"name": "collection.json", "extension": ".json", "text": text, "size": len(text), "warning": ""}]
        )
        self.assertEqual(len(model["endpoints"]), 1)

    def test_postman_collection_variables_export_to_environment(self):
        collection = {
            "info": {"schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},
            "variable": [
                {"key": "baseUrl", "value": "https://api.example.test"},
                {"key": "accessToken", "value": "do-not-export-this", "type": "secret"},
            ],
            "item": [{"name": "Consultar", "request": {
                "method": "GET",
                "url": {"raw": "{{baseUrl}}/users/:userId?include=profile"},
            }}],
        }
        text = json.dumps(collection)
        model = build_intermediate_model([
            {"name": "api.postman_collection.json", "extension": ".json", "text": text, "size": len(text), "warning": ""}
        ])

        request_url = build_collection(model)["item"][0]["item"][0]["request"]["url"]["raw"]
        environment = {item["key"]: item for item in build_environment(model)["values"]}

        self.assertIn("{{userId}}", request_url)
        self.assertEqual(environment["baseUrl"]["value"], "https://api.example.test")
        self.assertEqual(environment["userId"]["value"], "")
        self.assertEqual(environment["accessToken"]["value"], "")


if __name__ == "__main__":
    unittest.main()
