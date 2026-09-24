from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_list_crew():
    r = client.get("/api/crew")
    assert r.status_code == 200
    assert len(r.json()) == 4


def test_risk_for_high_susceptibility_crew_member():
    # crew-01 has high SANS susceptibility in the synthetic profile
    r = client.get("/api/crew/crew-01/risk/sans", params={"mission_day": 180})
    assert r.status_code == 200
    body = r.json()
    assert 0 <= body["current_risk"] <= 1
    assert 0 <= body["confidence"] <= 1
    assert body["condition"] == "sans"
    assert body["domain_status"] == "active"


def test_fallback_when_insufficient_data():
    r = client.get("/api/crew/crew-01/risk/sans", params={"mission_day": 3})
    assert r.status_code == 200
    body = r.json()
    assert body["is_fallback"] is True


def test_countermeasure_log_roundtrip():
    entry = {"crew_id": "crew-01", "countermeasure_id": "lbnp-protocol", "mission_day": 120}
    r = client.post("/api/countermeasures/log", json=entry)
    assert r.status_code == 200
    assert r.json()["logged"] is True
    r2 = client.get("/api/countermeasures/log", params={"crew_id": "crew-01"})
    assert any(e["crew_id"] == "crew-01" for e in r2.json())


def test_observation_validation_rejects_out_of_range_values():
    bad_obs = {"crew_id": "crew-01", "mission_day": 50, "signals": {"onsd_mm": 999.0}}
    r = client.post("/api/observations", json=bad_obs)
    assert r.status_code == 422


def test_observation_rejects_unknown_crew_id():
    obs = {"crew_id": "not-a-real-crew-member", "mission_day": 50, "signals": {"onsd_mm": 5.0}}
    r = client.post("/api/observations", json=obs)
    assert r.status_code == 404


def test_observation_accepts_valid_signals():
    obs = {"crew_id": "crew-02", "mission_day": 12, "signals": {"onsd_mm": 5.0, "hrv_ms": 60.0}}
    r = client.post("/api/observations", json=obs)
    assert r.status_code == 200
    assert r.json()["signals"]["onsd_mm"] == 5.0


def test_gate_check_correct_code():
    r = client.post("/api/gate/check", json={"code": "rofehai-preview-2026"})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_gate_check_wrong_code():
    r = client.post("/api/gate/check", json={"code": "wrong-code"})
    assert r.status_code == 401


def test_gate_check_custom_code(monkeypatch):
    monkeypatch.setenv("ROFEHAI_GATE_CODE", "custom-secret")
    try:
        r_old = client.post("/api/gate/check", json={"code": "rofehai-preview-2026"})
        assert r_old.status_code == 401
        r_new = client.post("/api/gate/check", json={"code": "custom-secret"})
        assert r_new.status_code == 200
    finally:
        monkeypatch.delenv("ROFEHAI_GATE_CODE", raising=False)


def test_gate_check_falls_back_to_default_when_env_var_is_empty_string(monkeypatch):
    """Regression test: docker-compose's `${VAR:-}` substitution sets an
    env var to an empty string when unset in .env, rather than leaving it
    absent. os.environ.get(VAR, default) does NOT fall back on an empty
    string (only on a missing key), which broke this in production -- see
    the matching fix in db.py's DATABASE_URL. Empty string must behave
    the same as unset."""
    monkeypatch.setenv("ROFEHAI_GATE_CODE", "")
    try:
        r = client.post("/api/gate/check", json={"code": "rofehai-preview-2026"})
        assert r.status_code == 200
    finally:
        monkeypatch.delenv("ROFEHAI_GATE_CODE", raising=False)


def test_api_key_enforced_when_configured(monkeypatch):
    monkeypatch.setenv("ROFEHAI_API_KEY", "test-secret")
    try:
        r_no_key = client.get("/api/crew")
        assert r_no_key.status_code == 401

        r_wrong_key = client.get("/api/crew", headers={"X-API-Key": "wrong"})
        assert r_wrong_key.status_code == 401

        r_right_key = client.get("/api/crew", headers={"X-API-Key": "test-secret"})
        assert r_right_key.status_code == 200
    finally:
        monkeypatch.delenv("ROFEHAI_API_KEY", raising=False)


# --- Domain registry tests (new in the multi-domain refactor) -----------

def test_domains_endpoint_lists_active_and_architecture_ready():
    r = client.get("/api/domains")
    assert r.status_code == 200
    body = r.json()
    ids = {d["id"] for d in body}
    statuses = {d["id"]: d["status"] for d in body}
    assert "sans" in ids and statuses["sans"] == "active"
    assert "immune_dysregulation" in ids and statuses["immune_dysregulation"] == "active"
    # At least one architecture-ready placeholder domain must be present.
    assert any(s == "architecture_ready" for s in statuses.values())


def test_unknown_domain_returns_404():
    r = client.get("/api/crew/crew-01/risk/not-a-real-domain", params={"mission_day": 100})
    assert r.status_code == 404


def test_architecture_ready_domain_returns_explicit_not_implemented():
    r = client.get("/api/crew/crew-01/risk/cardiovascular", params={"mission_day": 180})
    assert r.status_code == 200
    body = r.json()
    assert body["is_fallback"] is True
    assert body["domain_status"] == "architecture_ready"
    assert "architecture ready" in body["narrative"].lower()


def test_get_all_risk_only_scores_active_domains():
    r = client.get("/api/crew/crew-01/risk", params={"mission_day": 180})
    assert r.status_code == 200
    ids = {row["condition"] for row in r.json()}
    assert ids == {"sans", "immune_dysregulation"}
