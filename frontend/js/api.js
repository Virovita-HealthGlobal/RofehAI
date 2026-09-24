window.RofehAPI = (() => {
  const BASE = window.ROFEHAI_CONFIG.API_BASE;

  async function get(path) {
    const res = await fetch(`${BASE}${path}`);
    if (!res.ok) throw new Error(`GET ${path} failed: ${res.status}`);
    return res.json();
  }

  async function post(path, body) {
    const res = await fetch(`${BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`POST ${path} failed: ${res.status}`);
    return res.json();
  }

  return {
    listCrew: () => get("/crew"),
    missionMeta: () => get("/mission/meta"),
    domains: () => get("/domains"),
    observations: (crewId, missionDay) => get(`/crew/${crewId}/observations?mission_day=${missionDay}`),
    risk: (crewId, condition, missionDay) => get(`/crew/${crewId}/risk/${condition}?mission_day=${missionDay}`),
    countermeasure: (crewId, condition, missionDay) =>
      get(`/crew/${crewId}/countermeasure/${condition}?mission_day=${missionDay}`),
    logCountermeasure: (entry) => post("/countermeasures/log", entry),
    askAgent: (crewId, question, conversationId) =>
      post("/agent/ask", { crew_id: crewId, question, conversation_id: conversationId || null }),
  };
})();
