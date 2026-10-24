"""Cross-run records: object versions, trusted baselines, the approved change manifest, and business mappings.

Each file is a versioned JSON document written atomically. Entries are never
deleted; withdrawing or clearing one keeps it with a status and an audit event.
"""

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

SCHEMA_VERSION = 1
_FILES = {"versions", "baselines", "manifest", "mappings", "events"}
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

    def _event(self, action: str, actor: dict, detail: dict) -> None:
        events = self._read("events")
        events.append({"id": str(uuid4()), "at": utc_now(), "action": action, "actor": actor, "detail": detail})
        self._write("events", events)

    def events(self) -> list[dict]:
        with _LOCK:
            return self._read("events")

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

    def baselines(self) -> list[dict]:
        with _LOCK:
            return self._read("baselines")

    def set_baseline(self, entry: dict, actor: dict) -> dict:
        with _LOCK:
            baselines = self._read("baselines")
            for item in baselines:
                if item["status"] == "active" and _same(item, entry):
                    item["status"] = "superseded"
                    item["superseded_at"] = utc_now()
            record = {**entry, "id": str(uuid4()), "status": "active", "set_at": utc_now(), "set_by": actor}
            baselines.append(record)
            self._write("baselines", baselines)
            self._event("baseline.set", actor, {"id": record["id"], **entry})
            return record

    def clear_baseline(self, baseline_id: str, actor: dict, reason: str) -> bool:
        with _LOCK:
            baselines = self._read("baselines")
            for item in baselines:
                if item["id"] == baseline_id and item["status"] == "active":
                    item["status"] = "cleared"
                    item["cleared_at"] = utc_now()
                    item["cleared_by"] = actor
                    item["clear_reason"] = reason
                    self._write("baselines", baselines)
                    self._event("baseline.clear", actor, {"id": baseline_id, "reason": reason})
                    return True
            return False

    def manifest(self) -> list[dict]:
        with _LOCK:
            return self._read("manifest")

    def add_manifest(self, entry: dict, actor: dict) -> dict:
        with _LOCK:
            manifest = self._read("manifest")
            record = {**entry, "id": str(uuid4()), "status": "approved", "approved_at": utc_now(), "approved_by": actor}
            manifest.append(record)
            self._write("manifest", manifest)
            self._event("manifest.approve", actor, {"id": record["id"], **entry})
            return record

    def withdraw_manifest(self, entry_id: str, actor: dict, reason: str) -> bool:
        with _LOCK:
            manifest = self._read("manifest")
            for item in manifest:
                if item["id"] == entry_id and item["status"] == "approved":
                    item["status"] = "withdrawn"
                    item["withdrawn_at"] = utc_now()
                    item["withdrawn_by"] = actor
                    item["withdraw_reason"] = reason
                    self._write("manifest", manifest)
                    self._event("manifest.withdraw", actor, {"id": entry_id, "reason": reason})
                    return True
            return False

    def mappings(self) -> list[dict]:
        with _LOCK:
            return self._read("mappings")

    def add_mapping(self, entry: dict, actor: dict) -> dict:
        with _LOCK:
            mappings = self._read("mappings")
            record = {**entry, "id": str(uuid4()), "status": "active", "updated_at": utc_now(), "updated_by": actor}
            mappings.append(record)
            self._write("mappings", mappings)
            self._event("mapping.add", actor, {"id": record["id"], **entry})
            return record

    def retire_mapping(self, mapping_id: str, actor: dict) -> bool:
        with _LOCK:
            mappings = self._read("mappings")
            for item in mappings:
                if item["id"] == mapping_id and item["status"] == "active":
                    item["status"] = "retired"
                    item["retired_at"] = utc_now()
                    item["retired_by"] = actor
                    self._write("mappings", mappings)
                    self._event("mapping.retire", actor, {"id": mapping_id})
                    return True
            return False


def _same(left: dict, right: dict) -> bool:
    return all(left.get(key) == right.get(key) for key in ("environment", "owner", "name", "object_type"))
