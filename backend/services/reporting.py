# backend/services/reporting.py
from __future__ import annotations
from typing import List
from .quality_template import QualityResult, SectionResult

def _status(score: float, maxp: float) -> str:
    if maxp <= 0:
        return "—"
    if score >= maxp * 0.95:
        return "OK"
    if score >= maxp * 0.5:
        return "Parcial"
    return "Falta"

def _fmt_pair(sr: SectionResult) -> str:
    return f"{sr.score:.0f} / {sr.max_points:.0f}"

def build_markdown_report(qr: QualityResult) -> str:
    lines: List[str] = []
    lines.append(f"# Reporte de Calidad — Modelo {qr.version}")
    lines.append("")
    lines.append("## Matriz de Cumplimiento\n")
    lines.append("| Sección | Puntaje | Estado |")
    lines.append("|---|---:|:--|")
    for sr in qr.section_results:
        lines.append(f"| {sr.name} | {_fmt_pair(sr)} | {_status(sr.score, sr.max_points)} |")

    if qr.coverage.get("global_issues"):
        lines.append("\n**Inconsistencias globales detectadas:**")
        for it in qr.coverage["global_issues"]:
            lines.append(f"- {it}")

    lines.append("\n## Detalle por sección")
    for sr in qr.section_results:
        lines.append(f"\n### {sr.name}")
        if sr.strengths:
            lines.append("**✅ Puntos fuertes:**")
            for it in sr.strengths:
                lines.append(f"- {it}")
        if sr.improvements:
            lines.append("**🛠️ A mejorar:**")
            for it in sr.improvements:
                lines.append(f"- {it}")
        if sr.issues and not sr.improvements:
            lines.append("**Gaps / Observaciones:**")
            for it in sr.issues:
                lines.append(f"- {it}")
        if sr.evidence:
            lines.append("**Evidencia:**")
            for ev in sr.evidence:
                lines.append(f"> {ev}")

    lines.append("\n---\n_Reporte generado automáticamente a partir del template unificado_.")
    return "\n".join(lines)
