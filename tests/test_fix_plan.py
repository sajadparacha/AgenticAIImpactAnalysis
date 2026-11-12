import re

from oracle_impact.analysis.action_scripts import apply_action_scripts
from oracle_impact.models.enums import EvidenceStatus, Severity
from oracle_impact.models.report import Finding
from oracle_impact.services.fix_plan import build_fix_plan, combined_script
from oracle_impact.services.readiness import evaluate_readiness, policy_for

from tests.support import client_for, login, run_demo


def _analyze(client, package: str) -> str:
    token = re.search(r'name="csrf_token_value" value="([^"]+)"', client.get("/").text).group(1)
    response = client.post("/analyses", data={"packages": package, "csrf_token_value": token})
    return re.search(r"/analyses/([0-9a-f-]+)/", str(response.url) + "/").group(1)


def _plan_for(tmp_path, package: str):
    report = run_demo(tmp_path, [package])
    apply_action_scripts(report.findings, report.dependency_edges)
    policy, _computed = policy_for(report)
    readiness = evaluate_readiness(report, policy, [], [], None)
    return report, readiness


def test_fix_plan_lists_every_open_item_in_release_order(tmp_path):
    client = client_for(tmp_path)
    login(client)
    run_id = _analyze(client, "DEMO.PKG_BILLING")
    assert f"/analyses/{run_id}/fix-plan" in client.get(f"/analyses/{run_id}/report").text
    assert "fix-plan" not in client.get(f"/analyses/{run_id}/export?format=html").text

    report = client.get(f"/analyses/{run_id}/export?format=json").json()
    open_items = [item["finding_id"] for item in report["policy"]["blockers"] + report["policy"]["validations"]]
    assert open_items

    page = client.get(f"/analyses/{run_id}/fix-plan").text
    assert all(finding_id in page for finding_id in open_items)
    headings = re.findall(r'<h2 id="phase-[a-z_]+">([^<]+)</h2>', page)
    assert headings == ["Fix the code in Test", "Prepare Production", "Test or justify", "Confirm with a new analysis", "Release"]
    assert "/?packages=DEMO.PKG_BILLING" in page
    assert f"/analyses/{run_id}/fix-plan.sql?environment=test" in page
    assert f"/analyses/{run_id}/fix-plan.sql?environment=production" in page

    test_script = client.get(f"/analyses/{run_id}/fix-plan.sql?environment=test")
    assert f'fix-plan-{run_id}-test.sql' in test_script.headers["content-disposition"]
    assert "ALTER PACKAGE DEMO.PKG_LEGACY COMPILE BODY;" in test_script.text
    assert "This application does not run this script." in test_script.text
    production = client.get(f"/analyses/{run_id}/fix-plan.sql?environment=production").text
    assert "BILLING_HISTORY" in production
    assert not any(line.startswith(("ALTER", "CREATE", "DROP")) for line in production.splitlines())


def test_fix_plan_rejects_unknown_runs_and_environments(tmp_path):
    client = client_for(tmp_path)
    login(client)
    run_id = _analyze(client, "DEMO.PKG_BILLING")
    assert client.get(f"/analyses/{run_id}/fix-plan.sql?environment=../../etc").status_code == 400
    assert client.get("/analyses/not-a-run/fix-plan").status_code == 404
    assert client.get("/analyses/not-a-run/fix-plan.sql").status_code == 404
    assert client.get("/analyses/../etc/fix-plan.sql").status_code == 404


def test_clean_report_has_only_release_steps_and_no_button(tmp_path):
    client = client_for(tmp_path)
    login(client)
    run_id = _analyze(client, "DEMO.PKG_STATUS")
    assert f"/analyses/{run_id}/fix-plan" not in client.get(f"/analyses/{run_id}/report").text
    page = client.get(f"/analyses/{run_id}/fix-plan").text
    assert "No blocking items or required tests are open" in page
    assert re.findall(r'<h2 id="phase-[a-z_]+">([^<]+)</h2>', page) == ["Release"]
    assert "fix-plan.sql?environment=" not in page
    assert "no script steps for Production" in client.get(f"/analyses/{run_id}/fix-plan.sql?environment=production").text


def test_grants_follow_the_deployment_and_ai_leads_are_not_steps(tmp_path):
    report, readiness = _plan_for(tmp_path, "DEMO.PKG_DISPATCH")
    lead = Finding(
        id="AI-1",
        changed_object="DEMO.PKG_DISPATCH",
        object_owner="DEMO",
        object_name="PKG_DISPATCH",
        object_type="PACKAGE BODY",
        source_database="test",
        change_category="data",
        affected_object="DEMO.SHADOW",
        evidence="lead",
        severity=Severity.HIGH,
        confidence="low",
        status=EvidenceStatus.POTENTIAL,
        explanation="lead",
        recommended_action="lead",
        origin="investigator",
        attributes={"missing_object": True, "action_scripts": [{"title": "x", "environment": "production", "sql": "SELECT 1 FROM dual;\n"}]},
    )
    report.findings.append(lead)
    plan = build_fix_plan(report, readiness, report_url=f"/analyses/{report.run_id}/report")

    steps = [step for section in plan["sections"] for step in section["steps"]]
    assert [step["number"] for step in steps] == list(range(1, plan["total"] + 1))
    assert not any(step["finding"] and step["finding"]["id"] == "AI-1" for step in steps)
    assert "AI-1" not in plan["text"] and "DEMO.SHADOW" not in combined_script(plan, "production")

    titles = [step["title"] for step in steps]
    deploy = titles.index("Deploy DEMO.PKG_DISPATCH to Production")
    grant = next(index for index, title in enumerate(titles) if title.startswith("Grant access"))
    create = next(index for index, title in enumerate(titles) if title.startswith("Create DEMO.NEXT_BATCH"))
    assert create < deploy < grant
    assert plan["blockers"] == readiness["open_blockers"] and plan["validations"] == readiness["open_validations"]
    open_ids = {entry["item"].finding_id for entry in readiness["blockers"] + readiness["validations"]}
    assert open_ids <= {step["finding"]["id"] for step in steps if step["finding"]}
