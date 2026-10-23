from __future__ import annotations

import os
import tempfile
from contextlib import nullcontext
from pathlib import Path

from file_lock import exclusive_file_lock


def append_line(path: str | Path, line: str, *, locked: bool = False) -> int:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = nullcontext() if locked else exclusive_file_lock(target.with_suffix(target.suffix + ".lock"))
    value = line.rstrip("\r\n") + "\n"
    with lock:
        offset = target.stat().st_size if target.exists() else 0
        with target.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
    return offset


def write_latest(path: str | Path, content: str, *, append: bool = False, locked: bool = False) -> int:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = nullcontext() if locked else exclusive_file_lock(target.with_suffix(target.suffix + ".lock"))
    with lock:
        offset = target.stat().st_size if append and target.exists() else 0
        fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                if append and target.exists():
                    with target.open(encoding="utf-8", newline="") as current:
                        for chunk in iter(lambda: current.read(1024 * 1024), ""):
                            stream.write(chunk)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return offset
