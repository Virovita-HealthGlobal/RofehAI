import json, logging, os
import httpx
from app.core import db
logger = logging.getLogger(__name__)
NIGHTSHIFT_BASE_URL = os.environ.get("NIGHTSHIFT_BASE_URL", "https://update-banish-decent.ngrok-free.dev/v1")
NIGHTSHIFT_API_KEY = os.environ.get("NIGHTSHIFT_API_KEY", "nightshift")
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "")
AGENT_MODEL = os.environ.get("AGENT_MODEL", "nvidia/llama-3.1-nemotron-70b-instruct")
MAX_AGENT_TURNS = 8
TOOLS = [{"type":"function","function":{"name":"list_domains","description":"Return clinical domains for a crew member.","parameters":{"type":"object","properties":{"crew_id":{"type":"string"}},"required":["crew_id"]}}},{"type":"function","function":{"name":"list_crew","description":"List crew members.","parameters":{"type":"object","properties":{},"required":[]}}},{"type":"function","function":{"name":"get_risk","description":"Get risk scores for a crew member in a domain.","parameters":{"type":"object","properties":{"crew_id":{"type":"string"},"domain":{"type":"string"}},"required":["crew_id","domain"]}}},{"type":"function","function":{"name":"get_countermeasure","description":"Get countermeasures for a domain and risk level.","parameters":{"type":"object","properties":{"domain":{"type":"string"},"risk_level":{"type":"string","enum":["low","moderate","high","critical"]}},"required":["domain","risk_level"]}}},{"type":"function","function":{"name":"compose_answer","description":"Assemble final answer from fetched data.","parameters":{"type":"object","properties":{"opening_tone":{"type":"string","enum":["nominal","watchful","alert","insufficient_data"]},"sections":{"type":"array","items":{"type":"object","properties":{"domain":{"type":"string"},"summary":{"type":"string"},"countermeasure":{"type":"string"}},"required":["domain","summary"]}}},"required":["opening_tone","sections"]}}}]
def _execute_tool(name, args, crew_id, fetched):
    try:
        if name == "list_crew":
            return json.dumps({"crew": db.list_crew()})
        if name == "list_domains":
            cid = args.get("crew_id", crew_id)
            return json.dumps({"crew_id": cid, "domains": db.list_domains(cid)})
        if name == "get_risk":
            cid = args.get("crew_id", crew_id)
            domain = args["domain"]
            data = db.get_risk(cid, domain)
            fetched[f"risk_{cid}_{domain}"] = data
            return json.dumps(data)
        if name == "get_countermeasure":
            domain = args["domain"]
            rl = args.get("risk_level", "low")
            cm = db.get_countermeasure(domain, rl)
            fetched[f"cm_{domain}_{rl}"] = cm
            return json.dumps(cm)
        if name == "compose_answer":
            fetched["__compose_args__"] = args
            return json.dumps({"status": "answer_composed"})
        return json.dumps({"error": f"Unknown tool: {name}"})
    except Exception as e:
        logger.warning("Tool %s failed: %s", name, e)
        return json.dumps({"error": str(e)})
def _compose_final_answer(crew_id, compose_args, fetched, conversation_id, lang="en"):
    tone = compose_args.get("opening_tone", "nominal")
    sections = compose_args.get("sections", [])
    phrases = {"nominal":"All monitored systems are within acceptable parameters.","watchful":"Some parameters warrant closer monitoring.","alert":"One or more parameters require immediate attention.","insufficient_data":"Insufficient data is available to make a full assessment."}
    lines = [phrases.get(tone, "Assessment complete."), ""]
    sources = []
    for sec in sections:
        domain = sec.get("domain", "")
        lines.append(f"**{domain.title()}**: {sec.get('summary','')}")
        if sec.get("countermeasure"):
            lines.append(f"  *Countermeasure*: {sec['countermeasure']}")
        lines.append("")
        rk = f"risk_{crew_id}_{domain}"
        if rk in fetched:
            sources.append({"type":"risk_data","domain":domain,"data":fetched[rk]})
    return "\n".join(lines).strip(), sources
def _chat_completion(messages, use_tools=True):
    payload = {"model": AGENT_MODEL, "messages": messages, "max_tokens": 1024, "temperature": 0.2}
    if use_tools:
        payload["tools"] = TOOLS
        payload["tool_choice"] = "auto"
    try:
        resp = httpx.post(f"{NIGHTSHIFT_BASE_URL}/chat/completions", json=payload, headers={"Authorization": f"Bearer {NIGHTSHIFT_API_KEY}"}, timeout=15.0)
        resp.raise_for_status()
        r = resp.json(); r["__provider__"] = "nightshift"; logger.info("Provider: Meterless Nightshift"); return r
    except Exception as e:
        logger.warning("Nightshift unavailable (%s); falling back to NVIDIA cloud.", e)
    if not NVIDIA_API_KEY:
        raise RuntimeError("Nightshift unreachable and NVIDIA_API_KEY not set.")
    resp = httpx.post(f"{NVIDIA_BASE_URL}/chat/completions", json=payload, headers={"Authorization": f"Bearer {NVIDIA_API_KEY}"}, timeout=30.0)
    resp.raise_for_status()
    r = resp.json(); r["__provider__"] = "nvidia_cloud"; logger.info("Provider: NVIDIA cloud (fallback)"); return r
def run_agent(question, crew_id, actor_name="crew", conversation_id=None, lang="en"):
    storage_crew_id = crew_id
    conversation_id = db.ensure_conversation(crew_id, conversation_id)
    history = db.get_conversation_history(conversation_id)
    used_recall = len(history) > 0
    system_prompt = ("You are RofehAI, a space medicine AI monitoring astronaut health.\nRULES:\n1. Use tools to fetch data before answering.\n2. Never fabricate clinical values.\n3. Call compose_answer once data is gathered.\n4. Be concise and supportive.\n" + f"Crew member: {crew_id}\nLanguage: {lang}")
    messages = [{"role":"system","content":system_prompt}] + list(history) + [{"role":"user","content":question}]
    db.save_agent_message(conversation_id, "user", question)
    fetched = {}; tool_calls_made = 0; answer = ""; sources = []
    for turn in range(MAX_AGENT_TURNS):
        response = _chat_completion(messages)
        choice = response["choices"][0]; msg = choice["message"]
        messages.append(msg)
        finish_reason = choice.get("finish_reason", "")
        if finish_reason == "tool_calls" or msg.get("tool_calls"):
            tool_results = []
            for tc in msg.get("tool_calls", []):
                fn_name = tc["function"]["name"]
                fn_args = json.loads(tc["function"]["arguments"] or "{}")
                tool_calls_made += 1
                result_str = _execute_tool(fn_name, fn_args, crew_id, fetched)
                tool_results.append({"role":"tool","tool_call_id":tc["id"],"content":result_str})
                if fn_name == "compose_answer" and "__compose_args__" in fetched:
                    answer, sources = _compose_final_answer(crew_id, fetched["__compose_args__"], fetched, conversation_id, lang=lang)
                    db.save_agent_message(conversation_id, "assistant", answer)
                    db.log_agent_call(storage_crew_id, conversation_id, len(question), tool_calls_made, used_recall, question=question, answer=answer, actor_name=actor_name)
                    return answer, sources, conversation_id
            messages.append({"role":"user","content":tool_results})
        elif finish_reason == "stop":
            answer = msg.get("content", ""); break
        else:
            break
    if not answer:
        answer, sources = _compose_final_answer(crew_id, {"opening_tone":"insufficient_data","sections":[]}, fetched, conversation_id, lang=lang)
    db.save_agent_message(conversation_id, "assistant", answer)
    db.log_agent_call(storage_crew_id, conversation_id, len(question), tool_calls_made, used_recall, question=question, answer=answer, actor_name=actor_name)
    return answer, sources, conversation_id
