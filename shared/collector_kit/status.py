from __future__ import annotations

import json
import os
from contextlib import nullcontext
from pathlib import Path

from file_lock import exclusive_file_lock


def write_status(path: str | Path, payload: dict, *, locked: bool = False) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    lock = nullcontext() if locked else exclusive_file_lock(target.with_suffix(target.suffix + ".lock"))
    with lock:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, target)
