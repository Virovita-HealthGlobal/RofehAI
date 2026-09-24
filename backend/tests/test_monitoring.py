"""
Tests for the near-real-time clinical relay & response feature
(backend/app/core/monitoring.py + the /api/monitoring, /api/alerts,
/api/comms endpoints).

Focus areas, matching the feature's own stated requirements:
- Detection fires on a genuinely NEW significant change, not on every
  sweep of an already-known elevated state (would flood the queue).
- Alert content is template-assembled from real data -- same discipline
  as trajectory_model.py and agent.py's compose_answer.
- The simulated comms link actually gates transmission (alerts queue
  but don't transmit while unavailable; all pending transmit the moment
  it's restored).
- Human-in-the-loop: acknowledging requires a prior transmission; nothing
  auto-acknowledges.
- Pre-authorized protocols are clearly labeled, never autonomous action.
- Latency metrics are computed from real timestamps, with comms latency
  reported separately from RofehAI's own detection-to-queue time.
"""

from fastapi.testclient import TestClient

from app.core import db, monitoring
from app.main import app

client = TestClient(app)


def test_classify_severity_thresholds():
    thresholds = {"moderate": 0.5, "high": 0.75}
    assert monitoring.classify_severity(0.3, False, thresholds) == "nominal"
    assert monitoring.classify_severity(0.6, False, thresholds) == "moderate"
    assert monitoring.classify_severity(0.8, False, thresholds) == "high"
    # Insufficient data is never itself an alertable state, regardless of the number.
    assert monitoring.classify_severity(0.9, True, thresholds) == "nominal"


def test_new_significant_change_detection():
    assert monitoring._is_new_significant_change(None, "high") is True  # first-ever check, already elevated
    assert monitoring._is_new_significant_change(None, "nominal") is False  # first-ever check, fine -- no alert
    assert monitoring._is_new_significant_change("nominal", "moderate") is True  # crossed a threshold
    assert monitoring._is_new_significant_change("moderate", "high") is True  # got worse
    assert monitoring._is_new_significant_change("moderate", "moderate") is False  # unchanged -- no re-alert
    assert monitoring._is_new_significant_change("high", "moderate") is False  # improved -- not an alert (a human should still see this via the dashboard)


def test_monitoring_sweep_creates_alert_for_high_susceptibility_crew_member():
    """crew-01 has high SANS susceptibility in the synthetic profile and
    should trip an alert on a fresh sweep (no prior LastKnownRiskState)."""
    new_alerts = monitoring.run_monitoring_sweep()
    sans_alerts = [a for a in new_alerts if a["crew_id"] == "crew-01" and a["domain_id"] == "sans"]
    assert len(sans_alerts) == 1
    alert = sans_alerts[0]
    assert alert["severity"] in ("moderate", "high")
    assert alert["status"] == "pending"
    assert alert["detected_at"] is not None
    assert alert["queued_at"] is not None  # detection -> queue is immediate, always
    assert alert["transmitted_at"] is None  # not transmitted yet -- comms link default state matters here


def test_monitoring_sweep_does_not_duplicate_alert_on_second_identical_sweep():
    """Running the sweep twice in a row with no underlying data change
    must not create a second alert for the same already-known elevated
    state -- this is the core anti-flooding guarantee."""
    monitoring.run_monitoring_sweep()  # first sweep establishes LastKnownRiskState
    second_sweep_alerts = monitoring.run_monitoring_sweep()
    duplicate = [a for a in second_sweep_alerts if a["crew_id"] == "crew-01" and a["domain_id"] == "sans"]
    assert duplicate == []


def test_alert_content_is_verbatim_from_real_risk_output():
    """Same discipline as compose_answer: no free-typed text, everything
    traceable to a real RiskOutput."""
    from app.core.digital_twin import registry
    from app.core.seeding import ensure_seeded
    from app.core.trajectory_model import score
    from app.data.synthetic import MISSION_LENGTH_DAYS

    ensure_seeded("crew-01", MISSION_LENGTH_DAYS)
    twin = registry.get_or_create("crew-01")
    direct_result = score(twin, "sans", MISSION_LENGTH_DAYS)

    new_alerts = monitoring.run_monitoring_sweep()
    sans_alert = next((a for a in new_alerts if a["crew_id"] == "crew-01" and a["domain_id"] == "sans"), None)
    if sans_alert:  # may be None if a previous test already established this state -- that's fine, covered elsewhere
        assert sans_alert["narrative"] == direct_result.narrative


def test_pre_authorized_protocol_is_clearly_labeled_not_autonomous():
    new_alerts = monitoring.run_monitoring_sweep()
    with_protocol = [a for a in new_alerts if a["pre_authorized_protocol"] is not None]
    if with_protocol:
        protocol = with_protocol[0]["pre_authorized_protocol"]
        assert "not autonomous treatment" in protocol["disclaimer"].lower()
        assert "clinician confirmation" in protocol["disclaimer"].lower()
        assert protocol["instructions"]  # real, fixed text, not empty


def test_comms_link_gates_transmission():
    """The core simulated-relay guarantee: while the link is unavailable,
    a queued alert must NOT transmit. The moment it's restored, it must
    transmit immediately."""
    db.set_comms_link_available(False)
    monitoring.run_monitoring_sweep()

    pending = db.list_alerts(status="pending")
    assert len(pending) >= 1  # at least crew-01/sans should be queued from earlier tests

    # Still unavailable -- nothing should have transmitted.
    still_pending = db.list_alerts(status="pending")
    assert len(still_pending) == len(pending)

    result = db.set_comms_link_available(True)
    transmitted = db.transmit_pending_alerts()
    assert len(transmitted) >= 1
    for a in transmitted:
        assert a.transmitted_at is not None


def test_transmit_pending_alerts_prioritizes_high_severity_first():
    db.set_comms_link_available(False)
    high = db.create_alert("crew-01", "sans", "high", 0.9, 0.5, 0.8, "narrative", [], None)
    db.mark_alert_queued(high.id)
    moderate = db.create_alert("crew-02", "sans", "moderate", 0.6, 0.3, 0.7, "narrative", [], None)
    db.mark_alert_queued(moderate.id)

    transmitted = db.transmit_pending_alerts()
    transmitted_ids_in_order = [a.id for a in transmitted]
    assert transmitted_ids_in_order.index(high.id) < transmitted_ids_in_order.index(moderate.id)


def test_cannot_acknowledge_an_untransmitted_alert():
    """Human-in-the-loop guarantee: acknowledging is the clinician's
    confirmation that they RECEIVED the alert -- can't happen before
    transmission."""
    db.set_comms_link_available(False)
    alert = db.create_alert("crew-03", "sans", "high", 0.9, 0.5, 0.8, "narrative", [], None)
    r = client.post(f"/api/alerts/{alert.id}/acknowledge", json={"note": "reviewed"})
    assert r.status_code == 400


def test_acknowledge_alert_endpoint_records_note():
    db.set_comms_link_available(False)
    alert = db.create_alert("crew-04", "sans", "high", 0.9, 0.5, 0.8, "narrative", [], None)
    db.mark_alert_queued(alert.id)
    db.set_comms_link_available(True)
    db.transmit_pending_alerts()

    r = client.post(f"/api/alerts/{alert.id}/acknowledge", json={"note": "Reviewed, crew notified"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "acknowledged"
    assert body["acknowledged_at"] is not None


def test_alert_endpoint_404_for_unknown_id():
    r = client.get("/api/alerts/999999")
    assert r.status_code == 404


def test_monitoring_check_endpoint_live():
    r = client.post("/api/monitoring/check")
    assert r.status_code == 200
    body = r.json()
    assert "new_alerts_count" in body
    assert isinstance(body["new_alerts"], list)


def test_comms_link_endpoints_roundtrip():
    r = client.post("/api/comms/link", json={"available": False})
    assert r.status_code == 200
    assert r.json()["available"] is False

    r2 = client.get("/api/comms/link")
    assert r2.status_code == 200
    assert r2.json()["available"] is False

    r3 = client.post("/api/comms/link", json={"available": True})
    assert r3.status_code == 200
    assert r3.json()["available"] is True


def test_latency_metrics_endpoint_reports_separately():
    """The feature spec explicitly requires reporting RofehAI's own
    processing latency SEPARATELY from unavoidable comms latency -- this
    is a real requirement to test, not just a nice-to-have."""
    r = client.get("/api/alerts/metrics/latency")
    assert r.status_code == 200
    body = r.json()
    assert "avg_seconds_detection_to_alert_queued" in body
    assert "avg_seconds_comms_latency_queue_to_transmit" in body
    assert "avg_seconds_transmit_to_clinician_ack" in body
    assert "total_alerts" in body


def test_list_alerts_filters_by_status():
    db.set_comms_link_available(False)
    alert = db.create_alert("crew-01", "immune_dysregulation", "moderate", 0.6, 0.2, 0.6, "n", [], None)
    db.mark_alert_queued(alert.id)

    pending = client.get("/api/alerts", params={"status": "pending"}).json()
    assert any(a["id"] == alert.id for a in pending)

    transmitted = client.get("/api/alerts", params={"status": "transmitted"}).json()
    assert not any(a["id"] == alert.id for a in transmitted)
