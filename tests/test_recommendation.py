from pathlib import Path

from oracle_impact.services.recommendation import decide
from oracle_impact.models.enums import Recommendation, Severity
from oracle_impact.models.report import Finding


def _finding(**kwargs) -> Finding:
    payload = {
        "id": "F-1",
        "changed_object": "APP.PKG",
        "object_owner": "APP",
        "object_name": "PKG",
        "object_type": "PACKAGE",
        "source_database": "test",
        "change_category": "format",
        "evidence": "evidence",
        "severity": Severity.LOW,
        "confidence": "high",
        "status": "confirmed",
        "explanation": "explanation",
        "recommended_action": "Review.",
    }
    payload.update(kwargs)
    return Finding(**payload)


def test_breaking_signature_is_not_recommended():
    recommendation, _, actions = decide([_finding(attributes={"breaking": True}, severity=Severity.CRITICAL, change_category="interface")])
    assert recommendation == Recommendation.NOT_RECOMMENDED
    assert any("Do not promote" in action for action in actions)


def test_investigator_severity_does_not_change_the_decision():
    recommendation, _, _ = decide(
        [
            _finding(),
            _finding(id="AI-1", origin="investigator", severity=Severity.CRITICAL, attributes={"blocks_eligibility": True}),
        ]
    )
    assert recommendation == Recommendation.ELIGIBLE_FOR_REVIEW


def test_low_findings_remain_conditional():
    recommendation, _, actions = decide([_finding()])
    assert recommendation == Recommendation.ELIGIBLE_FOR_REVIEW
    assert any("human approval" in action.lower() for action in actions)


def test_blocking_reasons_use_plain_sentences():
    from types import SimpleNamespace

    from oracle_impact.infrastructure.llm.plain_text import plain_reasons

    findings = [
        _finding(
            id="F-795E0DD0",
            object_name="PAY_PKG",
            change_category="interface",
            dependency_path=["IMPACT_LAB.PAYMENT_API -> IMPACT_LAB.PAY_PKG.RELEASE_PAYMENT"],
            explanation=(
                "RELEASE_PAYMENT gained parameter(s) P_APPROVE without a compatible defaulted suffix. "
                "Unupdated callers: IMPACT_LAB.PAYMENT_API."
            ),
            attributes={"breaking": True},
            severity=Severity.CRITICAL,
        ),
        _finding(
            id="F-6295A365",
            object_name="PAY_PKG",
            change_category="data",
            dependency_path=["IMPACT_LAB.PAY_PKG.RELEASE_PAYMENT", "IMPACT_LAB.PAYMENT_AUDIT"],
            explanation=(
                "INSERT references IMPACT_LAB.PAYMENT_AUDIT, which was not found in Production. "
                "Static inspection cannot prove which rows the statement changes at runtime."
            ),
            attributes={"missing_object": True},
            severity=Severity.CRITICAL,
        ),
        _finding(
            id="F-46EB1887",
            object_name="PAY_PKG",
            change_category="transaction",
            explanation=(
                "RELEASE_PAYMENT transaction sequence changed from ('UPDATE', 'COMMIT') to "
                "('UPDATE', 'COMMIT', 'INSERT'). A moved commit can leave earlier statements durable "
                "if a later statement fails."
            ),
            attributes={"partial_commit_hazard": True},
            severity=Severity.HIGH,
        ),
    ]
    report = SimpleNamespace(
        findings=findings,
        recommendation_reasons=[f"{finding.id}: {finding.explanation}" for finding in findings],
    )
    items = plain_reasons(report)
    assert [item["id"] for item in items] == ["F-795E0DD0", "F-6295A365", "F-46EB1887"]
    assert items[0]["text"] == (
        "RELEASE_PAYMENT now requires P_APPROVE. IMPACT_LAB.PAYMENT_API still uses the old arguments."
    )
    assert items[1]["text"] == (
        "RELEASE_PAYMENT inserts into IMPACT_LAB.PAYMENT_AUDIT, but Production does not have that object."
    )
    assert "Static inspection" not in items[1]["text"]
    assert items[2]["text"] == (
        "RELEASE_PAYMENT saves its update and then inserts. If the insert fails, the update is already saved."
    )
    assert "('UPDATE'" not in items[2]["text"]


def test_env_file_is_gitignored():
    ignored = Path(".gitignore").read_text(encoding="utf-8")
    assert ".env" in ignored.splitlines()
