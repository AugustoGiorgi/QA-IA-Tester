from typing import List, Dict
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph


def docx_to_text(path: str) -> str:
    """
    Extrae un texto lineal del DOCX (párrafos + tablas a modo 'fila | fila').
    Se usa para heurísticas de otras secciones, headings, etc.
    """
    doc = Document(path)
    texts = []

    # Keep each table beside its section, preserving the meaning of adjacent rules.
    for element in doc.element.body.iterchildren():
        if element.tag.endswith('}p'):
            paragraph = Paragraph(element, doc)
            if paragraph.text.strip():
                texts.append(paragraph.text.strip())
        elif element.tag.endswith('}tbl'):
            table = Table(element, doc)
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                if any(cells):
                    texts.append(" | ".join(cells))

    return "\n".join(texts)


# === Tablas estructuradas (para evaluaciones específicas) ===
def extract_tables(docx_path: str) -> List[Dict]:
    """
    Devuelve una lista de tablas con forma:
    [
      {"headers": ["Col1", "Col2", ...],
       "rows": [["v11","v12",...], ["v21","v22",...], ...]
      },
      ...
    ]
    La primera fila se toma como encabezados si existe.
    """
    doc = Document(docx_path)
    out: List[Dict] = []

    for t in doc.tables:
        headers = [c.text.strip() for c in t.rows[0].cells] if t.rows else []
        rows = []
        for r in t.rows[1:]:
            rows.append([c.text.strip() for c in r.cells])
        out.append({"headers": headers, "rows": rows})

    return out
