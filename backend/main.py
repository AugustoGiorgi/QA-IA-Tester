# backend/main.py  (Python 3.8+)
import logging
import os
import re
import traceback
from pathlib import Path
from typing import List, Dict, Optional

from fastapi import FastAPI, UploadFile, File, Depends, HTTPException
from fastapi.responses import PlainTextResponse, StreamingResponse, JSONResponse, FileResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from dotenv import load_dotenv
from sqlalchemy.orm import Session

from database import Base, engine, get_db
from models import Feedback
from schemas import FeedbackOut, GenerateFunctionalIn

# Servicios
from services.parsing import docx_to_text
from services.testcases import generate_testcases_markdown
from services.ai import AIResponseError, MAX_FEEDBACK_SNIPPETS, build_messages, complete
from services.generator import generate_functional_json, functional_json_to_docx
from services.project_chat import chat_proyectos
from services.files import safe_filename, safe_stem, save_upload
from services.markdown_table import first_table
from services.auth import require_roles
from services.activity import record_activity
from services.quality_records import router as quality_records_router

# Evaluador determinístico + reporter
from services.quality_template import evaluate_docx_against_template
from services.reporting import build_markdown_report

# Playwright desde Excel (service)
from services.playwright_xlsx import router as playwright_xlsx_router
from services.playwright_ai import router as playwright_ai_router
from services.postman_generator import router as postman_generator_router
from services.karate_generator import router as karate_generator_router

# ⬇️ Chat de recomendaciones (calidad)

# ⬇️ NUEVO: Coach de Documento Funcional (ida y vuelta)

# Utilidad de Excel (usa plantilla)
from utils.excel import to_excel_bytes

load_dotenv()
logger = logging.getLogger("qa_doc_analyzer")

# --- App FASTAPI ---
app = FastAPI(title="QA Doc Analyzer API")


@app.exception_handler(AIResponseError)
async def incomplete_ai_response(request, exc):
    return JSONResponse(status_code=502, content={"detail": str(exc)})

# ⚠️ Desactivar redirects automáticos de barra final/inicial
try:
    app.router.redirect_slashes = False
except Exception:
    pass

# Auth
app.include_router(quality_records_router)

# Router de Chat contextual (repreguntas sobre el DF)
from services.routes_chat import router as chat_router
app.include_router(chat_router, dependencies=[Depends(require_roles("qa", "lider"))])

# Montar router de chat de recomendaciones

# Montar router NUEVO del coach funcional

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Paths/Dirs ---
BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent
DATA_DIR = BACKEND_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
(DATA_DIR / "feedback").mkdir(exist_ok=True)
(DATA_DIR / "outputs").mkdir(exist_ok=True)
(DATA_DIR / "templates").mkdir(exist_ok=True)

# Montar frontend si existe
FRONTEND_DIR = PROJECT_ROOT / "frontend"
if FRONTEND_DIR.exists():
    @app.get("/app/{legacy_page}", include_in_schema=False)
    async def legacy_page(legacy_page: str):
        if legacy_page in {"login.html", "usuarios.html", "calidad.html", "chat-funcional.html", "importacion.html"}:
            return RedirectResponse("/app/index.html")
        target = FRONTEND_DIR / legacy_page
        if not target.is_file() or target.parent != FRONTEND_DIR:
            raise HTTPException(status_code=404)
        return FileResponse(target)

    app.mount("/app", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")

    @app.get("/")
    async def root_front():
        return FileResponse(FRONTEND_DIR / "index.html")

# --- DB ---
Base.metadata.create_all(bind=engine)

# --- Helpers ---
def get_recent_feedback_snippets(db: Session, limit: int = MAX_FEEDBACK_SNIPPETS) -> List[str]:
    rows = db.query(Feedback).order_by(Feedback.created_at.desc()).limit(limit).all()
    snippets: List[str] = []
    for r in rows:
        if r.notes:
            snippets.append(f"Ajustes previos ({r.filename}): {r.notes}")
    return snippets

# === Health ===
@app.get("/api/health")
async def health():
    return {"status": "ok"}

# === 1) ENTENDIMIENTO ===
EXPLAIN_PROMPT_STORY = """
Leé el Documento Funcional completo y explicalo como una narración corrida, natural y fácil de entender.

Quiero que la respuesta parezca una explicación hablada, como si una persona le estuviera contando a otra de qué trata el documento, pero escrita de forma prolija.

Formato obligatorio:
- No uses tablas.
- No uses títulos.
- No uses subtítulos.
- No uses etiquetas como "Solicitud", "Funcionamiento", "Validación del sistema", "Impacto funcional" o "Puntos a revisar".
- No uses bullets.
- No uses numeraciones.
- No uses markdown.
- No armes una estructura por secciones.
- No copies frases literales del documento.
- No empieces con una tabla ni con columnas.
- Escribí únicamente párrafos narrativos.

Estilo:
- Usá español claro, cercano y natural.
- Podés usar un tono criollo/profesional, como explicándole el documento a un compañero de trabajo.
- Arrancá la explicación de forma natural.
- Explicá el contenido de corrido, conectando una idea con la otra.
- Separá el texto en párrafos, pero sin poner encabezados.
- No hagas frases sueltas tipo ficha técnica.
- No resumas demasiado: desarrollá bien la explicación.

Contenido que debe cubrir:
- Qué problema o necesidad plantea el documento.
- Qué cambio o funcionalidad se quiere implementar.
- Cómo debería funcionar el sistema.
- Qué validaciones o reglas importantes aparecen.
- Qué impacto tiene para el usuario o el proceso.
- Qué puntos habría que revisar o aclarar con negocio si corresponde.

Muy importante:
La salida final debe ser solo texto narrativo. 
Si detectás que estás escribiendo algo con formato de tabla, lista, sección o ficha, reescribilo como párrafos corridos antes de responder.
"""

@app.post("/api/explain", response_class=PlainTextResponse)
async def explain(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user=Depends(require_roles("qa", "lider")),
):
    if not file.filename.lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="Subí un .docx")
    filename = safe_filename(file.filename)
    tmp_path = await save_upload(file, DATA_DIR / filename)

    text = docx_to_text(str(tmp_path))
    msgs = build_messages(EXPLAIN_PROMPT_STORY, text, get_recent_feedback_snippets(db))
    explanation = await run_in_threadpool(complete, msgs)

    explanation = re.sub(r"\|", "", explanation)          # elimina tablas tipo |
    explanation = re.sub(r"-{2,}", "", explanation)       # elimina ----
    explanation = re.sub(r"\n\s*\n", "\n\n", explanation) # limpia saltos raros
    explanation = explanation.replace("  ", " ")          # espacios dobles
    explanation = explanation.strip()

    out_path = DATA_DIR / "outputs" / f"explicacion_{safe_stem(filename)}.txt"
    out_path.write_text(explanation, encoding="utf-8")
    await record_activity(
        user,
        "Entendimiento de funcional",
        "entendimiento",
        f"Analizo el documento {filename}",
        {
            "archivo_cargado": filename,
            "archivo_generado": out_path.name,
            "resultado_url": f"/api/outputs/{out_path.name}",
            "devolucion_ia": explanation[:4000],
        },
    )

    return PlainTextResponse(explanation)

# === 2) CALIDAD (template unificado) ===
@app.post("/api/quality")
@app.post("/api/quality/")
async def quality(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user=Depends(require_roles("funcional", "lider")),
):
    if not file.filename.lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="Subí un .docx")

    filename = safe_filename(file.filename)
    tmp_path = await save_upload(file, DATA_DIR / filename)

    try:
        qr = evaluate_docx_against_template(str(tmp_path))
        score = qr.total_score
        report_md = build_markdown_report(qr)

        out_path = DATA_DIR / "outputs" / f"calidad_{safe_stem(filename)}.txt"
        out_path.write_text(report_md, encoding="utf-8")
        await record_activity(
            user,
            "Calificacion de funcional",
            "calidad-funcional",
            f"Califico el documento {filename}",
            {
                "archivo_cargado": filename,
                "archivo_generado": out_path.name,
                "resultado_url": f"/api/outputs/{out_path.name}",
                "score": score,
                "devolucion_ia": report_md[:4000],
            },
        )

        def _status_local(s: float, m: float) -> str:
            if m <= 0: return "—"
            if s >= m * 0.95: return "OK"
            if s >= m * 0.5:  return "Parcial"
            return "Falta"

        sections = []
        for sr in qr.section_results:
            rating_5 = (sr.score / sr.max_points * 5) if sr.max_points else 0.0
            sections.append({
                "name": sr.name,
                "max_points": sr.max_points,
                "achieved": sr.score,
                "rating": round(rating_5, 2),
                "present": sr.score > 0,
                "status": _status_local(sr.score, sr.max_points),
                "strengths": getattr(sr, "strengths", []),
                "improvements": getattr(sr, "improvements", []),
                "rationale": getattr(sr, "rationale", ""),
                "issues": getattr(sr, "issues", []),
                "evidence": getattr(sr, "evidence", []),
            })

        return JSONResponse({
            "score": score,
            "rubric_version": qr.version,
            "report_markdown": report_md,
            "txt_path": str(out_path.name),
            "sections": sections,
        })
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass

@app.post("/api/chat-proyectos")
async def chat_endpoint(payload: dict, user=Depends(require_roles("lider"))):
    try:
        message = payload.get("message")
        session_id = payload.get("session_id", "default")

        if not message:
            return {
                "success": False,
                "error": "El campo 'message' es obligatorio"
            }

        resp = await chat_proyectos(message, session_id)

        return {
            "success": True,
            "response": resp
        }

    except Exception as e:
        return {
            "success": False,
            "error": "Error en chat_proyectos",
            "detail": str(e)
        }        

# === 3) CASOS DE PRUEBA (Excel con plantilla) ===
@app.post("/api/testcases")
async def testcases(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user=Depends(require_roles("qa")),
):
    if not file.filename.lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="Subí un .docx")
    original_name = safe_filename(file.filename)
    try:
        tmp_path = await save_upload(file, DATA_DIR / original_name)
    except Exception:
        logger.exception("No se pudo guardar archivo para casos de prueba")
        raise HTTPException(status_code=500, detail="No se pudo guardar el archivo subido.")

    try:
        text = docx_to_text(str(tmp_path))
    except Exception:
        logger.exception("No se pudo leer DOCX para casos de prueba")
        raise HTTPException(status_code=400, detail="No se pudo leer el DOCX. Verificá que el archivo no esté dañado.")

    if not text or len(text.strip()) < 40:
        raise HTTPException(status_code=400, detail="No se pudo extraer texto suficiente del DOCX.")

    try:
        table_md = await run_in_threadpool(generate_testcases_markdown, text, get_recent_feedback_snippets(db))
    except AIResponseError:
        raise
    except Exception as exc:
        logger.error("Fallo generación IA de casos: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(
            status_code=502,
            detail=f"No se pudieron generar los casos con IA. Revisá configuración/cuota/modelo de OpenAI. Detalle: {type(exc).__name__}",
        )

    try:
        rows: List[Dict[str, str]] = []
        for row_map in first_table(table_md).rows:
            numero = row_map.get("número", row_map.get("numero", ""))
            rows.append({
                "Caso de Prueba": numero or "1",
                "Objetivo": row_map.get("objetivo de la prueba", ""),
                "Paso a Paso": "",
                "Resultado Esperado": row_map.get("resultado esperado", ""),
                "Precondiciones": "",
                "Prioridad": "",
                "Datos de Prueba": "",
                "Funcionalidad": row_map.get("funcionalidad", ""),
                "Observaciones": row_map.get("observaciones", ""),
            })

        if not rows:
            rows = [{
                "Caso de Prueba": "1",
                "Objetivo": "Validar funcionalidad principal del DF",
                "Paso a Paso": "",
                "Resultado Esperado": "Resultados según reglas del DF",
                "Precondiciones": "",
                "Prioridad": "",
                "Datos de Prueba": "",
                "Funcionalidad": "—",
                "Observaciones": "La IA no devolvió una tabla parseable; revisar el documento y regenerar.",
            }]

        excel_bytes = to_excel_bytes(rows)
        output_name = f"casos_{safe_stem(original_name)}.xlsx"
        out_path = DATA_DIR / "outputs" / output_name
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(excel_bytes)
    except Exception:
        logger.exception("No se pudo armar Excel de casos")
        raise HTTPException(status_code=500, detail="Se generaron los casos, pero falló la creación del Excel.")

    await record_activity(
        user,
        "Generacion de casos de prueba",
        "casos",
        f"Genero casos de prueba desde {original_name}",
        {
            "archivo_cargado": original_name,
            "archivo_generado": output_name,
            "resultado_url": f"/api/outputs/{output_name}",
            "cantidad_casos": len(rows),
        },
    )
    return StreamingResponse(
        iter([excel_bytes]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={output_name}"}
    )

# === 4) FEEDBACK ===
@app.post("/api/feedback", response_model=FeedbackOut)
async def upload_feedback(
    file: UploadFile = File(...),
    source_doc_name: Optional[str] = None,
    notes: Optional[str] = None,
    db: Session = Depends(get_db)
):
    if not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Subí un .xlsx con los casos ajustados")

    filename = safe_filename(file.filename)
    dest = await save_upload(file, DATA_DIR / "feedback" / filename)

    row = Feedback(
        filename=filename,
        source_doc_name=source_doc_name,
        notes=notes,
        stored_path=str(dest)
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    return row

@app.get("/api/feedback", response_model=List[FeedbackOut])
async def list_feedback(db: Session = Depends(get_db)):
    rows = db.query(Feedback).order_by(Feedback.created_at.desc()).all()
    return rows

# === 5) Descarga de outputs .txt ===
@app.get("/api/outputs/{filename}")
async def download_output(filename: str):
    filename = safe_filename(filename)
    path = DATA_DIR / "outputs" / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    media_type = "text/plain"
    if path.suffix.lower() == ".xlsx":
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return FileResponse(path, media_type=media_type, filename=filename)

# === 6) Generador de DOC Funcional (+ evaluación con modelo nuevo)
@app.post("/api/functional/generate")
async def generate_functional(
    payload: GenerateFunctionalIn,
    db: Session = Depends(get_db),
    user=Depends(require_roles("funcional")),
):
    data = generate_functional_json(payload.dict(), get_recent_feedback_snippets(db))
    safe_title = safe_stem((payload.titulo or "Documento_Funcional").strip().replace(" ", "_"))
    out_docx = DATA_DIR / "outputs" / f"funcional_{safe_title}.docx"
    functional_json_to_docx(data, str(out_docx))

    qr = evaluate_docx_against_template(str(out_docx))
    report = build_markdown_report(qr)
    out_txt = DATA_DIR / "outputs" / f"calidad_{out_docx.stem}.txt"
    out_txt.write_text(report, encoding="utf-8")

    return JSONResponse({
        "docx_filename": out_docx.name,
        "quality_score": qr.total_score,
        "quality_report_txt": out_txt.name
    })

@app.get("/api/outputs/docx/{filename}")
async def download_docx(filename: str):
    filename = safe_filename(filename)
    path = DATA_DIR / "outputs" / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=filename
    )

@app.get("/api/ping-playwright-v2")
def ping_playwright_v2():
    return {"ok": True, "mode": "transpiler", "route": "/api/playwright/build-xlsx-v2"}

app.include_router(playwright_xlsx_router, prefix="/api/playwright", dependencies=[Depends(require_roles("qa"))])
app.include_router(playwright_ai_router, prefix="/api/playwright")
app.include_router(postman_generator_router)
app.include_router(karate_generator_router)
