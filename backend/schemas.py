# backend/schemas.py
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel
try:
    # Pydantic v2
    from pydantic import ConfigDict
    HAS_V2 = True
except Exception:
    HAS_V2 = False

class FeedbackOut(BaseModel):
    id: int
    filename: str
    source_doc_name: Optional[str] = None
    notes: Optional[str] = None
    stored_path: str
    created_at: datetime

    if HAS_V2:
        model_config = ConfigDict(from_attributes=True)
    else:
        class Config:
            orm_mode = True

# Entrada para generar documento funcional
class GenerateFunctionalIn(BaseModel):
    titulo: str
    contexto: Optional[str] = None
    objetivos: Optional[str] = None
    alcance: Optional[str] = None
    actores: Optional[List[str]] = None
    features: Optional[List[str]] = None
    integraciones: Optional[List[str]] = None
    restricciones: Optional[str] = None
    criterios_exito: Optional[str] = None
