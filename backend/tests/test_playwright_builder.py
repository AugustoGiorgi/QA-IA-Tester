import json
import os
import sys
import unittest
import zipfile
from io import BytesIO
from pathlib import Path

os.environ.setdefault("OPENAI_API_KEY", "test-only")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openpyxl import Workbook
from services.playwright_builder import DownloadIn, _build_zip, _parse_result, _safe_text, _xlsx_text


class PlaywrightBuilderTests(unittest.TestCase):
    def test_unknown_selectors_remain_explicit_and_expected_assertions_are_not_invented(self):
        result = _parse_result(json.dumps({
            "cases": [{
                "id": "CP-01",
                "title": "Alta",
                "source_reference": "casos.docx",
                "steps": [
                    {"name": "Completar usuario", "instruction": "Ingresar el usuario indicado", "action_type": "fill", "target": "campo usuario", "selector": "", "data_key": "username", "value": "qa"},
                    {"name": "Comprobar", "instruction": "Verificar que finalizó", "action_type": "assert", "target": "mensaje", "selector": "", "expected": ""},
                ],
            }],
            "test_data": {"username": "qa"},
            "manual_checks": [],
        }))
        self.assertEqual(result["selectors"]["campo_usuario"], "TODO_SELECTOR: campo usuario")
        self.assertIn("selector", result["manual_checks"][0].lower())
        self.assertEqual(result["cases"][0]["steps"][1]["action_type"], "manual")
        self.assertTrue(any("resultado esperado" in item.lower() for item in result["manual_checks"]))

    def test_a_selector_is_confirmed_only_when_it_appears_in_technical_source(self):
        candidate = {"cases": [{"id": "CP-01", "title": "Guardar", "steps": [{
            "name": "Guardar", "instruction": "Guardar", "action_type": "click", "target": "guardar", "selector": "button#save"
        }]}], "test_data": {}, "manual_checks": []}
        inferred = _parse_result(json.dumps(candidate))
        evidenced = _parse_result(json.dumps(candidate), "codegen: page.locator('button#save').click()")
        self.assertTrue(inferred["selectors"]["guardar"].startswith("TODO_SELECTOR:"))
        self.assertEqual(evidenced["selectors"]["guardar"], "button#save")

    def test_sensitive_values_are_redacted_before_prompt_or_artifact(self):
        self.assertNotIn("not-a-real-secret", _safe_text("password=not-a-real-secret"))

    def test_workbook_rows_become_source_cases_one_for_one(self):
        book = Workbook()
        sheet = book.active
        sheet.title = "Casos"
        sheet.append(["ID", "Escenario", "Pasos", "Resultado esperado"])
        sheet.append(["CP-01", "Alta", "Crear registro", "Se guarda"])
        sheet.append(["CP-02", "Consulta", "Buscar registro", "Aparece"])
        output = BytesIO()
        book.save(output)
        text, cases = _xlsx_text(output.getvalue(), "casos.xlsx")
        self.assertEqual(len(cases), 2)
        self.assertIn("CP-01", text)
        self.assertEqual(cases[1]["Escenario"], "Consulta")

    def test_zip_contains_one_spec_per_case_and_secrets_only_as_env_variables(self):
        payload = DownloadIn(
            project_name="Alta QA",
            initial_url="https://qa.example.test",
            selectors={"boton_ingresar": "TODO_SELECTOR: botón ingresar"},
            test_data={"password": "TODO_COMPLETAR", "usuario": "qa-demo"},
            cases=[
                {"id": "CP-01", "title": "Login", "source_reference": "docx", "steps": [
                    {"name": "Ingresar", "instruction": "Autenticarse", "action_type": "manual", "target": "credenciales", "selector_key": ""}
                ]},
                {"id": "CP-02", "title": "Salir", "source_reference": "docx", "steps": []},
            ],
        )
        archive = zipfile.ZipFile(BytesIO(_build_zip(payload)))
        specs = [name for name in archive.namelist() if name.endswith(".spec.ts")]
        self.assertEqual(len(specs), 2)
        self.assertIn("tests/qa-data.json", archive.namelist())
        config_data = json.loads(archive.read("tests/qa-data.json"))
        self.assertEqual(config_data["testData"]["password"], "")
        self.assertIn("TEST_PASSWORD=", archive.read(".env.example").decode())
        self.assertIn("process.env.TEST_PASSWORD", archive.read(specs[0]).decode())
        self.assertNotIn("TODO_SELECTOR", " ".join(archive.read(path).decode() for path in specs))


if __name__ == "__main__":
    unittest.main()
