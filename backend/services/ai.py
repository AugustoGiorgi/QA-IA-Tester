import os
from typing import Any, List, Dict, Optional
from dotenv import load_dotenv
from openai import AsyncOpenAI, OpenAI
from services.skills import skill_messages

load_dotenv()

MODEL = os.getenv("QA_OPENAI_MODEL") or os.getenv("OPENAI_MODEL", "gpt-5.4")
REASONING_EFFORT = os.getenv("OPENAI_REASONING_EFFORT", "medium")
MAX_COMPLETION_TOKENS = int(os.getenv("OPENAI_MAX_COMPLETION_TOKENS", "16000"))
client = OpenAI(timeout=180.0, max_retries=1)
async_client = AsyncOpenAI(timeout=180.0, max_retries=1)


class AIResponseError(ValueError):
    """An incomplete result must not become a downloadable artifact."""


def chat_completion(messages: List[Dict[str, Any]], max_tokens: int = MAX_COMPLETION_TOKENS,
                    temperature: float = 0.2, response_format: Optional[Dict[str, str]] = None):
    options: Dict[str, Any] = {"model": MODEL, "messages": messages}
    if MODEL.startswith("gpt-5"):
        # extra_body keeps the installed SDK compatible with the newer API fields.
        options["extra_body"] = {
            "reasoning_effort": REASONING_EFFORT,
            "max_completion_tokens": max_tokens,
        }
    else:
        options.update(max_tokens=max_tokens, temperature=temperature, top_p=1)
    if response_format is not None:
        options["response_format"] = response_format
    response = client.chat.completions.create(**options)
    if not response.choices:
        raise AIResponseError("La IA no devolvio un resultado. Intenta nuevamente.")
    choice = response.choices[0]
    if choice.finish_reason == "length":
        raise AIResponseError("La respuesta supero el limite de generacion. Divide el documento o proceso en partes mas pequenas.")
    if choice.finish_reason != "stop" or getattr(choice.message, "refusal", None):
        raise AIResponseError("La IA no pudo completar esta solicitud. Revisa el contenido enviado.")
    if not (choice.message.content or "").strip():
        raise AIResponseError("La IA devolvio una respuesta vacia. Intenta nuevamente.")
    return response

MAX_FEEDBACK_SNIPPETS = int(os.getenv("MAX_FEEDBACK_SNIPPETS", "3"))

def build_messages(
    skill_name: str,
    doc_text: str,
    feedback_snippets: Optional[List[str]] = None
) -> List[Dict[str, str]]:
    return skill_messages(skill_name, doc_text, feedback_snippets)

def complete(messages: List[Dict[str, str]], temperature: float = 0.2) -> str:
    resp = chat_completion(messages, temperature=temperature)
    return resp.choices[0].message.content or ""


async def chat_completion_async(
    messages: List[Dict[str, Any]],
    max_tokens: int = MAX_COMPLETION_TOKENS,
    temperature: float = 0.2,
    response_format: Optional[Dict[str, str]] = None,
):
    options: Dict[str, Any] = {"model": MODEL, "messages": messages}
    if MODEL.startswith("gpt-5"):
        options["extra_body"] = {
            "reasoning_effort": REASONING_EFFORT,
            "max_completion_tokens": max_tokens,
        }
    else:
        options.update(max_tokens=max_tokens, temperature=temperature, top_p=1)
    if response_format is not None:
        options["response_format"] = response_format
    response = await async_client.chat.completions.create(**options)
    if not response.choices:
        raise AIResponseError("La IA no devolvio un resultado. Intenta nuevamente.")
    choice = response.choices[0]
    if choice.finish_reason == "length":
        raise AIResponseError("La respuesta supero el limite de generacion. Divide el documento o proceso en partes mas pequenas.")
    if choice.finish_reason != "stop" or getattr(choice.message, "refusal", None):
        raise AIResponseError("La IA no pudo completar esta solicitud. Revisa el contenido enviado.")
    if not (choice.message.content or "").strip():
        raise AIResponseError("La IA devolvio una respuesta vacia. Intenta nuevamente.")
    return response


async def complete_async(messages: List[Dict[str, str]], temperature: float = 0.2) -> str:
    response = await chat_completion_async(messages, temperature=temperature)
    return response.choices[0].message.content or ""
