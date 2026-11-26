"""Run store, registry, and review store."""

import json

import pytest

from oracle_impact.infrastructure.storage.registry import Registry
from oracle_impact.infrastructure.storage.review_store import ReviewStore, empty_review
from oracle_impact.infrastructure.storage.run_store import RunStore

APPROVER = {"role": "approver", "name": "Alex", "declared": True}


@pytest.mark.parametrize("run_id", ["", ".", "..", "../x", "a/b", "a\\b", "/etc"])
def test_run_store_rejects_unsafe_run_ids(tmp_path, run_id):
    store = RunStore(tmp_path)
    with pytest.raises(ValueError):
        store.read_json(run_id, "report.json")


def test_run_store_rejects_unknown_artifacts(tmp_path):
    store = RunStore(tmp_path)
    store.create("run-1", ["APP.PKG"])
    with pytest.raises(ValueError):
        store.write_json("run-1", "secrets.json", {})
    with pytest.raises(ValueError):
        store.read_json("run-1", "../request.json")


def test_run_store_lists_runs_newest_first_and_skips_private_directories(tmp_path):
    store = RunStore(tmp_path)
    store.create("older", ["APP.PKG"])
    store.write_json("older", "report.json", {"created_at": "2026-10-01T00:00:00+00:00", "recommendation_label": "X"})
    store.create("newer", ["APP.PKG"])
    store.write_json("newer", "report.json", {"created_at": "2026-10-02T00:00:00+00:00"})
    (tmp_path / "_registry").mkdir()
    (tmp_path / ".cache").mkdir()
    runs = store.list_runs()
    assert [run["run_id"] for run in runs] == ["newer", "older"]
    assert runs[1]["recommendation"] == "X" and runs[0]["packages"] == ["APP.PKG"]
    assert all(run["requested_ns"] > 0 for run in runs)


def test_run_store_breaks_same_second_ties_by_request_time(tmp_path):
    store = RunStore(tmp_path)
    for run_id in ("first", "second"):
        store.create(run_id, ["APP.PKG"])
        store.write_json(run_id, "report.json", {"created_at": "2026-10-01T00:00:00+00:00"})
    order = [run["run_id"] for run in store.list_runs()]
    assert order == ["second", "first"]


def test_status_keeps_bounded_events_steps_and_activity(tmp_path):
    store = RunStore(tmp_path)
    store.create("run-1", ["APP.PKG"])
    for index in range(60):
        store.write_status("run-1", "comparing", detail=f"step {index}", lines=[f"line {index}", f"line {index}"], activity={"text": f"a{index}"})
    store.write_status("run-1", "comparing", activity={"text": "a59"})
    status = store.read_json("run-1", "status.json")
    assert len(status["events"]) == 50 and status["events"][-1]["message"] == "step 59"
    assert len(status["activity"]) == 40 and status["activity"][-1]["text"] == "a59"
    assert status["steps"]["comparing"]["lines"].count("line 59") == 1
    assert status["steps"]["queued"]["summary"] == "Analysis queued."


def test_status_records_error_kind_and_failed_stage(tmp_path):
    store = RunStore(tmp_path)
    store.create("run-1", ["APP.PKG"])
    store.write_status("run-1", "snapshotting", detail="Reading source.")
    store.write_status("run-1", "failed", error="Production is unreachable.", detail="Production is unreachable.", error_kind="connection")
    status = store.read_json("run-1", "status.json")
    assert status["error_kind"] == "connection"
    assert status["failed_stage"] == "snapshotting"


def test_list_runs_exposes_captured_recommendation_value(tmp_path):
    store = RunStore(tmp_path)
    store.create("run-1", ["APP.PKG"])
    store.write_json("run-1", "report.json", {"created_at": "2026-10-01T00:00:00+00:00", "recommendation": "eligible_for_review", "recommendation_label": "Deployment recommended"})
    runs = store.list_runs()
    assert runs[0]["recommendation"] == "Deployment recommended"
    assert runs[0]["recommendation_value"] == "eligible_for_review"


def test_registry_numbers_object_versions_and_tracks_last_seen(tmp_path):
    registry = Registry(tmp_path / "reg")
    entry = {"environment": "test", "owner": "APP", "name": "PKG", "object_type": "PACKAGE BODY", "sha256": "a" * 64, "captured_at": "t1"}
    registry.record_versions("run-1", [entry])
    registry.record_versions("run-2", [{**entry, "captured_at": "t2"}])
    registry.record_versions("run-3", [{**entry, "sha256": "b" * 64, "captured_at": "t3"}])
    versions = registry.versions()
    assert [item["version"] for item in versions] == [1, 2]
    assert versions[0]["first_seen_run"] == "run-1" and versions[0]["last_seen_run"] == "run-2" and versions[0]["last_seen_at"] == "t2"
    assert versions[1]["first_seen_run"] == "run-3"


def test_registry_files_are_versioned_json_and_names_are_restricted(tmp_path):
    registry = Registry(tmp_path)
    registry.record_versions(
        "run-1",
        [{"environment": "test", "owner": "APP", "name": "PKG", "object_type": "PACKAGE", "sha256": "a" * 64, "captured_at": "t1"}],
    )
    payload = json.loads((tmp_path / "versions.json").read_text())
    assert payload["schema_version"] == 1 and len(payload["items"]) == 1
    assert not list(tmp_path.glob("*.tmp"))
    with pytest.raises(ValueError):
        registry._read("../../etc/passwd")
    with pytest.raises(ValueError):
        registry._read("mappings")
    assert Registry(tmp_path / "missing").versions() == []


def test_review_store_appends_approvals(tmp_path):
    store = RunStore(tmp_path)
    store.create("run-1", ["APP.PKG"])
    reviews = ReviewStore(store)
    assert reviews.read("run-1") == empty_review()
    reviews.save_approval("run-1", {"decision": "on_hold", "notes": "Wait"}, APPROVER)
    payload = reviews.read("run-1")
    assert payload["validations"] == [] and payload["reviews"] == []
    assert payload["approvals"][0]["actor"] == APPROVER
    assert [event["action"] for event in payload["events"]] == ["approval.record"]


def test_review_store_fills_missing_sections_of_older_files(tmp_path):
    store = RunStore(tmp_path)
    store.create("run-1", ["APP.PKG"])
    store.write_json("run-1", "review.json", {"validations": [{"id": "V1"}]})
    payload = ReviewStore(store).read("run-1")
    assert payload["validations"] == [{"id": "V1"}] and payload["approvals"] == [] and payload["events"] == []
