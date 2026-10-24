"""Validation results, reviewer decisions, and approval decisions for one run.

Stored in runs/<id>/review.json. Records are updated in place, and every
change also appends an event, so the history of each record is kept.
"""

import threading
from uuid import uuid4

from oracle_impact.infrastructure.storage.registry import utc_now
from oracle_impact.infrastructure.storage.run_store import RunStore

SCHEMA_VERSION = 1
VALIDATION_STATUSES = ("not_executed", "passed", "failed", "blocked")
REVIEW_DECISIONS = ("accepted", "not_an_issue", "confirmed_issue", "needs_information")
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

    def save_validation(self, run_id: str, record: dict, actor: dict) -> dict:
        with _LOCK:
            payload = self.read(run_id)
            existing = next((item for item in payload["validations"] if item["id"] == record.get("id")), None)
            now = utc_now()
            if existing is None:
                stored = {**record, "id": str(uuid4()), "created_at": now, "created_by": actor, "updated_at": now, "updated_by": actor}
                payload["validations"].append(stored)
                action = "validation.create"
            else:
                before = {key: existing.get(key) for key in record}
                existing.update({**record, "id": existing["id"], "updated_at": now, "updated_by": actor})
                stored = existing
                action = "validation.update"
                record = {"before": before, "after": {key: stored.get(key) for key in before}}
            payload["events"].append({"id": str(uuid4()), "at": now, "action": action, "actor": actor, "record_id": stored["id"], "finding_id": stored.get("finding_id"), "detail": record})
            self._save(run_id, payload)
            return stored

    def save_review(self, run_id: str, record: dict, actor: dict) -> dict:
        with _LOCK:
            payload = self.read(run_id)
            now = utc_now()
            stored = {**record, "id": str(uuid4()), "at": now, "actor": actor}
            payload["reviews"].append(stored)
            payload["events"].append({"id": str(uuid4()), "at": now, "action": "review.record", "actor": actor, "record_id": stored["id"], "finding_id": stored.get("finding_id"), "detail": record})
            self._save(run_id, payload)
            return stored

    def save_approval(self, run_id: str, record: dict, actor: dict) -> dict:
        with _LOCK:
            payload = self.read(run_id)
            now = utc_now()
            stored = {**record, "id": str(uuid4()), "at": now, "actor": actor}
            payload["approvals"].append(stored)
            payload["events"].append({"id": str(uuid4()), "at": now, "action": "approval.record", "actor": actor, "record_id": stored["id"], "detail": record})
            self._save(run_id, payload)
            return stored
