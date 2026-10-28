"""Exercise the new agent.py against stub app modules and a fake OpenAI-style server."""
import importlib.util
import json
import sys
import threading
import types
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).parent
HM = Path(__file__).parent.parent / "app" / "core" / "hmem_client.py"

# ---------------------------------------------------------------- stub app.*
def mod(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    sys.modules[name] = m
    return m


class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def model_dump(self):
        return dict(self.__dict__)


CREW = [Obj(id="crew-01", name="A. Mendez"), Obj(id="crew-02", name="J. Okafor")]
DOMAINS = {
    "sans": Obj(id="sans", display_name="SANS (neuro-ocular) risk trajectory", status="active"),
    "immune_dysregulation": Obj(id="immune_dysregulation", display_name="Immune dysregulation risk trajectory", status="active"),
}


RISK = {"v": 0.99}


def score(twin, domain_id, day, lang="en"):
    return Obj(current_risk=RISK["v"], risk_delta_30d=1.0, confidence=0.84, narrative="Ocular trends suggest elevated risk.", is_fallback=False)


def recommend(risk, lang="en"):
    return Obj(countermeasure=Obj(name="LBNP sessions"), rationale="Current risk is high and rising.")


DB = {"msgs": {}, "convs": {}, "calls": []}


class Msg:
    def __init__(self, role, content):
        self.role, self.content = role, content


fake_db = mod(
    "app.core.db",
    conversation_belongs_to_crew=lambda cid, crew: DB["convs"].get(cid) == crew,
    create_conversation=lambda cid, crew: DB["convs"].__setitem__(cid, crew),
    load_agent_messages=lambda cid: [Msg(r, c) for r, c in DB["msgs"].get(cid, [])],
    save_agent_message=lambda cid, role, content: DB["msgs"].setdefault(cid, []).append((role, content)),
    log_agent_call=lambda *a, **k: DB["calls"].append((a, k)),
    count_agent_calls_since=lambda since: 0,
    get_crew_name_overrides=lambda: {},
)
mod("app")
core = mod("app.core", db=fake_db)
mod("app.core.countermeasures", recommend=recommend)
mod(
    "app.core.i18n",
    normalize_lang=lambda l: l or "en",
    translate=lambda table, lang, default: default,
    OPENING_TONES_I18N={}, GLUE_I18N={"current_risk": {}, "confidence": {}, "day_suffix": {}, "suggested": {}}, DISCLAIMER_I18N={},
)
mod("app.core.digital_twin", registry=Obj(get_or_create=lambda cid: object()))
mod("app.core.domains", DOMAIN_REGISTRY=DOMAINS, active_domains=lambda: list(DOMAINS.values()), all_domains=lambda: list(DOMAINS.values()))
mod("app.core.seeding", ensure_seeded=lambda cid, day: None)
mod("app.core.trajectory_model", score=score)
mod("app.data")
mod("app.data.synthetic", CREW=CREW, MISSION_LENGTH_DAYS=210)
mod("app.models")
mod("app.models.schemas", AgentSource=lambda **kw: Obj(**kw), CountermeasureRecommendation=object, RiskOutput=object)

hspec = importlib.util.spec_from_file_location("app.core.hmem_client", HM)
hm = importlib.util.module_from_spec(hspec)
sys.modules["app.core.hmem_client"] = hm
hspec.loader.exec_module(hm)
core.hmem_client = hm
hm.HMEM_URL = "http://127.0.0.1:1"  # dead on purpose for the legacy checks
spec = importlib.util.spec_from_file_location("agent_under_test", HERE.parent / "app" / "core" / "agent.py")
agent = importlib.util.module_from_spec(spec)
sys.modules["agent_under_test"] = agent  # dataclasses needs the module registered
spec.loader.exec_module(agent)

# ---------------------------------------------------------------- fake model server
SCRIPT = {"turns": [], "seen": [], "mode": "ok"}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path.startswith("/redir/"):
            self.send_response(307)
            self.send_header("Location", "/v1/chat/completions")
            self.end_headers()
            return
        if SCRIPT["mode"] == "down":
            self.send_response(503)
            self.end_headers()
            return
        SCRIPT["seen"].append(body)
        turn = SCRIPT["turns"].pop(0)
        out = json.dumps({"choices": [{"message": turn, "finish_reason": "tool_calls" if turn.get("tool_calls") else "stop"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


srv = HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_port}"


def tc(i, name, args, as_str=True):
    return {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args) if as_str else args}}


fails = []


def check(label, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + label + (f"  {extra}" if extra and not cond else ""))
    if not cond:
        fails.append(label)


# ---- 1. normal flow: fetch two domains (using the 'domain' alias), then compose
agent.NIGHTSHIFT_BASE_URL = base + "/v1"
SCRIPT["turns"] = [
    {"content": None, "tool_calls": [tc(1, "get_risk", {"crew_id": "crew-01", "domain": "sans"}), tc(2, "get_risk", {"crew_id": "crew-01", "domain_id": "immune_dysregulation"}), tc(4, "get_countermeasure", {"crew_id": "crew-01", "domain_id": "sans"})]},
    {"content": "ignored free text", "tool_calls": [tc(3, "compose_answer", {"opening_tone": "one_or_more_elevated", "sections": [{"domain": "sans", "include_countermeasure": True}, {"domain_id": "immune_dysregulation"}]})]},
]
answer, sources, conv = agent.run_agent("crew-01", "how is A. Mendez trending?", None, "tester", "en")
check("Nightshift request uses meterless/auto", SCRIPT["seen"][0]["model"] == "meterless/auto", str(SCRIPT["seen"][0].get("model")))
check("answer contains opening-tone sentence", "warrant review" in answer, answer)
check("answer has SANS risk 99%", "99%" in answer and "SANS" in answer, answer)
check("countermeasure rendered", "LBNP sessions" in answer, answer)
check("disclaimer present", "decision support" in answer or "Disclaimer" in answer or "This is decision support" in answer, answer)
check("model free text NOT in answer", "ignored free text" not in answer)
check("2 risk sources + 1 cm source", len(sources) == 3, str(len(sources)))
check("conversation saved + logged", DB["msgs"][conv][-1][0] == "assistant" and len(DB["calls"]) == 1)
second_req = SCRIPT["seen"][1]["messages"]
roles = [m["role"] for m in second_req]
check("second request has system, user, assistant(tool_calls), 3 tool msgs", roles == ["system", "user", "assistant", "tool", "tool", "tool"], str(roles))
check("tool result carries real risk data", '"current_risk": 0.99' in second_req[3]["content"], second_req[3]["content"])

# ---- 2. follow-up in same conversation reuses history; compose + data tool in the SAME turn
SCRIPT["turns"] = [
    {"content": "", "tool_calls": [tc(1, "get_risk", {"crew_id": "crew-01", "domain_id": "sans"}, as_str=False), tc(2, "compose_answer", {"opening_tone": "one_or_more_elevated", "sections": [{"domain_id": "sans"}]})]},
]
SCRIPT["seen"].clear()
answer2, _, conv2 = agent.run_agent("crew-01", "and now?", conv, "tester", "en")
check("same conversation id reused", conv2 == conv)
check("same-turn fetch+compose works", "99%" in answer2, answer2)
check("history included in request", any(m["role"] == "assistant" and "99%" in m["content"] for m in SCRIPT["seen"][0]["messages"] if m["role"] == "assistant"))

# ---- 3. model replies with plain text and no tools -> safe fallback, no free text leaks
SCRIPT["turns"] = [{"content": "You have a diagnosis of X!", "tool_calls": None}]
a3, _, _ = agent.run_agent("crew-02", "hello", None, "tester", "en")
check("plain text never returned", "diagnosis of X" not in a3 and "isn't enough recent data" in a3, a3)

# ---- 4. bad tool args do not crash (missing domain_id)
SCRIPT["turns"] = [
    {"content": "", "tool_calls": [tc(1, "get_risk", {"crew_id": "crew-02"})]},
    {"content": "", "tool_calls": [tc(2, "compose_answer", {"opening_tone": "insufficient_data", "sections": []})]},
]
a4, _, _ = agent.run_agent("crew-02", "x", None, "tester", "en")
check("missing arg handled", "isn't enough recent data" in a4, a4)

# ---- 5. wrong conversation -> AgentConversationError; routes also needs the 3 exception classes
try:
    agent.run_agent("crew-02", "x", conv, "tester", "en")  # conv belongs to crew-01
    check("conversation mismatch raises", False)
except agent.AgentConversationError:
    check("conversation mismatch raises AgentConversationError", True)
check("error classes exist for routes.py", all(hasattr(agent, n) for n in ("AgentError", "AgentConversationError", "AgentRateLimitError")))

# ---- 6. Nightshift behind a 307 redirect is followed
agent.NIGHTSHIFT_BASE_URL = base + "/redir"
# /redir/chat/completions -> 307 -> /v1/chat/completions ; fake server serves /v1/... for any non-/redir path
SCRIPT["turns"] = [{"content": "", "tool_calls": [tc(1, "compose_answer", {"opening_tone": "insufficient_data", "sections": []})]}]
a6, _, _ = agent.run_agent("crew-02", "x", None, "tester", "en")
check("307 redirect followed", "isn't enough recent data" in a6, a6)

# ---- 7. Nightshift down + no NVIDIA key -> AgentError (503), clear message
agent.NIGHTSHIFT_BASE_URL = base + "/v1"
SCRIPT["mode"] = "down"
import os
os.environ.pop("NVIDIA_API_KEY", None)
try:
    agent.run_agent("crew-02", "x", None, "tester", "en")
    check("no provider raises", False)
except agent.AgentError as e:
    check("no provider -> AgentError with clear message", "NVIDIA_API_KEY" in str(e), str(e))

# ---- 8. Nightshift down + NVIDIA key set -> falls back to NVIDIA base URL
agent.NVIDIA_BASE_URL = base + "/v1"
os.environ["NVIDIA_API_KEY"] = "test-key"
calls = {"n": 0}
orig_post = agent._post_chat


def spy(base_url, api_key, payload, timeout):
    calls["n"] += 1
    if api_key == "nightshift":
        raise RuntimeError("simulated nightshift down")
    return orig_post(base_url, api_key, payload, timeout)


agent._post_chat = spy
SCRIPT["mode"] = "ok"
SCRIPT["turns"] = [{"content": "", "tool_calls": [tc(1, "compose_answer", {"opening_tone": "insufficient_data", "sections": []})]}]
a8, _, _ = agent.run_agent("crew-02", "x", None, "tester", "en")
check("falls back to NVIDIA with its key", calls["n"] == 2 and "isn't enough recent data" in a8, str(calls))
check("NVIDIA request uses the Nemotron model name", SCRIPT["seen"][-1]["model"] == "nvidia/llama-3.1-nemotron-70b-instruct", str(SCRIPT["seen"][-1].get("model")))


# ================= H-MEM (real Meterless reference engine over HTTP) =================
import urllib.request
hm.HMEM_URL = "http://127.0.0.1:8910"
agent.NIGHTSHIFT_BASE_URL = base + "/v1"
SCRIPT["mode"] = "ok"
agent._post_chat = orig_post
def one_turn():
    SCRIPT["turns"] = [{"content": "", "tool_calls": [tc(1, "get_risk", {"crew_id": "crew-01", "domain_id": "sans"}), tc(2, "compose_answer", {"opening_tone": "one_or_more_elevated", "sections": [{"domain_id": "sans"}]})]}]
RISK["v"] = 0.82
one_turn()
h1, _, c1 = agent.run_agent("crew-01", "how is A. Mendez trending?", None, "t", "en")
check("H1 first session: no memory block yet", "H-MEM" not in h1, h1)
health = json.loads(urllib.request.urlopen("http://127.0.0.1:8910/health").read())
check("H1 memory stored in H-MEM", health["records"] == 1 and health["ledger"].get("create") == 1, str(health))
RISK["v"] = 0.90
one_turn()
h2, _, c2 = agent.run_agent("crew-01", "any change for A. Mendez?", None, "t", "en")  # NEW conversation
check("H2 new session recalls earlier briefing", c2 != c1 and "Since the last briefing (H-MEM memory)" in h2 and "82%" in h2 and "90% now (+8 pts)" in h2, h2)
check("H2 disclaimer still last", h2.rstrip().endswith("A human reviewer must confirm any action._"), h2[-120:])
one_turn()
h3, _, _ = agent.run_agent("crew-02", "how is J. Okafor?", None, "t", "en")
check("H3 other crew member does NOT see crew-01 memory", "H-MEM" not in h3 and "82%" not in h3, h3)
hm.HMEM_URL = "http://127.0.0.1:1"
one_turn()
h4, _, _ = agent.run_agent("crew-01", "service down?", None, "t", "en")
check("H4 memory service down: answer still returned", "90%" in h4 and "H-MEM" not in h4, h4)

print()
print("ALL PASSED" if not fails else f"FAILED: {fails}")
sys.exit(1 if fails else 0)
