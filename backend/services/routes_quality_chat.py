# backend/services/routes_quality_chat.py
# Chat sobre RECOMENDACIONES del análisis de calidad
from __future__ import annotations

import os
import tempfile
import traceback
import inspect
from typing import Dict, List

from fastapi import APIRouter, UploadFile, File, HTTPException
from pydantic import BaseModel
from uuid import uuid4

from .quality_template import evaluate_docx_against_template
from .ai import build_messages, complete  # mismo helper que ya usás en el chat de entendimiento

router = APIRouter(prefix="/api/reco-chat", tags=["reco-chat"])

# --- Memoria simple en proceso (por session_id) ---
class ChatTurn(BaseModel):
    role: str  # "user" | "assistant" | "system"
    content: str

class SessionState(BaseModel):
    recommendations: List[str]         # mejoras por sección
    strengths: List[str]               # puntos fuertes
    by_section: Dict[str, Dict[str, List[str]]]
    history: List[ChatTurn]            # historial breve de QA

SESSIONS: Dict[str, SessionState] = {}


def _collect_recos(qr) -> SessionState:
    recos: List[str] = []
    streng: List[str] = []
    by_section: Dict[str, Dict[str, List[str]]] = {}

    for sr in getattr(qr, "section_results", []):
        if getattr(sr, "improvements", None):
            for it in sr.improvements:
                recos.append(f"[{sr.name}] {it}")
        if getattr(sr, "strengths", None):
            for it in sr.strengths:
                streng.append(f"[{sr.name}] {it}")
        by_section[sr.name] = {
            "improvements": list(sr.improvements or []),
            "strengths": list(sr.strengths or []),
        }

    return SessionState(
        recommendations=recos,
        strengths=streng,
        by_section=by_section,
        history=[]
    )


# --------- Endpoints ----------
@router.post("/start")
async def start_session(file: UploadFile = File(...)):
    """
    Sube el DOCX, corre el evaluador y abre una sesión de chat sobre las recomendaciones.
    Devuelve session_id para seguir preguntando.
    """
    if not file or not file.filename or not file.filename.lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="Subí un archivo .docx válido.")

    tmp_path = None
    try:
        tmp_bytes = await file.read()

        # ⚠️ Windows-safe: delete=False para no lockear
        fd, tmp_path = tempfile.mkstemp(suffix=".docx")
        with os.fdopen(fd, "wb") as fh:
            fh.write(tmp_bytes)

        # Ejecutamos el evaluador sobre el path
        qr = evaluate_docx_against_template(tmp_path)

        state = _collect_recos(qr)
        if not state.recommendations and not state.strengths:
            raise HTTPException(
                status_code=400,
                detail="No se detectaron recomendaciones ni puntos fuertes en el análisis."
            )

        sid = uuid4().hex
        SESSIONS[sid] = state

        # Resumen inicial
        preview = {
            "sections_with_recos": [s for s, v in state.by_section.items() if v["improvements"]],
            "total_recos": len(state.recommendations),
            "total_strengths": len(state.strengths),
            "score": getattr(qr, "total_score", 0),
            "version": getattr(qr, "version", "unknown"),
        }
        return {"session_id": sid, "summary": preview}

    except HTTPException:
        raise
    except Exception as e:
        tb = traceback.format_exc(limit=4)
        raise HTTPException(
            status_code=500,
            detail=f"Error iniciando chat: {e.__class__.__name__}: {e}. Trace: {tb}"
        )
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


class AskIn(BaseModel):
    session_id: str
    question: str


@router.post("/ask")
async def ask(payload: AskIn):
    """
    Preguntá sobre las recomendaciones, pedí ejemplos, validá alternativas (sí/no), etc.
    """
    sid = (payload.session_id or "").strip()
    q = (payload.question or "").strip()

    if not sid or sid not in SESSIONS:
        raise HTTPException(status_code=404, detail="session_id inválido o expirada.")
    if not q:
        raise HTTPException(status_code=400, detail="Falta la pregunta.")

    st = SESSIONS[sid]

    recos_context = "\n".join(st.recommendations[:80]) or "—"
    strengths_context = "\n".join(st.strengths[:40]) or "—"

    system = (
        "Sos un experto en QA y documentación funcional. "
        "Respondé en español rioplatense, concreto y accionable. "
        "Si el usuario propone una opción, decí explícitamente si SÍ o NO y por qué, "
        "y sugerí la redacción o ejemplo exacto que faltaría en el documento. "
        "Evitá generalidades."
    )

    user_prompt = (
        f"RECOMENDACIONES DETECTADAS (por sección):\n{recos_context}\n\n"
        f"PUNTOS FUERTES:\n{strengths_context}\n\n"
        f"Pregunta del usuario: {q}\n\n"
        "Reglas de respuesta:\n"
        "- Si pide '¿a qué te referís?', explicá el gap concreto y mostrale 1–2 ejemplos textuales listos para pegar.\n"
        "- Si sugiere una alternativa, evaluá SÍ o NO y justificá en 1–2 frases.\n"
        "- Si hay varias secciones implicadas, nombralas entre corchetes (p. ej., [Riesgos]).\n"
        "- Sé breve (3–6 líneas)."
    )

    msgs = build_messages(system, user_prompt, [])
    try:
        if inspect.iscoroutinefunction(complete):
            answer = await complete(msgs)
        else:
            answer = complete(msgs)
    except Exception as e:
        tb = traceback.format_exc(limit=4)
        raise HTTPException(
            status_code=500,
            detail=f"Error consultando al modelo: {e.__class__.__name__}: {e}. Trace: {tb}"
        )

    st.history.append(ChatTurn(role="user", content=q))
    st.history.append(ChatTurn(role="assistant", content=answer))
    if len(st.history) > 20:
        st.history = st.history[-20:]

    return {"answer": answer}


@router.get("/state/{session_id}")
async def get_state(session_id: str):
    """Devuelve contexto básico de la sesión (útil para debug/UI)."""
    st = SESSIONS.get(session_id)
    if not st:
        raise HTTPException(status_code=404, detail="session_id inválido o expirada.")
    return {
        "recommendations": st.recommendations,
        "strengths": st.strengths,
        "sections": list(st.by_section.keys()),
        "history": [h.dict() for h in st.history][-10:],
    }
