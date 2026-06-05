from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, List, Optional
from pathlib import Path
from uuid import uuid4
import json
import re

from services.ai import build_messages, complete  # helpers existentes
from services.generator import generate_functional_json, functional_json_to_docx
from services.quality_template import evaluate_docx_against_template
from services.reporting import build_markdown_report
from services.files import safe_stem

router = APIRouter()

ROOT = Path(__file__).resolve().parent.parent  # .../backend
DATA = ROOT / "data"
SESSIONS = DATA / "sessions_coach"
OUTPUTS = DATA / "outputs"
SESSIONS.mkdir(parents=True, exist_ok=True)
OUTPUTS.mkdir(parents=True, exist_ok=True)

SECTIONS: List[Dict[str, Any]] = [
    {"key": "situacion_actual", "label": "Situación actual", "required": True},
    {"key": "objetivos",        "label": "Objetivos",        "required": True},
    {"key": "alcance",          "label": "Alcance / Fuera de alcance", "required": True},
    {"key": "actores",          "label": "Actores / Roles",  "required": True},
    {"key": "flujos",           "label": "Flujos / Casos de uso", "required": True},
    {"key": "integraciones",    "label": "Integraciones",    "required": False},
    {"key": "nfr",              "label": "Requisitos no funcionales", "required": False},
    {"key": "criterios",        "label": "Criterios de aceptación", "required": True},
    {"key": "restricciones",    "label": "Restricciones / Políticas", "required": False},
]

class StartPayload(BaseModel):
    titulo: Optional[str] = None

class MessagePayload(BaseModel):
    session_id: str
    text: str

class ConfirmPayload(BaseModel):
    session_id: str
    ok: bool
    changes: Optional[str] = None

class FinishPayload(BaseModel):
    session_id: str
    generate_doc: bool = False

SESSION_ID_RE = re.compile(r"^[a-f0-9-]{32,36}$", re.I)


def _p_session(session_id: str) -> Path:
    if not SESSION_ID_RE.fullmatch(session_id or ""):
        raise HTTPException(400, "session_id inválido")
    return SESSIONS / f"{session_id}.json"

def _load(session_id: str) -> Dict[str, Any]:
    p = _p_session(session_id)
    if not p.exists():
        raise HTTPException(404, "Sesión no encontrada")
    return json.loads(p.read_text(encoding="utf-8"))

def _save(session_id: str, data: Dict[str, Any]):
    _p_session(session_id).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def _current_section(state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    idx = state.get("section_index", 0)
    if 0 <= idx < len(SECTIONS):
        return SECTIONS[idx]
    return None

def _is_done(state: Dict[str, Any]) -> bool:
    return state.get("section_index", 0) >= len(SECTIONS)

# --- Compatibilidad: tu complete(msgs) puede devolver str o dict ---
def _complete_compat(msgs) -> str:
    out = complete(msgs)  # sin max_tokens
    if isinstance(out, dict):
        # por si tu wrapper devuelve algo tipo {"content": "..."} o {"text": "..."}
        out = out.get("content") or out.get("text") or ""
    return (out or "").strip()

def _ai_review_and_questions(section_label: str, user_text: str) -> str:
    """
    1) Qué está claro  2) Ambigüedades/faltantes  3) 2–5 preguntas puntuales
    """
    system = (
        "Sos un analista funcional senior. Te paso la sección indicada y el texto del usuario. "
        "1) Decí qué está claro. 2) Lista brevemente AMBIGÜEDADES o faltantes. "
        '3) Hacé 2–5 PREGUNTAS puntuales. Sé concreto, en español rioplatense. Usa bullets "-" cuando corresponda.'
    )
    user = f"Sección: {section_label}\n\nTexto del usuario:\n{user_text}"
    msgs = build_messages(system, user)  # posicional
    return _complete_compat(msgs)

def _ai_propose(section_label: str, consolidated_text: str) -> str:
    """
    Redacción final concisa y verificable. No inventar; si falta algo, marcar 'TODO:'.
    """
    system = (
        "Sos un analista funcional senior. Proponé una redacción final, concisa y verificable "
        "para la sección indicada SIN inventar datos. Si faltan, dejá 'TODO:'. "
        "Podés usar viñetas si ayuda."
    )
    user = f"Sección: {section_label}\n\nNotas consolidadas:\n{consolidated_text}"
    msgs = build_messages(system, user)  # posicional
    return _complete_compat(msgs)

@router.post("/api/functional/coach/start")
def coach_start(payload: StartPayload):
    session_id = str(uuid4())
    state = {
        "session_id": session_id,
        "titulo": payload.titulo or "Documento Funcional",
        "section_index": 0,
        "sections_data": { s["key"]: {"status": "awaiting_input", "raw": "", "qa_notes": "", "final": ""} for s in SECTIONS },
    }
    _save(session_id, state)
    first = SECTIONS[0]
    return {
        "session_id": session_id,
        "message": f"Hola 👋 Arranquemos por **{first['label']}**. Contame la situación actual con todo el contexto que tengas.",
        "section": first["key"],
        "label": first["label"],
        "status": "awaiting_input"
    }

@router.post("/api/functional/coach/message")
def coach_message(payload: MessagePayload):
    state = _load(payload.session_id)
    if _is_done(state):
        return {"done": True, "message": "Ya finalizaste todas las secciones."}

    section = _current_section(state)
    sec_key, sec_label = section["key"], section["label"]
    sec_state = state["sections_data"][sec_key]
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(400, "Texto vacío")

    if sec_state["status"] == "awaiting_input":
        sec_state["raw"] = text
        review = _ai_review_and_questions(sec_label, text)
        sec_state["qa_notes"] = review
        sec_state["status"] = "awaiting_corrections"
        _save(payload.session_id, state)
        return {
            "section": sec_key,
            "label": sec_label,
            "status": "awaiting_corrections",
            "assistant": (
                "Leí tu texto. Observaciones y preguntas:\n\n"
                f"{review}\n\n"
                "Respondeme estas preguntas o agregá aclaraciones."
            )
        }

    if sec_state["status"] in ("awaiting_corrections",):
        consolidated = (sec_state["raw"] + "\n\nAclaraciones del usuario:\n" + text).strip()
        proposal = _ai_propose(sec_label, consolidated)
        sec_state["final"] = proposal
        sec_state["status"] = "awaiting_confirmation"
        _save(payload.session_id, state)
        return {
            "section": sec_key,
            "label": sec_label,
            "status": "awaiting_confirmation",
            "assistant": "Esta sería la redacción final. ¿Está OK o querés ajustes?\n\n" + proposal
        }

    if sec_state["status"] == "awaiting_confirmation":
        consolidated = (sec_state["raw"] + "\n\nAjustes adicionales del usuario:\n" + text).strip()
        proposal = _ai_propose(sec_label, consolidated)
        sec_state["final"] = proposal
        _save(payload.session_id, state)
        return {
            "section": sec_key,
            "label": sec_label,
            "status": "awaiting_confirmation",
            "assistant": "Actualicé la propuesta. ¿Así está OK?\n\n" + proposal
        }

    raise HTTPException(400, f"Estado inválido para escribir: {sec_state['status']}")

@router.post("/api/functional/coach/confirm")
def coach_confirm(payload: ConfirmPayload):
    state = _load(payload.session_id)
    if _is_done(state):
        return {"done": True, "message": "Ya finalizaste todas las secciones."}

    section = _current_section(state)
    sec_key, sec_label = section["key"], section["label"]
    sec_state = state["sections_data"][sec_key]

    if sec_state["status"] != "awaiting_confirmation":
        raise HTTPException(400, "La sección aún no está lista para confirmar.")

    if payload.ok:
        sec_state["status"] = "ok"
        state["section_index"] += 1
        _save(payload.session_id, state)

        if _is_done(state):
            return {
                "done": True,
                "assistant": "¡Perfecto! Terminamos todas las secciones. Podés **Finalizar sin generar** o **Finalizar y generar DOCX**."
            }

        next_sec = _current_section(state)
        return {
            "done": False,
            "assistant": f"Sección **{sec_label}** OK ✅. Sigamos con **{next_sec['label']}**. Contame lo relevante.",
            "next_section": next_sec["key"],
            "next_label": next_sec["label"],
            "status": "awaiting_input"
        }

    # no OK -> cambios
    changes = (payload.changes or "").strip()
    if not changes:
        return {"assistant": "Decime qué cambiar y lo reescribo."}
    consolidated = state["sections_data"][sec_key]["raw"] + "\n\nCorrecciones solicitadas:\n" + changes
    proposal = _ai_propose(sec_label, consolidated)
    sec_state["final"] = proposal
    sec_state["status"] = "awaiting_confirmation"
    _save(payload.session_id, state)
    return {
        "assistant": "Reescribí con tus cambios. ¿Ahora sí está OK?\n\n" + proposal,
        "section": sec_key,
        "label": sec_label,
        "status": "awaiting_confirmation"
    }

@router.post("/api/functional/coach/finish")
def coach_finish(payload: FinishPayload):
    state = _load(payload.session_id)

    if payload.generate_doc:
        missing = []
        for s in SECTIONS:
            if s["required"]:
                final_txt = state["sections_data"][s["key"]].get("final", "").strip()
                if not final_txt:
                    missing.append(s["label"])
        if missing:
            return {"ok": False, "error": "Faltan secciones obligatorias confirmadas.", "missing": missing}

        context = {"titulo": state.get("titulo") or "Documento Funcional"}
        for s in SECTIONS:
            context[s["key"]] = state["sections_data"][s["key"]].get("final", "").strip() or None

        data = generate_functional_json(context, feedback_snippets=None)
        safe_title = safe_stem((context["titulo"] or "Documento_Funcional").replace(" ", "_"))
        out_docx = OUTPUTS / f"funcional_{safe_title}.docx"
        functional_json_to_docx(data, str(out_docx))

        qr = evaluate_docx_against_template(str(out_docx))
        report = build_markdown_report(qr)
        out_txt = OUTPUTS / f"calidad_{out_docx.stem}.txt"
        out_txt.write_text(report, encoding="utf-8")

        return {
            "ok": True,
            "generated": True,
            "docx_filename": out_docx.name,
            "quality_score": qr.total_score,
            "quality_report_txt": out_txt.name
        }

    return {"ok": True, "generated": False, "message": "Chat finalizado sin generar documento."}
