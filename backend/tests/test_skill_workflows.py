import json
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zipfile import ZipFile

os.environ.setdefault("OPENAI_API_KEY", "test-only")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bson import ObjectId
from docx import Document
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from PIL import Image

from main import app
from services import ai, evidence_generator, routes_chat
from services.skills import load_skill, skill_revision


SOURCE = "Al guardar una reserva valida, el sistema confirma la reserva y muestra su identificador."


class SkillWorkflowTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        folder = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        (folder / "outputs").mkdir()
        stack.enter_context(patch("main.DATA_DIR", folder))
        stack.enter_context(patch("main.get_recent_feedback_snippets", return_value=[]))
        stack.enter_context(patch.object(routes_chat, "SESSIONS_DIR", folder / "sessions"))
        self.sync_ai = stack.enter_context(patch.object(
            ai.client.chat.completions, "create", side_effect=AssertionError("Unexpected model call"),
        ))
        self.async_ai = stack.enter_context(patch.object(
            ai.async_client.chat.completions, "create", new_callable=AsyncMock,
            side_effect=AssertionError("Unexpected model call"),
        ))
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def model_reply(self, mock, skill, expected_context, content):
        def reply(**options):
            messages = options["messages"]
            self.assertEqual(messages[0], {"role": "system", "content": load_skill(skill)})
            self.assertEqual(sum(message["role"] == "system" for message in messages), 1)
            self.assertIn(expected_context, str(messages[-1]["content"]))
            return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=content))])

        mock.side_effect = reply

    def upload_doc(self, endpoint):
        document = Document()
        document.add_paragraph(SOURCE)
        stream = BytesIO()
        document.save(stream)
        return self.client.post(endpoint, files={"file": ("regla.docx", stream.getvalue())})

    def assert_ok(self, response):
        self.assertEqual(response.status_code, 200, response.text[:300] if response.status_code != 200 else "")

    def test_health_reports_the_deployed_prompt_fingerprint_and_commit(self):
        with patch.dict(os.environ, {"RENDER_GIT_COMMIT": "test-commit"}):
            result = self.client.get("/api/health")
        self.assert_ok(result)
        self.assertEqual(result.json(), {
            "status": "ok", "revision": "test-commit", "skills_revision": skill_revision(),
        })

    def test_active_screens_and_modules_are_served_without_login(self):
        for page, script in {
            "index": "home", "entendimiento": "entendimiento", "casos": "casos",
            "playwright_xlsx": "playwright_xlsx", "evidencias": "evidencias",
            "sql-query": "sql-query", "postman": "postman", "karate": "karate",
        }.items():
            with self.subTest(page=page):
                html = self.client.get(f"/app/{page}.html", follow_redirects=False)
                self.assert_ok(html)
                self.assertIn(f"js/{script}.js", html.text)
                self.assert_ok(self.client.get(f"/app/js/{script}.js"))

    def test_understanding_and_followup_use_distinct_skills(self):
        explanation = "La reserva se confirma al guardar."
        self.model_reply(self.sync_ai, "entendimiento", SOURCE, explanation)
        result = self.upload_doc("/api/explain")
        self.assert_ok(result)
        self.assertEqual(result.text, explanation)

        session = self.upload_doc("/api/chat/start")
        self.assert_ok(session)
        answer = "El sistema muestra el identificador."
        self.model_reply(self.sync_ai, "chat-documental", SOURCE, answer)
        result = self.client.post("/api/chat/ask", json={
            "session_id": session.json()["session_id"], "question": "Que dato muestra?",
        })
        self.assert_ok(result)
        self.assertEqual(result.json()["answer"], answer)
        self.assertEqual(self.sync_ai.call_count, 2)

    def test_case_generation_delivers_a_readable_excel(self):
        table = ("| numero | objetivo de la prueba | funcionalidad | resultado esperado | observaciones |\n"
                 "| --- | --- | --- | --- | --- |\n"
                 "| 1 | Confirmar la reserva | Reserva | Se muestra el identificador | Datos ficticios |")
        self.model_reply(self.async_ai, "casos-prueba", SOURCE, table)
        result = self.upload_doc("/api/testcases")
        self.assert_ok(result)
        self.assertTrue(result.headers["content-type"].endswith("spreadsheetml.sheet"))
        workbook = load_workbook(BytesIO(result.content), read_only=True)
        try:
            cells = [cell for row in workbook.active.values for cell in row]
            self.assertIn("Confirmar la reserva", cells)
            self.assertIn("Se muestra el identificador", cells)
        finally:
            workbook.close()

    def test_playwright_document_and_video_keep_pending_selectors_and_download(self):
        output = {"cases": [{
            "id": "CP-01", "title": "Confirmar reserva",
            "steps": [{"name": "Guardar", "instruction": "Guardar la reserva",
                       "action_type": "click", "target": "guardar", "selector": ""}],
        }], "manual_checks": [], "test_data": {}}
        for filename, data, context in (
            ("regla.txt", SOURCE.encode(), SOURCE), ("flujo.mp4", b"fixture", "data:image/jpeg;base64,"),
        ):
            with self.subTest(filename=filename), patch("services.playwright_builder._video_frames", return_value=[b"fake frame"]):
                self.model_reply(self.sync_ai, "playwright", context, json.dumps(output))
                generated = self.client.post("/api/playwright/generate", files={"files": (filename, data)}, data={"project_name": "Reserva"})
                self.assert_ok(generated)
                draft = generated.json()["draft"]
                self.assertEqual(draft["selectors"]["guardar"], "TODO_SELECTOR: guardar")
                download = self.client.post("/api/playwright/download", json=draft)
                self.assert_ok(download)
                with ZipFile(BytesIO(download.content)) as archive:
                    specs = [name for name in archive.namelist() if name.endswith(".spec.ts")]
                    self.assertEqual(len(specs), 1)
                    self.assertIn("Confirmar reserva", archive.read(specs[0]).decode())

    def test_evidence_suggestion_preserves_category_title_and_caption(self):
        session_id, image_id = ObjectId(), ObjectId()
        stream = BytesIO()
        Image.new("RGB", (2, 2)).save(stream, format="PNG")
        images = SimpleNamespace(find_one=AsyncMock(return_value={"data": stream.getvalue(), "mime_type": "image/png"}))
        suggestion = {"category": "Acci\u00f3n", "title": "Guardar", "caption": "Boton visible"}
        self.model_reply(self.async_ai, "evidencias", "data:image/png;base64,", json.dumps(suggestion))
        with patch.object(evidence_generator, "_session", AsyncMock(return_value={"_id": session_id})), \
             patch.object(evidence_generator, "_db", return_value={evidence_generator.IMAGES: images}):
            result = self.client.post(f"/api/evidence/sessions/{session_id}/images/{image_id}/suggest")
        self.assert_ok(result)
        self.assertEqual(result.json()["suggestion"], suggestion)
