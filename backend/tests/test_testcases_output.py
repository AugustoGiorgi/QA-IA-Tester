import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.ai import AIResponseError  # noqa: E402
from services.testcases import case_rows_from_markdown  # noqa: E402


class TestcaseOutputTests(unittest.TestCase):
    def test_valid_cases_keep_the_expected_excel_columns(self):
        markdown = (
            "| número | objetivo de la prueba | funcionalidad | resultado esperado | observaciones |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| 1 | Validar vigencia | Pólizas | La póliza está vigente | Sin observaciones |"
        )
        rows = case_rows_from_markdown(markdown)
        self.assertEqual(rows[0]["Objetivo"], "Validar vigencia")
        self.assertEqual(rows[0]["Caso de Prueba"], "1")

    def test_invalid_ai_output_does_not_create_a_placeholder_case(self):
        for markdown in ("La IA no generó casos", "| nombre | valor |\n| --- | --- |\n| caso | algo |", "| número | objetivo de la prueba | funcionalidad | resultado esperado | observaciones |\n| --- | --- | --- | --- | --- |"):
            with self.subTest(markdown=markdown), self.assertRaises(AIResponseError):
                case_rows_from_markdown(markdown)
