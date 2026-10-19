from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, UploadFile


MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def safe_filename(filename: Optional[str], default: str = "archivo") -> str:
    """Return a basename-only filename safe for local storage."""
    name = Path((filename or default).replace("\\", "/")).name.strip() or default
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name or default


def safe_stem(filename: Optional[str], default: str = "archivo") -> str:
    stem = Path(safe_filename(filename, default)).stem.strip()
    stem = re.sub(r"[^\w .()\-]+", "_", stem, flags=re.UNICODE).strip(" ._")
    return stem or default


async def save_upload(file: UploadFile, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    contents = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="El archivo supera el límite de 20 MB.")
    path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=destination.parent, prefix=f"{safe_stem(destination.name)}_",
            suffix=destination.suffix, delete=False,
        ) as temporary:
            path = Path(temporary.name)
            temporary.write(contents)
        return path
    except OSError:
        if path is not None:
            path.unlink(missing_ok=True)
        raise
