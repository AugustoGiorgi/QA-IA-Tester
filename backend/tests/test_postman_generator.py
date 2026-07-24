import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.postman_generator import (  # noqa: E402
    build_collection,
    build_environment,
    build_intermediate_model,
    validate_model,
)


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


if __name__ == "__main__":
    unittest.main()
