"""Approval decisions for one run.

Stored in runs/<id>/review.json. Older files may still contain validation
results and reviewer decisions; this application no longer writes them.
Every approval appends an event.
"""

import threading
from uuid import uuid4

from oracle_impact.infrastructure.storage.registry import utc_now
from oracle_impact.infrastructure.storage.run_store import RunStore

SCHEMA_VERSION = 1
APPROVAL_DECISIONS = ("approved", "rejected", "on_hold")
_LOCK = threading.RLock()


def empty_review() -> dict:
    return {"schema_version": SCHEMA_VERSION, "validations": [], "reviews": [], "approvals": [], "events": []}


class ReviewStore:
    def __init__(self, runs: RunStore) -> None:
        self.runs = runs

    def read(self, run_id: str) -> dict:
        payload = self.runs.read_json(run_id, "review.json")
        if payload is None:
            return empty_review()
        merged = empty_review()
        merged.update(payload)
        return merged

    def _save(self, run_id: str, payload: dict) -> None:
        self.runs.write_json(run_id, "review.json", payload)

    def save_approval(self, run_id: str, record: dict, actor: dict) -> dict:
        with _LOCK:
            payload = self.read(run_id)
            now = utc_now()
            stored = {**record, "id": str(uuid4()), "at": now, "actor": actor}
            payload["approvals"].append(stored)
            payload["events"].append({"id": str(uuid4()), "at": now, "action": "approval.record", "actor": actor, "record_id": stored["id"], "detail": record})
            self._save(run_id, payload)
            return stored
