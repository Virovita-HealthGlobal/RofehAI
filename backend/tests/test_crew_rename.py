"""
Tests for crew display-name overrides (rename an existing synthetic crew
slot, e.g. for a live pilot session with real reviewers).

Deliberately scoped: this does NOT add or remove crew members, only
relabels an existing one -- the underlying synthetic health-signal
generation stays keyed by the original crew_id, untouched.

Covers the real gap found while building this: crew names were being
resolved in three different places (the /crew listing, the fleet-wide
agent's grouping) and only fixing the listing endpoint would have left
the agent showing stale names.
"""

from fastapi.testclient import TestClient

from app.core import db
from app.main import app

client = TestClient(app)


def test_rename_requires_admin_key():
    r = client.put("/api/crew/crew-01/name", json={"display_name": "Dr. Test"})
    assert r.status_code == 503  # no ROFEHAI_ADMIN_KEY configured at all


def test_rename_requires_correct_admin_key(monkeypatch):
    monkeypatch.setenv("ROFEHAI_ADMIN_KEY", "correct-key")
    try:
        r = client.put(
            "/api/crew/crew-01/name",
            headers={"X-API-Key": "wrong-key"},
            json={"display_name": "Dr. Test"},
        )
        assert r.status_code == 401
    finally:
        monkeypatch.delenv("ROFEHAI_ADMIN_KEY", raising=False)


def test_rename_rejects_unknown_crew_id(monkeypatch):
    monkeypatch.setenv("ROFEHAI_ADMIN_KEY", "test-key")
    try:
        r = client.put(
            "/api/crew/not-a-real-crew-id/name",
            headers={"X-API-Key": "test-key"},
            json={"display_name": "Dr. Test"},
        )
        assert r.status_code == 404
    finally:
        monkeypatch.delenv("ROFEHAI_ADMIN_KEY", raising=False)


def test_rename_actually_changes_the_roster_listing(monkeypatch):
    monkeypatch.setenv("ROFEHAI_ADMIN_KEY", "test-key")
    try:
        r = client.put(
            "/api/crew/crew-01/name",
            headers={"X-API-Key": "test-key"},
            json={"display_name": "Dr. Sarah Chen"},
        )
        assert r.status_code == 200

        roster = client.get("/api/crew").json()
        crew_01 = next(c for c in roster if c["id"] == "crew-01")
        assert crew_01["name"] == "Dr. Sarah Chen"

        # Every other crew member is untouched.
        others = [c for c in roster if c["id"] != "crew-01"]
        assert all(c["name"] != "Dr. Sarah Chen" for c in others)
    finally:
        db.clear_crew_name_override("crew-01")
        monkeypatch.delenv("ROFEHAI_ADMIN_KEY", raising=False)


def test_rename_does_not_change_the_crew_id_or_underlying_data(monkeypatch):
    """The whole point of this feature: it's a label change only. The
    real synthetic health data must still be reachable under the
    original crew_id, unaffected by the rename."""
    monkeypatch.setenv("ROFEHAI_ADMIN_KEY", "test-key")
    try:
        client.put(
            "/api/crew/crew-01/name",
            headers={"X-API-Key": "test-key"},
            json={"display_name": "Dr. Sarah Chen"},
        )
        risk_r = client.get("/api/crew/crew-01/risk/sans", params={"mission_day": 210})
        assert risk_r.status_code == 200
        assert risk_r.json()["crew_id"] == "crew-01"  # unchanged, only the display label changed
    finally:
        db.clear_crew_name_override("crew-01")
        monkeypatch.delenv("ROFEHAI_ADMIN_KEY", raising=False)


def test_reset_reverts_to_the_original_synthetic_name(monkeypatch):
    monkeypatch.setenv("ROFEHAI_ADMIN_KEY", "test-key")
    try:
        client.put(
            "/api/crew/crew-02/name",
            headers={"X-API-Key": "test-key"},
            json={"display_name": "Temporary Name"},
        )
        roster_before = client.get("/api/crew").json()
        assert next(c for c in roster_before if c["id"] == "crew-02")["name"] == "Temporary Name"

        reset_r = client.delete("/api/crew/crew-02/name", headers={"X-API-Key": "test-key"})
        assert reset_r.status_code == 200
        assert reset_r.json()["display_name"] == "J. Okafor"  # the real original synthetic name

        roster_after = client.get("/api/crew").json()
        assert next(c for c in roster_after if c["id"] == "crew-02")["name"] == "J. Okafor"
    finally:
        db.clear_crew_name_override("crew-02")
        monkeypatch.delenv("ROFEHAI_ADMIN_KEY", raising=False)


def test_renamed_crew_member_shows_new_name_in_fleet_wide_agent_response(monkeypatch):
    """Regression test for a real gap found while building this: the
    fleet-wide agent's answer composition built its own crew-name lookup
    directly from the static roster, bypassing any override. A rename
    must be visible everywhere a crew member's name is shown, not just
    the /crew listing."""
    from app.core import agent

    monkeypatch.setenv("ROFEHAI_ADMIN_KEY", "test-key")
    try:
        client.put(
            "/api/crew/crew-03/name",
            headers={"X-API-Key": "test-key"},
            json={"display_name": "Dr. Renamed Person"},
        )

        fetched = agent._FetchedData()
        agent._execute_tool("get_risk", {"crew_id": "crew-03", "domain_id": "sans"}, None, fetched)
        tool_input = {
            "opening_tone": "one_or_more_elevated",
            "sections": [{"crew_id": "crew-03", "domain_id": "sans"}],
        }
        answer, sources = agent._compose_final_answer(None, tool_input, fetched)
        assert "Dr. Renamed Person" in answer
        assert "S. Ito" not in answer  # the stale original name must not appear
    finally:
        db.clear_crew_name_override("crew-03")
        monkeypatch.delenv("ROFEHAI_ADMIN_KEY", raising=False)


def test_no_overrides_means_unchanged_original_roster():
    """Sanity check: with nothing renamed, the roster is exactly the
    original synthetic list -- confirms the override mechanism is
    additive, not something that could corrupt the default state."""
    roster = client.get("/api/crew").json()
    names = {c["name"] for c in roster}
    assert names == {"A. Mendez", "J. Okafor", "S. Ito", "R. Kessler"}
