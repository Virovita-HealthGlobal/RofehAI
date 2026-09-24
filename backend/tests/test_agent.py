"""
Tests for the agent orchestration layer, with particular focus on the
safety property described in app.core.agent's module docstring: the
model can never inject free text into the final answer -- every
sentence must come from either the fixed OPENING_TONES constants or a
real, already-fetched RiskOutput/CountermeasureRecommendation.

These tests do NOT require a live ANTHROPIC_API_KEY -- they test
_compose_final_answer and _execute_tool directly, which is exactly the
deterministic, non-LLM part that has to be bulletproof regardless of
which model (or prompt) sits in front of it.
"""

from app.core import agent
from app.core.domains import DOMAIN_REGISTRY
from app.core.seeding import ensure_seeded
from app.core.digital_twin import registry
from app.core.trajectory_model import score
from app.data.synthetic import MISSION_LENGTH_DAYS


def _real_fetched_data(crew_id: str, domain_id: str) -> agent._FetchedData:
    """Build a _FetchedData the way run_agent would -- via a real get_risk
    tool call against real (synthetic) data -- so these tests exercise the
    actual data path, not a hand-crafted mock."""
    fetched = agent._FetchedData()
    result = agent._execute_tool("get_risk", {"crew_id": crew_id, "domain_id": domain_id}, crew_id, fetched)
    assert "error" not in result
    return fetched


def test_compose_answer_only_includes_real_fetched_domains():
    """The model claims it fetched 'sans', but never actually called
    get_risk for it -- compose_answer must silently drop that section,
    not fabricate content for it."""
    fetched = agent._FetchedData()  # nothing fetched at all
    tool_input = {"opening_tone": "all_nominal", "sections": [{"domain_id": "sans", "include_countermeasure": True}]}
    answer, sources = agent._compose_final_answer("crew-01", tool_input, fetched)
    assert "sans" not in answer.lower() or "SANS" not in answer  # no fabricated SANS section
    assert sources == []  # nothing to cite because nothing was actually fetched


def test_compose_answer_uses_real_narrative_verbatim():
    """The included text must be exactly the real RiskOutput.narrative,
    not something the model composed."""
    fetched = _real_fetched_data("crew-01", "sans")
    real_narrative = fetched.risks["sans"].narrative
    tool_input = {"opening_tone": "one_or_more_elevated", "sections": [{"domain_id": "sans"}]}
    answer, sources = agent._compose_final_answer("crew-01", tool_input, fetched)
    assert real_narrative in answer
    assert sources == [agent.AgentSource(domain_id="sans", kind="risk")]


def test_compose_answer_rejects_invalid_opening_tone():
    """If the model somehow passes a tone outside the fixed enum (e.g. by
    a malformed tool call), fail safe to 'insufficient_data' rather than
    error out or silently accept arbitrary text."""
    fetched = agent._FetchedData()
    tool_input = {"opening_tone": "<script>alert(1)</script>", "sections": []}
    answer, sources = agent._compose_final_answer("crew-01", tool_input, fetched)
    assert answer.startswith(agent.OPENING_TONES["insufficient_data"])


def test_compose_answer_cannot_include_countermeasure_not_fetched():
    """Risk was fetched, but the countermeasure tool was never called --
    compose_answer must not fabricate a countermeasure recommendation."""
    fetched = _real_fetched_data("crew-01", "sans")
    tool_input = {"opening_tone": "one_or_more_elevated", "sections": [{"domain_id": "sans", "include_countermeasure": True}]}
    answer, sources = agent._compose_final_answer("crew-01", tool_input, fetched)
    assert not any(s.kind == "countermeasure" for s in sources)


def test_compose_answer_always_includes_human_review_disclaimer():
    fetched = agent._FetchedData()
    tool_input = {"opening_tone": "insufficient_data", "sections": []}
    answer, _ = agent._compose_final_answer("crew-01", tool_input, fetched)
    assert "not a diagnosis" in answer.lower()
    assert "human reviewer" in answer.lower()


def test_execute_tool_get_risk_matches_direct_api_call():
    """The tool dispatcher must return exactly what the REST API would --
    same underlying score(), no divergent logic."""
    fetched = agent._FetchedData()
    tool_result = agent._execute_tool("get_risk", {"crew_id": "crew-01", "domain_id": "sans"}, "crew-01", fetched)
    ensure_seeded("crew-01", MISSION_LENGTH_DAYS)
    twin = registry.get_or_create("crew-01")
    direct_result = score(twin, "sans", MISSION_LENGTH_DAYS)
    assert tool_result["current_risk"] == direct_result.current_risk
    assert tool_result["narrative"] == direct_result.narrative


def test_execute_tool_rejects_unknown_domain():
    fetched = agent._FetchedData()
    result = agent._execute_tool("get_risk", {"crew_id": "crew-01", "domain_id": "not-a-domain"}, "crew-01", fetched)
    assert "error" in result


def test_execute_tool_unknown_tool_name_raises():
    fetched = agent._FetchedData()
    try:
        agent._execute_tool("delete_everything", {}, "crew-01", fetched)
        assert False, "expected AgentError"
    except agent.AgentError:
        pass


def test_agent_ask_endpoint_returns_503_without_api_key(client_fixture=None):
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    r = client.post("/api/agent/ask", json={"crew_id": "crew-01", "question": "how is crew-01 trending?"})
    # No ANTHROPIC_API_KEY configured in this test environment -- must fail
    # clearly (503), never silently return a fabricated answer.
    assert r.status_code == 503
    assert "ANTHROPIC_API_KEY" in r.json()["detail"]


def test_agent_ask_endpoint_rejects_unknown_crew():
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    r = client.post("/api/agent/ask", json={"crew_id": "not-a-crew-member", "question": "status?"})
    assert r.status_code == 404


# --- Persistent memory tests (use case 2) --------------------------------

def test_build_initial_messages_no_conversation_id_is_just_the_new_question():
    messages = agent._build_initial_messages("crew-01", "how is this person doing?", conversation_id=None)
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert "how is this person doing?" in messages[0]["content"]


def test_conversation_memory_persists_and_is_loaded_in_order():
    """Real DB round-trip: create a conversation, save two turns, confirm
    _build_initial_messages reconstructs them in the correct order for
    the next call -- this is the actual mechanism that gives the agent
    continuity across separate /api/agent/ask calls."""
    from app.core import db
    import uuid

    conversation_id = uuid.uuid4().hex
    db.create_conversation(conversation_id, "crew-03")
    db.save_agent_message(conversation_id, "user", "how is crew-03 trending on SANS?")
    db.save_agent_message(conversation_id, "assistant", "Risk is currently moderate and rising.")

    messages = agent._build_initial_messages("crew-03", "did the countermeasure help?", conversation_id)

    assert len(messages) == 3  # 2 prior turns + the new question
    assert messages[0] == {"role": "user", "content": "how is crew-03 trending on SANS?"}
    assert messages[1] == {"role": "assistant", "content": "Risk is currently moderate and rising."}
    assert messages[2]["role"] == "user"
    assert "did the countermeasure help?" in messages[2]["content"]


def test_conversation_belongs_to_crew_rejects_cross_crew_access():
    from app.core import db
    import uuid

    conversation_id = uuid.uuid4().hex
    db.create_conversation(conversation_id, "crew-01")
    assert db.conversation_belongs_to_crew(conversation_id, "crew-01") is True
    assert db.conversation_belongs_to_crew(conversation_id, "crew-02") is False


def test_agent_ask_endpoint_rejects_conversation_id_for_wrong_crew(monkeypatch):
    """Even without a live API key, an invalid conversation_id must be
    caught (404) before we'd ever attempt a model call."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.core import db
    import uuid

    conversation_id = uuid.uuid4().hex
    db.create_conversation(conversation_id, "crew-01")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-dummy-not-real")
    try:
        client = TestClient(app)
        r = client.post(
            "/api/agent/ask",
            json={"crew_id": "crew-02", "question": "x", "conversation_id": conversation_id},
        )
        assert r.status_code == 404
    finally:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def test_recall_prior_answer_re_serves_the_previous_assistant_turn():
    """Regression test for a real gap found in live testing: a follow-up
    question like 'what did we just discuss?' isn't a request for fresh
    risk data, so compose_answer's normal section-based assembly has
    nothing to work with. recall_prior_answer must safely re-serve the
    conversation's own previous answer (already vetted through the same
    deterministic assembly) rather than falling back to a generic
    'insufficient data' non-answer."""
    from app.core import db
    import uuid

    conversation_id = uuid.uuid4().hex
    db.create_conversation(conversation_id, "crew-01")
    db.save_agent_message(conversation_id, "user", "how is crew-01 trending on SANS?")
    db.save_agent_message(conversation_id, "assistant", "SANS risk is 99% and rising. Suggested: LBNP sessions.")

    fetched = agent._FetchedData()
    tool_input = {"recall_prior_answer": True}
    answer, sources = agent._compose_final_answer("crew-01", tool_input, fetched, conversation_id)

    assert "SANS risk is 99% and rising" in answer
    assert sources == []  # nothing newly fetched this turn -- it's a pure recall


def test_recall_prior_answer_with_no_history_falls_back_safely():
    """If recall is requested but there's genuinely nothing to recall yet
    (e.g. a bug called it on a brand-new conversation), fail safe to the
    normal insufficient-data path rather than erroring or returning
    something empty/confusing."""
    fetched = agent._FetchedData()
    tool_input = {"recall_prior_answer": True}
    answer, sources = agent._compose_final_answer("crew-01", tool_input, fetched, conversation_id=None)
    assert answer.startswith(agent.OPENING_TONES["insufficient_data"])


# --- Rate limiting, history cap, and call logging tests -------------------

def test_build_initial_messages_caps_history_length():
    """A very long conversation must not grow the request (and therefore
    token cost) unboundedly -- only the most recent MAX_HISTORY_MESSAGES
    turns should be included."""
    from app.core import db
    import uuid

    conversation_id = uuid.uuid4().hex
    db.create_conversation(conversation_id, "crew-01")
    # Save far more turns than the cap.
    for i in range(agent.MAX_HISTORY_MESSAGES + 10):
        db.save_agent_message(conversation_id, "user" if i % 2 == 0 else "assistant", f"turn {i}")

    messages = agent._build_initial_messages("crew-01", "new question", conversation_id)

    # +1 for the new question itself.
    assert len(messages) == agent.MAX_HISTORY_MESSAGES + 1
    # The included history must be the MOST RECENT turns, not the oldest.
    assert "turn " in messages[0]["content"]
    included_turn_numbers = [int(m["content"].split()[-1]) for m in messages[:-1]]
    assert min(included_turn_numbers) >= 10  # oldest 10 turns were correctly dropped


def test_agent_call_logging_records_real_metadata():
    """log_agent_call must actually persist retrievable, accurate rows --
    this is what real observability depends on, not just 'we call the
    function somewhere'."""
    from app.core import db
    import uuid

    conversation_id = uuid.uuid4().hex
    db.create_conversation(conversation_id, "crew-03")
    db.log_agent_call("crew-03", conversation_id, question_length=42, tool_calls_made=3, used_recall=False)

    recent = db.recent_agent_calls(limit=5)
    matching = [c for c in recent if c.conversation_id == conversation_id]
    assert len(matching) == 1
    assert matching[0].crew_id == "crew-03"
    assert matching[0].question_length == 42
    assert matching[0].tool_calls_made == 3
    assert matching[0].used_recall is False


def test_agent_usage_endpoint_returns_logged_calls():
    from fastapi.testclient import TestClient
    from app.main import app
    from app.core import db
    import uuid

    conversation_id = uuid.uuid4().hex
    db.create_conversation(conversation_id, "crew-04")
    db.log_agent_call("crew-04", conversation_id, question_length=10, tool_calls_made=1, used_recall=True)

    client = TestClient(app)
    r = client.get("/api/agent/usage", params={"limit": 5})
    assert r.status_code == 200
    body = r.json()
    assert any(c["conversation_id"] == conversation_id for c in body)


def test_rate_limit_blocks_once_daily_cap_reached(monkeypatch):
    """The core protection: once the global daily call count reaches the
    configured limit, the check must refuse -- verified by setting the
    limit to 0 (already 'reached') and confirming AgentRateLimitError
    fires. Tests _check_rate_limit() directly, no network call needed."""
    monkeypatch.setenv(agent.DAILY_CALL_LIMIT_ENV_VAR, "0")
    try:
        try:
            agent._check_rate_limit()
            assert False, "expected AgentRateLimitError"
        except agent.AgentRateLimitError:
            pass
    finally:
        monkeypatch.delenv(agent.DAILY_CALL_LIMIT_ENV_VAR, raising=False)


def test_rate_limit_falls_back_to_default_when_env_var_is_empty_string(monkeypatch):
    """Regression test for the same empty-string env var class of bug
    fixed earlier in db.py/routes.py (docker-compose's ${VAR:-} sets an
    empty string rather than leaving the var unset). int('') would crash
    -- must fall back to DEFAULT_DAILY_CALL_LIMIT instead. Tests
    _check_rate_limit() directly, no network call involved."""
    monkeypatch.setenv(agent.DAILY_CALL_LIMIT_ENV_VAR, "")
    try:
        agent._check_rate_limit()  # must not raise ValueError from int('')
    finally:
        monkeypatch.delenv(agent.DAILY_CALL_LIMIT_ENV_VAR, raising=False)


def test_rate_limit_endpoint_returns_429(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-dummy-not-real")
    monkeypatch.setenv(agent.DAILY_CALL_LIMIT_ENV_VAR, "0")
    try:
        client = TestClient(app)
        r = client.post("/api/agent/ask", json={"crew_id": "crew-01", "question": "status?"})
        assert r.status_code == 429
    finally:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv(agent.DAILY_CALL_LIMIT_ENV_VAR, raising=False)
