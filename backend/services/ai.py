import os
from typing import List, Dict, Optional
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# Podés dejar una variable de entorno; por defecto uso un modelo con más aire.
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
client = OpenAI()

SYSTEM_NAME = os.getenv("SYSTEM_NAME", "QA Doc Analyzer")
MAX_FEEDBACK_SNIPPETS = int(os.getenv("MAX_FEEDBACK_SNIPPETS", "3"))

def build_messages(
    task_instructions: str,
    doc_text: str,
    feedback_snippets: Optional[List[str]] = None
) -> List[Dict[str, str]]:
    system = {
        "role": "system",
        "content": (
            f"Sos {SYSTEM_NAME}. Ayudás a QA a entender documentos funcionales y a diseñar "
            "casos de prueba claros, detallados y ejecutables. Siempre respondé con tablas Markdown "
            "respetando exactamente las columnas pedidas, sin prosa adicional."
        ),
    }
    fb_content = "\n\n".join(feedback_snippets or [])
    user = {
        "role": "user",
        "content": (
            "INSTRUCCIONES:\n" + task_instructions + "\n\n"
            "RETROALIMENTACIÓN RELEVANTE (opcional):\n" + fb_content + "\n\n"
            "DOCUMENTO FUNCIONAL (texto plano):\n" + doc_text
        ),
    }
    return [system, user]

def complete(messages: List[Dict[str, str]], temperature: float = 0.2) -> str:
    """
    Ajustes para respuestas largas (tablas grandes) y evitar recortes:
    - max_tokens alto
    - top_p=1 para salida más determinista
    """
    resp = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=temperature,
        max_tokens=6000,   # subir/bajar según tu cuota; más aire = menos cortes
        top_p=1,
    )
    return resp.choices[0].message.content or ""
