"""JSON run directories. Source snapshots stay on disk and out of git."""

import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel


class RunStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, run_id: str, packages: list[str], model: str = "", mode: str = "deterministic") -> None:
        path = self._dir(run_id)
        path.mkdir(parents=True, exist_ok=False)
        self.write_json(run_id, "request.json", {"packages": packages, "model": model, "mode": mode})
        self.write_status(run_id, "queued", detail="Analysis queued.")

    def write_status(
        self,
        run_id: str,
        status: str,
        error: str = "",
        detail: str = "",
        lines: list[str] | None = None,
        activity: dict | None = None,
    ) -> None:
        current = self.read_json(run_id, "status.json") or {}
        events = list(current.get("events") or [])
        trail = list(current.get("activity") or [])
        if activity and activity.get("text"):
            item = {"kind": activity.get("kind") or "status", "text": activity["text"]}
            if not trail or trail[-1] != item:
                trail.append(item)
            trail = trail[-40:]
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        if detail:
            events.append({"stage": status, "message": detail, "at": now})
            events = events[-50:]
        steps = dict(current.get("steps") or {})
        step = dict(steps.get(status) or {"summary": "", "lines": [], "started_at": now})
        step["updated_at"] = now
        if detail:
            step["summary"] = detail
        recorded = list(step.get("lines") or [])
        for line in lines or []:
            if line and line not in recorded:
                recorded.append(line)
        step["lines"] = recorded[-80:]
        steps[status] = step
        current.update(
            {
                "run_id": run_id,
                "status": status,
                "error": error,
                "detail": detail or current.get("detail", ""),
                "events": events,
                "steps": steps,
                "activity": trail,
            }
        )
        self.write_json(run_id, "status.json", current)

    def write_model(self, run_id: str, name: str, model: BaseModel) -> None:
        self.write_json(run_id, name, model.model_dump(mode="json"))

    def write_json(self, run_id: str, name: str, payload: dict | list) -> None:
        target = self._file(run_id, name)
        target.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def read_json(self, run_id: str, name: str) -> dict | None:
        target = self._file(run_id, name)
        if not target.exists():
            return None
        return json.loads(target.read_text(encoding="utf-8"))

    def list_runs(self) -> list[dict]:
        runs = []
        if not self.root.exists():
            return runs
        for path in self.root.iterdir():
            if not path.is_dir() or path.name.startswith(("_", ".")):
                continue
            status = self.read_json(path.name, "status.json") or {}
            request = self.read_json(path.name, "request.json") or {}
            report = self.read_json(path.name, "report.json") or {}
            requested = path / "request.json"
            runs.append(
                {
                    "run_id": path.name,
                    "status": status.get("status", "unknown"),
                    "packages": request.get("packages", []),
                    "mode": request.get("mode") or "deterministic",
                    "model": report.get("model_name") or "",
                    "recommendation": report.get("recommendation_label", ""),
                    "created_at": report.get("created_at", ""),
                    "report_version": report.get("report_version", ""),
                    "requested_ns": requested.stat().st_mtime_ns if requested.exists() else 0,
                }
            )
        runs.sort(key=lambda item: (item.get("created_at") or item["run_id"], item["requested_ns"]), reverse=True)
        return runs

    def _dir(self, run_id: str) -> Path:
        if "/" in run_id or "\\" in run_id or run_id in {"", ".", ".."}:
            raise ValueError("Invalid run id.")
        path = (self.root / run_id).resolve()
        if path.parent != self.root.resolve():
            raise ValueError("Invalid run id.")
        return path

    def _file(self, run_id: str, name: str) -> Path:
        if name not in {
            "status.json",
            "request.json",
            "snapshot.json",
            "report.json",
            "tool_log.json",
            "findings.json",
            "review.json",
        }:
            raise ValueError("Unknown run artifact.")
        return self._dir(run_id) / name
