"""Write a complete JSON snapshot despite transient Windows sync/indexer locks."""
import json
from pathlib import Path
import time
from tempfile import NamedTemporaryFile


def save_json(path, value):
    file = NamedTemporaryFile(mode="w", dir=path.parent, prefix=path.stem+"-", suffix=".tmp",
                              encoding="utf-8", delete=False)
    pending = Path(file.name)
    try:
        with file:
            json.dump(value, file, ensure_ascii=False, indent=2)
        for attempt in range(7):
            try:
                pending.replace(path)
                return
            except PermissionError:
                if attempt == 6:
                    raise
                time.sleep(.04 * 2**attempt)
    finally:
        pending.unlink(missing_ok=True)
