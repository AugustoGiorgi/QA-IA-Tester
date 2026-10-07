import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docx import Document
from openpyxl import Workbook
from PIL import Image

from services.evidence_generator import build_evidence_docx, parse_spreadsheet


def workbook_bytes(rows):
    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


class EvidenceGeneratorTests(unittest.TestCase):
    def test_parser_maps_aliases_and_keeps_every_source_column(self):
        contents = workbook_bytes([
            ["Identificador", "Escenario", "Resultado de ejecución", "Esperado", "Resultado real", "Detalle del error", "Dato adicional"],
            ["CP-51", "Consulta comercial", "Error", "Debe responder 200", "Respondió 500", "Timeout reportado", "Valor original"],
        ])
        cases = parse_spreadsheet(contents, "casos.xlsx")
        self.assertEqual(len(cases), 1)
        case = cases[0]
        self.assertEqual(case["case_id"], "CP-51")
        self.assertEqual(case["name"], "Consulta comercial")
        self.assertEqual(case["status"], "Error")
        self.assertEqual(case["observations"], "Timeout reportado")
        self.assertEqual(case["source_fields"]["Dato adicional"], "Valor original")
        self.assertEqual(case["images"], [])

    def test_parser_does_not_invent_missing_execution_values(self):
        contents = workbook_bytes([
            ["ID caso", "Nombre del caso", "Resultado esperado", "Observaciones"],
            ["CP-01", "Validar alta", "Alta confirmada", None],
        ])
        case = parse_spreadsheet(contents, "casos.xlsx")[0]
        self.assertEqual(case["status"], "")
        self.assertEqual(case["actual_result"], "")
        self.assertEqual(case["observations"], "")

    def test_parser_keeps_zero_padding_from_excel_cell_format(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["ID caso", "Escenario"])
        sheet.append([51, "Consulta"])
        sheet["A2"].number_format = "0000"
        output = io.BytesIO()
        workbook.save(output)
        case = parse_spreadsheet(output.getvalue(), "casos.xlsx")[0]
        self.assertEqual(case["case_id"], "0051")

    def test_report_contains_summary_case_fields_and_embedded_image(self):
        image_buffer = io.BytesIO()
        Image.new("RGB", (320, 180), color=(35, 99, 235)).save(image_buffer, format="PNG")
        image_bytes = image_buffer.getvalue()
        session = {
            "project_name": "Proyecto de prueba",
            "requirement": "REQ-TEST",
            "environment": "QA",
            "source_filename": "casos.xlsx",
            "cases": [{
                "case_key": "case-1",
                "case_id": "CP-01",
                "name": "Alta comercial",
                "status": "Error",
                "source_fields": {
                    "ID": "CP-01",
                    "Caso": "Alta comercial",
                    "Estado": "Error",
                    "Detalle del error": "Respuesta no esperada",
                },
                "images": [{"id": "image-1", "title": "Respuesta del sistema", "caption": "Se observa el error", "category": "Error"}],
            }],
        }
        result = build_evidence_docx(session, {"image-1": image_bytes})
        document = Document(io.BytesIO(result))
        text = "\n".join([paragraph.text for paragraph in document.paragraphs] + [cell.text for table in document.tables for row in table.rows for cell in row.cells])
        self.assertIn("Evidencias de ejecución QA", text)
        self.assertIn("CP-01", text)
        self.assertIn("Respuesta no esperada", text)
        self.assertEqual(len(document.inline_shapes), 1)
        self.assertEqual(len(document.sections), 1)

    def test_report_marks_missing_values_and_missing_image(self):
        session = {
            "project_name": "Proyecto",
            "cases": [{"case_key": "case-1", "case_id": "CP-02", "name": "Caso sin datos", "status": "", "source_fields": {"Observaciones": ""}, "images": []}],
        }
        result = build_evidence_docx(session, {})
        document = Document(io.BytesIO(result))
        text = "\n".join([paragraph.text for paragraph in document.paragraphs] + [cell.text for table in document.tables for row in table.rows for cell in row.cells])
        self.assertIn("Sin evidencia gráfica adjunta.", text)
        self.assertIn("No informado", text)


if __name__ == "__main__":
    unittest.main()
