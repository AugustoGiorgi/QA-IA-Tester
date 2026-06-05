# services/routes_chat.py
from fastapi import APIRouter, UploadFile, File, HTTPException
from pathlib import Path
from uuid import uuid4
from typing import Dict, Any
import json
import re

from services.parsing import docx_to_text
from services.ai import complete

router = APIRouter()

# ⬅️ IMPORTANTE: subir un nivel (de services → backend) y reutilizar /data/sessions
BACKEND_DIR = Path(__file__).resolve().parent.parent  # .../backend
SESSIONS_DIR = BACKEND_DIR / "data" / "sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
SESSION_ID_RE = re.compile(r"^[a-f0-9-]{32,36}$", re.I)

def _session_dir(session_id: str) -> Path:
    if not SESSION_ID_RE.fullmatch(session_id or ""):
        raise HTTPException(status_code=400, detail="session_id inválido")
    d = SESSIONS_DIR / session_id
    d.mkdir(parents=True, exist_ok=True)
    return d

def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""

def _append_history(session_id: str, role: str, content: str):
    d = _session_dir(session_id)
    hist_path = d / "history.json"
    try:
        hist = json.loads(hist_path.read_text(encoding="utf-8"))
    except Exception:
        hist = []
    hist.append({"role": role, "content": content})
    hist_path.write_text(json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8")

@router.post("/api/chat/start")
async def chat_start(file: UploadFile = File(...), explanation: str = "") -> Dict[str, Any]:
    if not file.filename.lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="Subí un .docx")

    session_id = str(uuid4())
    d = _session_dir(session_id)

    # Guardar el DOCX original
    docx_path = d / "doc.docx"
    content = await file.read()
    docx_path.write_bytes(content)

    # Extraer texto del DOCX
    try:
        txt = docx_to_text(str(docx_path))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"No pude leer el DOCX: {e}")

    (d / "doc.txt").write_text(txt, encoding="utf-8")
    (d / "explanation.txt").write_text(explanation or "", encoding="utf-8")
    (d / "history.json").write_text("[]", encoding="utf-8")

    return {"session_id": session_id}

@router.post("/api/chat/ask")
async def chat_ask(payload: Dict[str, Any]) -> Dict[str, Any]:
    session_id = payload.get("session_id")
    question = (payload.get("question") or "").strip()
    if not session_id or not question:
        raise HTTPException(status_code=400, detail="Falta session_id o question")

    d = _session_dir(session_id)
    doc_txt = _read_text(d / "doc.txt")
    exp_txt = _read_text(d / "explanation.txt")
    if not doc_txt:
        raise HTTPException(status_code=404, detail="Sesión inválida o sin documento")

    system = {
        "role": "system",
        "content": (
            "Sos un analista funcional/QA extremadamente riguroso. "
            "Respondé únicamente usando la información del Documento Funcional y de la Explicación base. "
            "Si la pregunta no tiene respuesta en esos textos, decí: "
            "\"No hay información suficiente en el documento para responder con certeza.\" "
            "Sé concreto."
        ),
    }
    user = {
        "role": "user",
        "content": (
            "=== EXPLICACIÓN BASE ===\n"
            f"{exp_txt}\n\n"
            "=== DOCUMENTO (TEXTO PLANO) ===\n"
            f"{doc_txt}\n\n"
            "=== PREGUNTA ===\n"
            f"{question}"
        )
    }

    try:
        answer = complete([system, user], temperature=0.1)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al consultar el modelo: {e}")

    _append_history(session_id, "user", question)
    _append_history(session_id, "assistant", answer)

    return {"answer": answer, "session_id": session_id}
