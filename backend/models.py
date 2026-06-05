# backend/models.py
from sqlalchemy import Column, Integer, String, DateTime, func
from database import Base

class Feedback(Base):
    __tablename__ = "feedback"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String, nullable=False)
    source_doc_name = Column(String, nullable=True)
    notes = Column(String, nullable=True)
    stored_path = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
