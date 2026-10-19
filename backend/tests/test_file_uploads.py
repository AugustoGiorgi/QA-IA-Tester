import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import HTTPException, UploadFile  # noqa: E402
from services.files import safe_filename, save_upload  # noqa: E402
from services.routes_chat import chat_start  # noqa: E402


class FileUploadTests(unittest.IsolatedAsyncioTestCase):
    def test_windows_style_path_cannot_become_a_filename(self):
        self.assertEqual(safe_filename(r"C:\Users\QA\documento.docx"), "documento.docx")

    async def test_same_filename_does_not_overwrite_another_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "documento.docx"
            first = await save_upload(UploadFile(file=BytesIO(b"primero"), filename="documento.docx"), destination)
            second = await save_upload(UploadFile(file=BytesIO(b"segundo"), filename="documento.docx"), destination)
            self.assertNotEqual(first, second)
            self.assertEqual(first.read_bytes(), b"primero")
            self.assertEqual(second.read_bytes(), b"segundo")

    async def test_rejects_file_above_limit_without_writing_it(self):
        with tempfile.TemporaryDirectory() as directory, patch("services.files.MAX_UPLOAD_BYTES", 4):
            destination = Path(directory) / "documento.docx"
            with self.assertRaises(HTTPException) as raised:
                await save_upload(UploadFile(file=BytesIO(b"12345"), filename="documento.docx"), destination)
            self.assertEqual(raised.exception.status_code, 413)
            self.assertFalse(list(Path(directory).iterdir()))

    async def test_chat_rejects_oversized_document_before_creating_session(self):
        with tempfile.TemporaryDirectory() as directory, patch("services.routes_chat.MAX_UPLOAD_BYTES", 4), patch("services.routes_chat.SESSIONS_DIR", Path(directory)):
            with self.assertRaises(HTTPException) as raised:
                await chat_start(UploadFile(file=BytesIO(b"12345"), filename="documento.docx"))
            self.assertEqual(raised.exception.status_code, 413)
            self.assertFalse(list(Path(directory).iterdir()))

    async def test_chat_removes_document_after_extracting_text(self):
        with tempfile.TemporaryDirectory() as directory, patch("services.routes_chat.SESSIONS_DIR", Path(directory)), patch("services.routes_chat.docx_to_text", return_value="contenido"):
            result = await chat_start(UploadFile(file=BytesIO(b"DOCX"), filename="documento.docx"))
            session = Path(directory) / result["session_id"]
            self.assertEqual((session / "doc.txt").read_text(encoding="utf-8"), "contenido")
            self.assertFalse((session / "doc.docx").exists())
