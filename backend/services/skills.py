"""Load the instruction set for exactly one AI-backed workflow."""

from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional


SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
SKILL_NAMES = frozenset({
    "entendimiento", "casos-prueba", "documento-funcional", "playwright",
    "evidencias", "sql-chat", "sql-datos", "chat-documental",
    "coach-funcional", "chat-calidad", "chat-proyectos",
})


@lru_cache(maxsize=len(SKILL_NAMES))
def load_skill(name: str) -> str:
    if name not in SKILL_NAMES:
        raise ValueError(f"Skill desconocida: {name}")
    content = (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8").strip()
    if not content:
        raise ValueError(f"Skill vacia: {name}")
    return content


def skill_messages(name: str, context: str, feedback: Optional[List[str]] = None) -> List[Dict[str, str]]:
    parts = [context]
    if feedback:
        parts.append("Retroalimentacion relevante:\n" + "\n\n".join(feedback))
    return [
        {"role": "system", "content": load_skill(name)},
        {"role": "user", "content": "\n\n".join(parts)},
    ]
