from pathlib import Path

from tests.support import client_for, login


def test_health_hides_secrets(tmp_path: Path):
    client = client_for(tmp_path)
    response = client.get("/health")
    assert response.status_code == 200
    body = response.text
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["llm_egress_enabled"] is False
    assert "should-not-leak" not in body
    assert "dsn" not in body.lower()
    assert "password" not in body.lower()


def test_dashboard_requires_sign_in_and_renders(tmp_path: Path):
    client = client_for(tmp_path)
    anonymous = client.get("/", follow_redirects=False)
    assert anonymous.status_code == 303
    assert anonymous.headers["location"] == "/login"
    login(client)
    page = client.get("/")
    assert page.status_code == 200
    assert "New analysis" in page.text
    assert "Fixture catalog" in page.text


def test_templates_have_no_inline_style_or_script():
    root = Path("src/oracle_impact/views/templates")
    for path in root.glob("*.html"):
        text = path.read_text(encoding="utf-8")
        assert 'style="' not in text, path.name
        for line in text.splitlines():
            stripped = line.strip()
            if "<script" in stripped and "src=" not in stripped:
                raise AssertionError(f"{path.name} has an inline script")


def test_controllers_do_not_contain_sql():
    root = Path("src/oracle_impact/controllers")
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8").upper()
        assert "ALL_SOURCE" not in text
        assert "SELECT " not in text
