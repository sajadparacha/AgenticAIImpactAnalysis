"""One row per in-scope object: what changed, and which environments it was captured in.

Classification is a direct comparison of the captured checksums. There is no
record of approved changes, so nothing here is called planned, and a checksum
that differs is reported as a difference, not as drift against a baseline.
"""

from oracle_impact.models.report import Report

_CHANGED = {"modified", "created", "deleted", "formatting"}
_ENVIRONMENTS = ("test", "production")


def object_key(owner: str, name: str, object_type: str) -> str:
    return f"{owner}.{name}:{object_type}"


def classify_changes(report: Report) -> dict:
    objects = [_row(state) for state in report.object_states]
    return {
        "objects": objects,
        "recorded": bool(report.object_states),
        "changed": sum(1 for item in objects if item["change_type"] in _CHANGED),
    }


def _row(state) -> dict:
    owner, name, object_type = state.owner, state.name, state.object_type
    hashes = {
        item.environment: (item.sha256 if item.found else None)
        for item in state.environments
        if not item.inaccessible and item.environment in _ENVIRONMENTS
    }
    return {
        "key": object_key(owner, name, object_type),
        "qualified": f"{owner}.{name}",
        "owner": owner,
        "name": name,
        "object_type": object_type,
        "change_type": state.change_type,
        "hashes": hashes,
        "environments": [item.model_dump() for item in state.environments if item.environment in _ENVIRONMENTS],
    }
