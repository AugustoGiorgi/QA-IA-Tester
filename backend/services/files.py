from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from fastapi import UploadFile


def safe_filename(filename: Optional[str], default: str = "archivo") -> str:
    """Return a basename-only filename safe for local storage."""
    name = Path(filename or default).name.strip() or default
    name = re.sub(r"[\x00-\x1f]", "", name)
    return name or default


def safe_stem(filename: Optional[str], default: str = "archivo") -> str:
    stem = Path(safe_filename(filename, default)).stem.strip()
    stem = re.sub(r"[^\w .()\-]+", "_", stem, flags=re.UNICODE).strip(" ._")
    return stem or default


async def save_upload(file: UploadFile, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(await file.read())
    return destination
