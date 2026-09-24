"""
Agent orchestration layer.

WHAT THIS IS
------------
An LLM-driven agent that reasons across a crew member's active health
domains and produces a single, prioritized briefing -- instead of a human
clicking through separate risk cards one at a time (use case 1 from the
hackathon scoping: cross-domain risk briefing). The model can freely plan
and call read-only data-fetching tools (list_domains, get_risk,
get_countermeasure, list_crew).

THE SAFETY DESIGN -- READ THIS BEFORE CHANGING ANYTHING HERE
--------------------------------------------------------------
This is the one place in the codebase where free-generated text could, in
principle, drift into diagnostic-sounding or otherwise non-compliant
language -- exactly the failure mode the rest of RofehAI has been built
to avoid (see the "surface / flag / prioritize, never diagnose / confirm
/ conclude" register enforced via narrative templates in
app.core.domains). To close that gap at the code level, not just via a
prompt instruction:

  The model NEVER writes the final answer's clinical content itself.

Instead, the model's last step must be a call to the `compose_answer`
tool, whose arguments are *structural* (which domains to include, in
what order, and one enum value for the opening tone) -- not free text.
`_compose_final_answer()` below then assembles the actual sentences
*deterministically*, by copying verbatim from the real `RiskOutput` and
`CountermeasureRecommendation` objects already fetched earlier in the
same conversation turn (see `_FetchedData`). There is no code path by
which a string the model typed can end up in the returned answer. If you
are tempted to let the model pass free text into the final answer to
make it sound more natural, don't -- that reintroduces exactly the risk
this design exists to close. Extend the fixed opening-tone sentences or
add more structured fields instead.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from app.core import countermeasures as cm
from app.core.digital_twin import registry
from app.core.domains import DOMAIN_REGISTRY, active_domains, all_domains
from app.core.seeding import ensure_seeded
from app.core.trajectory_model import score
from app.data.synthetic import CREW, MISSION_LENGTH_DAYS
from app.models.schemas import AgentSource, CountermeasureRecommendation, RiskOutput

ANTHROPIC_API_KEY_ENV_VAR = "ANTHROPIC_API_KEY"
AGENT_MODEL = "claude-sonnet-4-6"
MAX_TOOL_TURNS = 6

# Fixed, pre-written opening sentences -- the ONLY "tone" content the
# model can select, by enum value, never by typing new text.
OPENING_TONES: dict[str, str] = {
    "all_nominal": "All active health-domain trends for this crew member are currently within their expected range.",
    "one_or_more_elevated": "One or more active health-domain trends for this crew member warrant review.",
    "insufficient_data": "There isn't enough recent data yet to produce a confident briefing for this crew member.",
}

TOOLS = [
    {
        "name": "list_domains",
        "description": "List every registered health domain and whether it is active (real scoring) or architecture-ready (placeholder, no signals yet).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_crew",
        "description": "List all crew members on the mission.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_risk",
        "description": "Fetch the current risk trajectory for one crew member in one active health domain (SANS, immune dysregulation, etc). Call this once per active domain you want to check for a crew member.",
        "input_schema": {
            "type": "object",
            "properties": {
                "crew_id": {"type": "string"},
                "domain_id": {"type": "string", "description": "e.g. 'sans', 'immune_dysregulation'"},
            },
            "required": ["crew_id", "domain_id"],
        },
    },
    {
        "name": "get_countermeasure",
        "description": "Fetch the suggested countermeasure (if any) for a crew member in a given domain, based on their current risk trajectory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "crew_id": {"type": "string"},
                "domain_id": {"type": "string"},
            },
            "required": ["crew_id", "domain_id"],
        },
    },
    {
        "name": "compose_answer",
        "description": (
            "Finalize your answer. You do NOT write any clinical text yourself here -- "
            "you only choose which previously-fetched domain sections to include, in what "
            "order, and one opening tone. The system assembles the actual wording from the "
            "real data you already fetched with get_risk/get_countermeasure. Only reference "
            "domain_ids you already called get_risk on in this conversation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "opening_tone": {
                    "type": "string",
                    "enum": list(OPENING_TONES.keys()),
                    "description": "Pick based on what you found: all nominal, one or more elevated, or insufficient data.",
                },
                "sections": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "domain_id": {"type": "string"},
                            "include_countermeasure": {"type": "boolean"},
                        },
                        "required": ["domain_id"],
                    },
                    "description": "Domains to include in the briefing, in the order they should appear.",
                },
            },
            "required": ["opening_tone", "sections"],
        },
    },
]


@dataclass
class _FetchedData:
    """Everything real that's been fetched so far in this conversation
    turn. compose_answer can only reference what's already in here --
    it cannot conjure a domain result that was never actually fetched."""

    risks: dict[str, RiskOutput] = field(default_factory=dict)  # domain_id -> RiskOutput
    countermeasures: dict[str, CountermeasureRecommendation | None] = field(default_factory=dict)


class AgentError(Exception):
    pass


def _execute_tool(name: str, tool_input: dict, crew_id: str, fetched: _FetchedData) -> dict:
    if name == "list_domains":
        return {"domains": [{"id": d.id, "display_name": d.display_name, "status": d.status} for d in all_domains()]}

    if name == "list_crew":
        return {"crew": [c.model_dump() for c in CREW]}

    if name == "get_risk":
        domain_id = tool_input["domain_id"]
        if domain_id not in DOMAIN_REGISTRY:
            return {"error": f"unknown domain_id '{domain_id}'"}
        ensure_seeded(crew_id, MISSION_LENGTH_DAYS)
        twin = registry.get_or_create(crew_id)
        result = score(twin, domain_id, MISSION_LENGTH_DAYS)
        fetched.risks[domain_id] = result
        return result.model_dump()

    if name == "get_countermeasure":
        domain_id = tool_input["domain_id"]
        if domain_id not in DOMAIN_REGISTRY:
            return {"error": f"unknown domain_id '{domain_id}'"}
        ensure_seeded(crew_id, MISSION_LENGTH_DAYS)
        twin = registry.get_or_create(crew_id)
        risk = fetched.risks.get(domain_id) or score(twin, domain_id, MISSION_LENGTH_DAYS)
        fetched.risks[domain_id] = risk
        rec = cm.recommend(risk)
        fetched.countermeasures[domain_id] = rec
        return rec.model_dump() if rec else {"countermeasure": None}

    raise AgentError(f"unknown tool '{name}'")


def _compose_final_answer(crew_id: str, tool_input: dict, fetched: _FetchedData) -> tuple[str, list[AgentSource]]:
    """The ONE place the final answer text is assembled. Every sentence
    here is either a fixed constant (OPENING_TONES) or copied verbatim
    from a real RiskOutput/CountermeasureRecommendation already fetched
    -- never text the model typed. See module docstring."""
    tone = tool_input.get("opening_tone")
    if tone not in OPENING_TONES:
        tone = "insufficient_data"
    lines = [OPENING_TONES[tone]]
    sources: list[AgentSource] = []

    for section in tool_input.get("sections", []):
        domain_id = section.get("domain_id")
        risk = fetched.risks.get(domain_id)
        if risk is None:
            continue  # ignore any domain the model didn't actually fetch -- can't be included
        domain = DOMAIN_REGISTRY.get(domain_id)
        display_name = domain.display_name if domain else domain_id

        if risk.is_fallback:
            lines.append(f"\n**{display_name}:** {risk.narrative}")
        else:
            lines.append(
                f"\n**{display_name}:** {round(risk.current_risk * 100)}% current risk "
                f"({'+' if risk.risk_delta_30d >= 0 else ''}{round(risk.risk_delta_30d * 100)}% / 30d), "
                f"confidence {risk.confidence:.2f}. {risk.narrative}"
            )
        sources.append(AgentSource(domain_id=domain_id, kind="risk"))

        if section.get("include_countermeasure"):
            rec = fetched.countermeasures.get(domain_id)
            if rec:
                lines.append(f"  Suggested: {rec.countermeasure.name} -- {rec.rationale}")
                sources.append(AgentSource(domain_id=domain_id, kind="countermeasure"))

    lines.append(
        "\n\n_This is decision support, not a diagnosis. A human reviewer must confirm any action._"
    )
    return "\n".join(lines), sources


def run_agent(crew_id: str, question: str) -> tuple[str, list[AgentSource]]:
    api_key = os.environ.get(ANTHROPIC_API_KEY_ENV_VAR)
    if not api_key:
        raise AgentError(
            "ANTHROPIC_API_KEY is not configured on this server. Set it as an environment "
            "variable to enable the agent endpoint."
        )

    import anthropic  # local import: keeps the SDK optional for deployments not using the agent

    client = anthropic.Anthropic(api_key=api_key)
    fetched = _FetchedData()

    system_prompt = (
        "You are RofehAI's crew-health briefing assistant. You help a flight surgeon or crew "
        "medical officer understand a crew member's health trajectory across active domains. "
        "Use the tools to gather real data (call get_risk for each active domain relevant to "
        "the question -- check list_domains first if unsure which are active). When you have "
        "enough information, call compose_answer to finalize -- you select which domains to "
        "include and an opening tone, but you never write the clinical wording yourself; the "
        "system assembles it from the real data you fetched. Never state or imply a diagnosis."
    )

    messages = [{"role": "user", "content": f"Crew member: {crew_id}\nQuestion: {question}"}]

    for _ in range(MAX_TOOL_TURNS):
        response = client.messages.create(
            model=AGENT_MODEL,
            max_tokens=1024,
            system=system_prompt,
            tools=TOOLS,
            messages=messages,
        )

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses:
            # Model didn't call a tool at all -- treat as insufficient data rather
            # than ever falling back to raw model text as the answer.
            return _compose_final_answer(crew_id, {"opening_tone": "insufficient_data", "sections": []}, fetched)

        messages.append({"role": "assistant", "content": response.content})

        compose_call = next((t for t in tool_uses if t.name == "compose_answer"), None)
        if compose_call:
            return _compose_final_answer(crew_id, compose_call.input, fetched)

        tool_results = []
        for t in tool_uses:
            try:
                result = _execute_tool(t.name, t.input, crew_id, fetched)
            except AgentError as e:
                result = {"error": str(e)}
            tool_results.append({"type": "tool_result", "tool_use_id": t.id, "content": str(result)})
        messages.append({"role": "user", "content": tool_results})

    # Ran out of turns without the model finalizing -- fail safe, not open-ended.
    return _compose_final_answer(crew_id, {"opening_tone": "insufficient_data", "sections": []}, fetched)
