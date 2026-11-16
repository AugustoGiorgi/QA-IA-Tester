"""Load the instruction set for exactly one AI-backed workflow."""

from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional


BACKEND_DIR = Path(__file__).resolve().parent.parent
SKILLS_DIR = BACKEND_DIR / "skills"
CONTRACTS_DIR = BACKEND_DIR / "skill_contracts"
SKILL_NAMES = frozenset({
    "entendimiento", "casos-prueba", "documento-funcional", "playwright",
    "evidencias", "sql-chat", "sql-datos", "chat-documental",
    "coach-funcional", "chat-calidad", "chat-proyectos",
    "cucumber-gherkin", "karate-framework",
})
APP_CONTRACT_SKILLS = frozenset({
    "casos-prueba", "documento-funcional", "evidencias", "playwright",
    "sql-chat", "sql-datos",
})


@lru_cache(maxsize=len(SKILL_NAMES))
def load_skill(name: str) -> str:
    if name not in SKILL_NAMES:
        raise ValueError(f"Skill desconocida: {name}")
    raw = (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
    opening, _, rest = raw.partition("\n")
    _, closing, body = rest.partition("\n---\n")
    if opening != "---" or not closing:
        raise ValueError(f"Skill sin frontmatter valido: {name}")
    content = body.strip()
    if not content:
        raise ValueError(f"Skill vacia: {name}")
    if name in APP_CONTRACT_SKILLS:
        contract = (CONTRACTS_DIR / f"{name}.md").read_text(encoding="utf-8").strip()
        if not contract:
            raise ValueError(f"Contrato de skill vacio: {name}")
        content += "\n\n## Requisitos de esta integracion (prevalecen sobre el formato por defecto)\n" + contract
    return content


def skill_messages(name: str, context: str, feedback: Optional[List[str]] = None) -> List[Dict[str, str]]:
    parts = [context]
    if feedback:
        parts.append("Retroalimentacion relevante:\n" + "\n\n".join(feedback))
    return [
        {"role": "system", "content": load_skill(name)},
        {"role": "user", "content": "\n\n".join(parts)},
    ]
