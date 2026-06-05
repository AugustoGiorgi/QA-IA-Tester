from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence


@dataclass(frozen=True)
class MarkdownTable:
    headers: List[str]
    rows: List[Dict[str, str]]


def _is_separator(line: str) -> bool:
    cleaned = line.replace("|", "").replace(":", "").replace(" ", "").strip()
    return bool(cleaned) and set(cleaned) == {"-"}


def _split_row(line: str) -> List[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def parse_tables(markdown: str) -> List[MarkdownTable]:
    lines = [line.rstrip() for line in (markdown or "").splitlines()]
    tables: List[MarkdownTable] = []
    i = 0

    while i < len(lines) - 1:
        if not lines[i].strip():
            i += 1
            continue
        if lines[i].lstrip().startswith("|") and _is_separator(lines[i + 1]):
            headers = [h.lower() for h in _split_row(lines[i])]
            rows: List[Dict[str, str]] = []
            i += 2

            while i < len(lines) and lines[i].lstrip().startswith("|"):
                if i + 1 < len(lines) and _is_separator(lines[i + 1]):
                    break
                cols = _split_row(lines[i])
                if len(cols) == len(headers):
                    rows.append(dict(zip(headers, cols)))
                i += 1

            tables.append(MarkdownTable(headers=headers, rows=rows))
            continue

        i += 1

    return tables


def first_table(markdown: str) -> MarkdownTable:
    tables = parse_tables(markdown)
    return tables[0] if tables else MarkdownTable([], [])


def normalize_first_table(markdown: str) -> str:
    lines = [line.rstrip() for line in (markdown or "").splitlines() if line.strip()]

    for i in range(len(lines) - 1):
        if lines[i].lstrip().startswith("|") and _is_separator(lines[i + 1]):
            table_lines: List[str] = []
            for line in lines[i:]:
                if not line.lstrip().startswith("|"):
                    break
                table_lines.append(line)
            return "\n".join(table_lines)

    return markdown


def _separator_for(headers: Sequence[str]) -> str:
    return "| " + " | ".join("---" for _ in headers) + " |"


def rows_signature(rows: Sequence[Dict[str, str]]) -> int:
    keys_seen = {
        (
            row.get("objetivo de la prueba", "").strip().lower(),
            row.get("funcionalidad", "").strip().lower(),
            row.get("resultado esperado", "").strip().lower(),
            row.get("observaciones", "").strip().lower(),
        )
        for row in rows
    }
    return len(keys_seen)


def merge_tables(markdown: str) -> str:
    tables = parse_tables(markdown)
    if not tables:
        return markdown

    headers = tables[0].headers
    seen = set()
    rows: List[Dict[str, str]] = []

    for table in tables:
        if table.headers != headers:
            continue
        for row in table.rows:
            key = tuple(row.get(header, "").strip().lower() for header in headers)
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)

    out = [
        "| " + " | ".join(headers) + " |",
        _separator_for(headers),
    ]
    for idx, row in enumerate(rows, start=1):
        values = [row.get(header, "") for header in headers]
        if values:
            values[0] = str(idx)
        out.append("| " + " | ".join(values) + " |")
    return "\n".join(out)
