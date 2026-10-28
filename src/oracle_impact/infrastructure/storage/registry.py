"""Cross-run object version history.

One versioned JSON document written atomically: the checksum of each captured
object, per environment, with the run that first and last saw it.
"""

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
_FILES = {"versions"}
_LOCK = threading.RLock()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Registry:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, name: str) -> Path:
        if name not in _FILES:
            raise ValueError("Unknown registry file.")
        return self.root / f"{name}.json"

    def _read(self, name: str) -> list[dict]:
        path = self._path(name)
        if not path.exists():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        return list(payload.get("items") or [])

    def _write(self, name: str, items: list[dict]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(name)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"schema_version": SCHEMA_VERSION, "items": items}, indent=2), encoding="utf-8")
        os.replace(temporary, path)

    def record_versions(self, run_id: str, captured: list[dict]) -> None:
        """Append a version entry for each environment object whose checksum was not seen before."""
        with _LOCK:
            versions = self._read("versions")
            known = {(item["environment"], item["owner"], item["name"], item["object_type"], item.get("sha256")) for item in versions}
            for entry in captured:
                key = (entry["environment"], entry["owner"], entry["name"], entry["object_type"], entry.get("sha256"))
                if key in known:
                    for item in versions:
                        if (item["environment"], item["owner"], item["name"], item["object_type"], item.get("sha256")) == key:
                            item["last_seen_at"] = entry["captured_at"]
                            item["last_seen_run"] = run_id
                    continue
                known.add(key)
                number = 1 + sum(
                    1
                    for item in versions
                    if (item["environment"], item["owner"], item["name"], item["object_type"]) == key[:4]
                )
                versions.append(
                    {
                        **entry,
                        "version": number,
                        "first_seen_run": run_id,
                        "last_seen_run": run_id,
                        "last_seen_at": entry["captured_at"],
                    }
                )
            self._write("versions", versions)

    def versions(self) -> list[dict]:
        with _LOCK:
            return self._read("versions")
