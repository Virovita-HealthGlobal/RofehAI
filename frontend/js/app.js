(() => {
  let missionLengthDays = 210;
  let viewDay = null; // null = "current" (full mission length); set by the mission-day slider
  let activeCrewId = null;
  let chart = null;
  let activeDomains = [];      // [{id, display_name, status}] where status === "active"
  let placeholderDomains = []; // status === "architecture_ready"
  const conversationIdByCrew = {}; // crew_id -> conversation_id, so each crew member's thread persists while browsing
  let fleetConversationId = null; // separate conversation thread for fleet-wide (crew-id-less) questions
  let commsLinkAvailable = true;
  let viewingAlerts = false;
  let speakAnswersEnabled = false;
  let voiceLang = "en-US";

  const rosterList = document.getElementById("roster-list");
  const detailPanel = document.getElementById("detail-panel");
  const missionNameEl = document.getElementById("mission-name");
  const missionDayEl = document.getElementById("mission-day-value");

  function fmtPct(x) {
    return `${Math.round(x * 100)}%`;
  }

  function statusForRisk(risk, isFallback) {
    if (isFallback) return "nominal";
    if (risk > 0.75) return "critical";
    if (risk > 0.5) return "elevated";
    return "nominal";
  }

  async function init() {
    document.getElementById("speak-answers-toggle")?.addEventListener("change", (e) => {
      speakAnswersEnabled = e.target.checked;
    });
    document.getElementById("voice-lang-select")?.addEventListener("change", (e) => {
      voiceLang = e.target.value;
    });

    const [meta, crew, domains] = await Promise.all([
      RofehAPI.missionMeta(),
      RofehAPI.listCrew(),
      RofehAPI.domains(),
    ]);
    missionLengthDays = meta.mission_length_days;
    missionNameEl.textContent = meta.name;
    missionDayEl.textContent = missionLengthDays;

    const daySlider = document.getElementById("mission-day-slider");
    daySlider.max = missionLengthDays;
    daySlider.value = missionLengthDays;
    daySlider.addEventListener("input", () => {
      // Live label update while dragging -- cheap, no fetch yet.
      missionDayEl.textContent = daySlider.value;
    });
    daySlider.addEventListener("change", () => {
      // Only re-fetch once the person releases the slider, not on every
      // intermediate tick -- avoids flooding the API while dragging.
      const day = parseInt(daySlider.value, 10);
      viewDay = day === missionLengthDays ? null : day; // null = back to "current" (full mission)
      if (activeCrewId && !viewingAlerts) {
        const crewName = document.querySelector(`.roster-item[data-crew-id="${activeCrewId}"] .roster-item-name`)?.textContent || "";
        selectCrew(activeCrewId, crewName);
      }
    });

    activeDomains = domains.filter((d) => d.status === "active");
    placeholderDomains = domains.filter((d) => d.status !== "active");

    rosterList.innerHTML = "";
    for (const member of crew) {
      const li = document.createElement("li");
      li.className = "roster-item";
      li.dataset.crewId = member.id;
      li.innerHTML = `
        <span class="status-dot status-dot--nominal" data-role="dot"></span>
        <span class="roster-item-name">${member.name}</span>
        <span class="roster-item-day">${member.id}</span>
        <button type="button" data-role="rename" title="Rename crew member (admin)" style="margin-left:auto; background:none; border:none; color:var(--text-3); cursor:pointer; font-size:12px; padding:2px 6px; opacity:0.55;">&#9998;</button>
      `;
      li.addEventListener("click", () => selectCrew(member.id, member.name));
      li.querySelector('[data-role="rename"]').addEventListener("click", (e) => {
        e.stopPropagation();
        renameCrewMember(member.id, li.querySelector(".roster-item-name").textContent);
      });
      rosterList.appendChild(li);
    }

    // Pre-fetch the flagship domain's risk to color the roster dots, then select the first crew member.
    const flagshipDomainId = activeDomains[0]?.id;
    if (flagshipDomainId) {
      for (const member of crew) {
        const risk = await RofehAPI.risk(member.id, flagshipDomainId, missionLengthDays);
        const dot = rosterList.querySelector(`[data-crew-id="${member.id}"] [data-role="dot"]`);
        const level = statusForRisk(risk.current_risk, risk.is_fallback);
        dot.className = `status-dot status-dot--${level}`;
      }
    }

    wireFleetChatForm();
    wireAlertsControls();
    wireSignOut();
    refreshCommsLinkUI();
    refreshAlertsBadge();
    if (crew.length) selectCrew(crew[0].id, crew[0].name);
  }


  async function renameCrewMember(crewId, currentName) {
    const newName = window.prompt(`Rename ${currentName} (${crewId}).\n\nLeave blank to reset to the original name.`, currentName);
    if (newName === null) return; // cancelled
    const adminKey = window.prompt("Enter admin key to confirm this change:");
    if (!adminKey) return;
    try {
      const trimmed = newName.trim();
      const res = trimmed === ""
        ? await fetch(`${window.ROFEHAI_CONFIG.API_BASE}/crew/${crewId}/name`, {
            method: "DELETE",
            headers: { "x-api-key": adminKey },
          })
        : await fetch(`${window.ROFEHAI_CONFIG.API_BASE}/crew/${crewId}/name`, {
            method: "PUT",
            headers: { "Content-Type": "application/json", "x-api-key": adminKey },
            body: JSON.stringify({ display_name: trimmed }),
          });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        alert(`Rename failed: ${body.detail || res.status}`);
        return;
      }
      const data = await res.json();
      const nameEl = document.querySelector(`.roster-item[data-crew-id="${crewId}"] .roster-item-name`);
      if (nameEl) nameEl.textContent = data.display_name;
      if (activeCrewId === crewId) selectCrew(crewId, data.display_name);
    } catch (err) {
      alert("Rename failed: network error");
    }
  }

  async function selectCrew(crewId, name) {
    activeCrewId = crewId;
    viewingAlerts = false;
    document.querySelectorAll(".roster-item").forEach((el) => {
      el.classList.toggle("is-active", el.dataset.crewId === crewId);
    });

    detailPanel.innerHTML = `<div class="empty-state">Loading ${name}&rsquo;s trajectory&hellip;</div>`;

    const dayToView = viewDay || missionLengthDays;
    const [obs, ...rest] = await Promise.all([
      RofehAPI.observations(crewId, dayToView),
      ...activeDomains.map((d) => RofehAPI.risk(crewId, d.id, dayToView)),
      ...activeDomains.map((d) => RofehAPI.countermeasure(crewId, d.id, dayToView)),
    ]);
    const risks = rest.slice(0, activeDomains.length);
    const countermeasures = rest.slice(activeDomains.length);

    render(name, obs, risks, countermeasures);
  }

  function riskPanel(title, risk) {
    const deltaClass = risk.risk_delta_30d > 0 ? "is-up" : "is-down";
    const deltaSign = risk.risk_delta_30d > 0 ? "+" : "";
    const level = statusForRisk(risk.current_risk, risk.is_fallback);

    return `
      <div class="panel">
        <div class="panel-title">
          <span class="status-dot status-dot--${level}"></span> ${title}
        </div>
        ${
          risk.is_fallback
            ? `<div class="cm-none">${risk.narrative}</div>`
            : `
          <div class="risk-row">
            <span class="risk-figure">${fmtPct(risk.current_risk)}</span>
            <span class="risk-delta ${deltaClass}">${deltaSign}${fmtPct(risk.risk_delta_30d)} / 30d</span>
          </div>
          <div class="risk-confidence">confidence ${risk.confidence.toFixed(2)}</div>
          <div class="contributors">
            ${risk.top_contributors.map((c) => `<span class="contributor-chip">${c}</span>`).join("")}
          </div>
          <div class="narrative">${risk.narrative}</div>
        `
        }
      </div>
    `;
  }

  function countermeasurePanel(countermeasures) {
    const items = countermeasures.filter(Boolean);
    if (!items.length) {
      return `
        <div class="panel countermeasure-panel">
          <div class="panel-title">Countermeasure suggestions</div>
          <div class="cm-none">No countermeasure suggested at this time — risk trends do not currently warrant one.</div>
        </div>
      `;
    }
    return `
      <div class="panel countermeasure-panel">
        <div class="panel-title">Countermeasure suggestions</div>
        ${items
          .map(
            (rec) => `
          <div class="cm-name">${rec.countermeasure.name}</div>
          <div class="cm-desc">${rec.countermeasure.description}</div>
          <div class="cm-rationale">${rec.rationale}</div>
          <button class="btn is-primary" data-log-cm="${rec.countermeasure.id}">Log as used</button>
        `
          )
          .join("<hr style='border-color:var(--border); margin:14px 0;'/>")}
      </div>
    `;
  }

  function placeholderDomainsPanel() {
    if (!placeholderDomains.length) return "";
    return `
      <div class="panel placeholder-domains-panel" style="grid-column: 1 / -1;">
        <div class="panel-title">Other domains — architecture ready</div>
        <div class="narrative" style="margin-top:0; padding-top:0; border-top:none;">
          Registered in the platform's domain architecture but not yet wired to real or synthetic signals —
          shown here so the platform's intended multi-system scope is visible without claiming they're operational.
        </div>
        <div class="contributors" style="margin-top:10px;">
          ${placeholderDomains.map((d) => `<span class="contributor-chip">${d.display_name}</span>`).join("")}
        </div>
      </div>
    `;
  }

  function chatPanel(crewName) {
    return `
      <div class="panel chat-panel" style="grid-column: 1 / -1;">
        <div class="panel-title">Ask the briefing agent</div>
        <div class="narrative" style="margin-top:0; padding-top:0; border-top:none;">
          Reasons across this crew member's active health domains using real data — never fabricates clinical text; every sentence is sourced from an actual risk or countermeasure lookup.
        </div>
        <div id="chat-log" style="margin-top:12px; display:flex; flex-direction:column; gap:10px; max-height:340px; overflow-y:auto;"></div>
        <form id="chat-form" style="display:flex; gap:8px; margin-top:12px;">
          <input id="chat-input" type="text" placeholder="e.g. how is ${crewName} trending across their active domains?"
            style="flex:1; background:var(--surface-2); border:1px solid var(--border); border-radius:var(--radius); color:var(--text-1); padding:9px 12px; font-family:var(--font-body, inherit); font-size:13px;" />
          <button class="btn is-primary" type="submit" id="chat-send">Ask</button>
        </form>
      </div>
    `;
  }

  function addVoiceButton(inputId) {
    const input = document.getElementById(inputId);
    if (!input || input.dataset.voiceWired) return;
    const SpeechRecognitionCtor = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognitionCtor) return;
    input.dataset.voiceWired = "1";

    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = "\u{1F3A4}";
    btn.title = "Speak your question — review the transcribed text before clicking Ask, in case it mishears you";
    btn.style.cssText =
      "background:var(--surface-2); border:1px solid var(--border); border-radius:var(--radius); color:var(--text-1); padding:9px 12px; cursor:pointer; font-size:14px;";
    input.insertAdjacentElement("afterend", btn);

    const recognition = new SpeechRecognitionCtor();
    recognition.lang = voiceLang;
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;

    let listening = false;
    recognition.addEventListener("result", (e) => {
      input.value = e.results[0][0].transcript;
    });
    recognition.addEventListener("end", () => {
      listening = false;
      btn.textContent = "\u{1F3A4}";
    });
    recognition.addEventListener("error", () => {
      listening = false;
      btn.textContent = "\u{1F3A4}";
    });

    btn.addEventListener("click", () => {
      if (listening) {
        recognition.stop();
        return;
      }
      listening = true;
      btn.textContent = "\u{1F534}";
      recognition.start();
    });
  }

  function speakAloud(text) {
    if (!speakAnswersEnabled) return;
    if (!("speechSynthesis" in window)) return;
    window.speechSynthesis.cancel();
    const utter = new SpeechSynthesisUtterance(text);
    utter.lang = voiceLang;
    window.speechSynthesis.speak(utter);
  }

  function appendChatMessage(role, text, logId = "chat-log") {
    const log = document.getElementById(logId);
    if (!log) return;
    const bubble = document.createElement("div");
    bubble.style.cssText =
      role === "user"
        ? "align-self:flex-end; max-width:80%; background:var(--accent); color:#0A1020; padding:10px 12px; border-radius:10px 10px 2px 10px; font-size:13px; white-space:pre-wrap;"
        : "align-self:flex-start; max-width:85%; background:var(--surface-2); border:1px solid var(--border); padding:10px 12px; border-radius:10px 10px 10px 2px; font-size:13px; white-space:pre-wrap; line-height:1.5;";
    bubble.textContent = text;
    log.appendChild(bubble);
    log.scrollTop = log.scrollHeight;
    if (role === "agent") speakAloud(text);
  }

  function wireFleetChatForm() {
    const form = document.getElementById("fleet-chat-form");
    if (!form) return;
    addVoiceButton("fleet-chat-input");
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const input = document.getElementById("fleet-chat-input");
      const sendBtn = document.getElementById("fleet-chat-send");
      const question = input.value.trim();
      if (!question) return;

      appendChatMessage("user", question, "fleet-chat-log");
      input.value = "";
      sendBtn.disabled = true;
      sendBtn.textContent = "Thinking…";

      try {
        const res = await RofehAPI.askAgent(null, question, fleetConversationId);
        fleetConversationId = res.conversation_id;
        appendChatMessage("agent", res.answer, "fleet-chat-log");
      } catch (err) {
        appendChatMessage("agent", "Sorry — the agent isn't available right now. It may not be configured on this server yet.", "fleet-chat-log");
      } finally {
        sendBtn.disabled = false;
        sendBtn.textContent = "Ask about crew";
      }
    });
  }

  function wireChatForm() {
    const form = document.getElementById("chat-form");
    if (!form) return;
    addVoiceButton("chat-input");
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const input = document.getElementById("chat-input");
      const sendBtn = document.getElementById("chat-send");
      const question = input.value.trim();
      if (!question) return;

      appendChatMessage("user", question);
      input.value = "";
      sendBtn.disabled = true;
      sendBtn.textContent = "Thinking…";

      try {
        const crewId = activeCrewId;
        const res = await RofehAPI.askAgent(crewId, question, conversationIdByCrew[crewId]);
        conversationIdByCrew[crewId] = res.conversation_id;
        appendChatMessage("agent", res.answer);
      } catch (err) {
        appendChatMessage("agent", "Sorry — the agent isn't available right now. It may not be configured on this server yet.");
      } finally {
        sendBtn.disabled = false;
        sendBtn.textContent = "Ask";
      }
    });
  }

  function render(name, obs, risks, countermeasures) {
    const riskPanels = activeDomains
      .map((d, i) => riskPanel(`${d.display_name} risk trajectory`, risks[i]))
      .join("");

    detailPanel.innerHTML = `
      <div class="detail-heading">
        <h1>${name}</h1>
        <span class="subtitle">${activeCrewId}</span>
      </div>
      <div class="grid">
        ${riskPanels}
        <div class="panel chart-panel">
          <div class="panel-title">Signal trends</div>
          <div class="chart-wrap"><canvas id="trend-chart"></canvas></div>
        </div>
        ${countermeasurePanel(countermeasures)}
        ${chatPanel(name)}
        ${placeholderDomainsPanel()}
      </div>
    `;

    document.querySelectorAll("[data-log-cm]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        await RofehAPI.logCountermeasure({
          crew_id: activeCrewId,
          countermeasure_id: btn.dataset.logCm,
          mission_day: missionLengthDays,
        });
        btn.textContent = "Logged ✓";
        btn.disabled = true;
      });
    });

    wireChatForm();
    drawChart(obs);
  }

  function drawChart(obs) {
    const ctx = document.getElementById("trend-chart");
    const days = obs.map((o) => o.mission_day);

    if (chart) chart.destroy();
    chart = new Chart(ctx, {
      type: "line",
      data: {
        labels: days,
        datasets: [
          {
            label: "Optic nerve sheath diameter (mm)",
            data: obs.map((o) => o.signals?.onsd_mm ?? null),
            borderColor: "#E5615C",
            backgroundColor: "transparent",
            yAxisID: "y",
            pointRadius: 0,
            tension: 0.25,
          },
          {
            label: "EBV shedding (copies/mL)",
            data: obs.map((o) => o.signals?.ebv_shedding_copies_ml ?? null),
            borderColor: "#4C8DFF",
            backgroundColor: "transparent",
            yAxisID: "y1",
            pointRadius: 0,
            tension: 0.25,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        scales: {
          x: {
            title: { display: true, text: "Mission day", color: "#8C9AC2" },
            ticks: { color: "#8C9AC2" },
            grid: { color: "#263257" },
          },
          y: {
            position: "left",
            ticks: { color: "#8C9AC2" },
            grid: { color: "#263257" },
            title: { display: true, text: "ONSD (mm)", color: "#8C9AC2" },
          },
          y1: {
            position: "right",
            ticks: { color: "#8C9AC2" },
            grid: { drawOnChartArea: false },
            title: { display: true, text: "EBV (copies/mL)", color: "#8C9AC2" },
          },
        },
        plugins: {
          legend: { labels: { color: "#E8ECF6" } },
        },
      },
    });
  }

  function renderChangePasswordView() {
    viewingAlerts = false;
    activeCrewId = null;
    document.querySelectorAll(".roster-item").forEach((el) => el.classList.remove("is-active"));

    detailPanel.innerHTML = `
      <div class="detail-heading"><h1>Change password</h1></div>
      <div class="panel" style="max-width:420px;">
        <form id="change-password-form">
          <label style="display:block; font-size:12px; color:var(--text-2); margin-bottom:4px;">Current password</label>
          <input class="gate-input" id="current-password" type="password" required style="width:100%; box-sizing:border-box; margin-bottom:14px;" />
          <label style="display:block; font-size:12px; color:var(--text-2); margin-bottom:4px;">New password (min 8 characters)</label>
          <input class="gate-input" id="new-password" type="password" required minlength="8" style="width:100%; box-sizing:border-box; margin-bottom:14px;" />
          <label style="display:block; font-size:12px; color:var(--text-2); margin-bottom:4px;">Confirm new password</label>
          <input class="gate-input" id="confirm-password" type="password" required style="width:100%; box-sizing:border-box; margin-bottom:14px;" />
          <div id="change-password-error" style="color:var(--status-critical); font-size:13px; margin-bottom:12px; display:none;"></div>
          <div id="change-password-success" style="color:var(--status-nominal); font-size:13px; margin-bottom:12px; display:none;">Password changed successfully.</div>
          <button class="btn is-primary" type="submit" id="change-password-submit">Update password</button>
        </form>
      </div>
    `;

    document.getElementById("change-password-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const errorEl = document.getElementById("change-password-error");
      const successEl = document.getElementById("change-password-success");
      const submitBtn = document.getElementById("change-password-submit");
      errorEl.style.display = "none";
      successEl.style.display = "none";

      const current = document.getElementById("current-password").value;
      const next = document.getElementById("new-password").value;
      const confirm = document.getElementById("confirm-password").value;

      if (next !== confirm) {
        errorEl.textContent = "New password and confirmation don't match.";
        errorEl.style.display = "block";
        return;
      }

      submitBtn.disabled = true;
      submitBtn.textContent = "Updating…";
      try {
        await RofehAPI.changePassword(current, next);
        successEl.style.display = "block";
        document.getElementById("change-password-form").reset();
      } catch (err) {
        errorEl.textContent = "Current password is incorrect, or the new password is too short.";
        errorEl.style.display = "block";
      } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = "Update password";
      }
    });
  }

  function wireSignOut() {
    const nameEl = document.getElementById("signed-in-as");
    const displayName = sessionStorage.getItem("rofehai_display_name");
    if (nameEl && displayName) nameEl.textContent = `Signed in as ${displayName}`;

    document.getElementById("signout-link").addEventListener("click", async (e) => {
      e.preventDefault();
      try {
        await RofehAPI.logout(); // best-effort: actually revokes the session server-side, not just client-side
      } catch (err) {
        // Even if the network call fails, still clear locally and redirect --
        // a failed revocation shouldn't trap the user on the dashboard.
      }
      sessionStorage.removeItem("rofehai_session_token");
      sessionStorage.removeItem("rofehai_display_name");
      window.location.href = "gate.html";
    });

    document.getElementById("change-password-link").addEventListener("click", (e) => {
      e.preventDefault();
      renderChangePasswordView();
    });
  }

  const SEVERITY_DOT = { high: "critical", moderate: "elevated", nominal: "nominal" };

  async function refreshCommsLinkUI() {
    const state = await RofehAPI.getCommsLink();
    commsLinkAvailable = state.available;
    const dot = document.getElementById("comms-link-dot");
    const label = document.getElementById("comms-link-label");
    if (dot) dot.className = `status-dot status-dot--${commsLinkAvailable ? "nominal" : "critical"}`;
    if (label) label.textContent = commsLinkAvailable ? "Comms link: available" : "Comms link: SIMULATED OUTAGE";
  }

  async function refreshAlertsBadge() {
    const pending = await RofehAPI.listAlerts("pending");
    const badge = document.getElementById("alerts-pending-badge");
    if (!badge) return;
    if (pending.length > 0) {
      badge.textContent = String(pending.length);
      badge.style.display = "inline-block";
    } else {
      badge.style.display = "none";
    }
  }

  function wireAlertsControls() {
    const toggleBtn = document.getElementById("toggle-comms-btn");
    const sweepBtn = document.getElementById("run-sweep-btn");
    const viewBtn = document.getElementById("view-alerts-btn");

    toggleBtn?.addEventListener("click", async () => {
      toggleBtn.disabled = true;
      try {
        await RofehAPI.setCommsLink(!commsLinkAvailable);
        await refreshCommsLinkUI();
        await refreshAlertsBadge();
        if (viewingAlerts) renderAlertsView();
      } finally {
        toggleBtn.disabled = false;
      }
    });

    sweepBtn?.addEventListener("click", async () => {
      sweepBtn.disabled = true;
      sweepBtn.textContent = "Sweeping…";
      try {
        await RofehAPI.runMonitoringCheck();
        await refreshAlertsBadge();
        if (viewingAlerts) renderAlertsView();
      } finally {
        sweepBtn.disabled = false;
        sweepBtn.textContent = "Run sweep";
      }
    });

    viewBtn?.addEventListener("click", () => {
      document.querySelectorAll(".roster-item").forEach((el) => el.classList.remove("is-active"));
      renderAlertsView();
    });

    const metricsBtn = document.getElementById("view-metrics-btn");
    metricsBtn?.addEventListener("click", () => {
      document.querySelectorAll(".roster-item").forEach((el) => el.classList.remove("is-active"));
      renderMetricsView();
    });
  }

  async function renderMetricsView() {
    viewingAlerts = false;
    activeCrewId = null;
    detailPanel.innerHTML = `<div class="empty-state">Loading metrics&hellip;</div>`;

    const [calls, latency] = await Promise.all([
      RofehAPI.getAgentUsage(100),
      RofehAPI.getLatencyMetrics(),
    ]);

    const todayStr = new Date().toISOString().slice(0, 10);
    const callsToday = calls.filter((c) => (c.created_at || "").slice(0, 10) === todayStr);

    function fmtSecs(s) {
      if (s === null || s === undefined) return "&mdash;";
      return `${s.toFixed(1)}s`;
    }

    function callRow(c) {
      return `
        <tr>
          <td style="padding:6px 10px; font-size:12px;">${c.crew_id || "fleet-wide"}</td>
          <td style="padding:6px 10px; font-size:12px;">${c.question_length}</td>
          <td style="padding:6px 10px; font-size:12px;">${c.tool_calls_made}</td>
          <td style="padding:6px 10px; font-size:12px;">${c.used_recall ? "yes" : "no"}</td>
          <td style="padding:6px 10px; font-size:12px; color:var(--text-3);">${new Date(c.created_at).toLocaleString()}</td>
        </tr>`;
    }

    detailPanel.innerHTML = `
      <div class="detail-heading"><h1>Agent &amp; System Metrics</h1></div>

      <div style="display:flex; gap:16px; flex-wrap:wrap; margin-bottom:20px;">
        <div class="risk-card" style="flex:1; min-width:180px;">
          <div style="font-size:11px; color:var(--text-3); text-transform:uppercase;">Agent calls today</div>
          <div style="font-size:28px; font-weight:700;">${callsToday.length}</div>
          <div style="font-size:11px; color:var(--text-3);">Protected by a server-side daily limit (ROFEHAI_AGENT_DAILY_LIMIT)</div>
        </div>
        <div class="risk-card" style="flex:1; min-width:180px;">
          <div style="font-size:11px; color:var(--text-3); text-transform:uppercase;">Total alerts detected</div>
          <div style="font-size:28px; font-weight:700;">${latency.total_alerts}</div>
        </div>
      </div>

      <h3 style="font-size:13px; color:var(--text-2);">Clinical alert relay latency (measured, not simulated)</h3>
      <div style="margin-bottom:20px; font-size:13px; line-height:1.9;">
        Detection &rarr; queued: <strong>${fmtSecs(latency.avg_seconds_detection_to_alert_queued)}</strong> avg<br/>
        Queued &rarr; transmitted (simulated comms latency): <strong>${fmtSecs(latency.avg_seconds_comms_latency_queue_to_transmit)}</strong> avg<br/>
        Transmitted &rarr; clinician acknowledged: <strong>${fmtSecs(latency.avg_seconds_transmit_to_clinician_ack)}</strong> avg
      </div>

      <h3 style="font-size:13px; color:var(--text-2);">Recent briefing-agent calls</h3>
      <div style="font-size:11px; color:var(--text-3); margin-bottom:8px;">Call metadata only &mdash; never conversation content.</div>
      <table style="width:100%; border-collapse:collapse;">
        <thead>
          <tr style="text-align:left; border-bottom:1px solid var(--border);">
            <th style="padding:6px 10px; font-size:11px; color:var(--text-3);">Crew</th>
            <th style="padding:6px 10px; font-size:11px; color:var(--text-3);">Question length</th>
            <th style="padding:6px 10px; font-size:11px; color:var(--text-3);">Tool calls</th>
            <th style="padding:6px 10px; font-size:11px; color:var(--text-3);">Used memory</th>
            <th style="padding:6px 10px; font-size:11px; color:var(--text-3);">When</th>
          </tr>
        </thead>
        <tbody>${calls.slice(0, 20).map(callRow).join("")}</tbody>
      </table>

      <h3 style="font-size:13px; color:var(--text-2); margin-top:24px;">Conversation audit trail</h3>
      <div style="font-size:11px; color:var(--text-3); margin-bottom:8px;">Admin-only. Full question/answer transcripts, for compliance review.</div>
      <button class="btn" type="button" id="view-audit-log-btn" style="font-size:12px;">View full conversation audit log (admin)</button>
      <div id="audit-log-container" style="margin-top:12px;"></div>
    `;

    document.getElementById("view-audit-log-btn")?.addEventListener("click", async () => {
      const adminKey = window.prompt("Enter admin key to view the audit log:");
      if (!adminKey) return;
      const container = document.getElementById("audit-log-container");
      container.innerHTML = `<div style="font-size:12px; color:var(--text-3);">Loading&hellip;</div>`;
      try {
        const res = await fetch(`${window.ROFEHAI_CONFIG.API_BASE}/agent/audit-log?limit=100`, {
          headers: { "x-api-key": adminKey },
        });
        if (!res.ok) {
          container.innerHTML = `<div style="font-size:12px; color:#e06666;">Failed to load audit log (${res.status}). Check the admin key.</div>`;
          return;
        }
        const entries = await res.json();
        if (!entries.length) {
          container.innerHTML = `<div style="font-size:12px; color:var(--text-3);">No conversation records yet.</div>`;
          return;
        }
        const esc = (s) => (s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        container.innerHTML = entries
          .map(
            (e) => `
          <div style="background:var(--surface-2); border:1px solid var(--border); border-radius:var(--radius); padding:10px 12px; margin-bottom:8px; font-size:12px;">
            <div style="color:var(--text-3); margin-bottom:6px;">${esc(e.crew_id || "fleet-wide")} &middot; ${esc(e.actor_name || "unknown user")} &middot; ${new Date(e.created_at).toLocaleString()}</div>
            <div style="margin-bottom:4px;"><strong>Q:</strong> ${esc(e.question)}</div>
            <div><strong>A:</strong> ${esc(e.answer)}</div>
          </div>`
          )
          .join("");
      } catch (err) {
        container.innerHTML = `<div style="font-size:12px; color:#e06666;">Network error loading audit log.</div>`;
      }
    });
  }

  async function renderAlertsView() {
    viewingAlerts = true;
    activeCrewId = null;
    const [pending, transmitted, acknowledged] = await Promise.all([
      RofehAPI.listAlerts("pending"),
      RofehAPI.listAlerts("transmitted"),
      RofehAPI.listAlerts("acknowledged"),
    ]);

    function alertCard(a) {
      const dot = SEVERITY_DOT[a.severity] || "nominal";
      const protocol = a.pre_authorized_protocol;
      return `
        <div class="panel" style="margin-bottom:12px;">
          <div class="panel-title">
            <span class="status-dot status-dot--${dot}"></span>
            ${a.crew_id} — ${a.domain_display_name} — <span style="text-transform:uppercase; font-size:10px;">${a.severity}</span>
          </div>
          <div class="risk-row">
            <span class="risk-figure">${Math.round(a.current_risk * 100)}%</span>
            <span class="risk-delta ${a.risk_delta_30d >= 0 ? "is-up" : "is-down"}">${a.risk_delta_30d >= 0 ? "+" : ""}${Math.round(a.risk_delta_30d * 100)}% / 30d</span>
          </div>
          <div class="risk-confidence">confidence ${a.confidence.toFixed(2)} · detected ${new Date(a.detected_at).toLocaleTimeString()}</div>
          <div class="narrative">${a.narrative}</div>
          ${
            protocol
              ? `<div class="cm-name" style="margin-top:10px;">${protocol.title}</div>
                 <div class="cm-desc">${protocol.instructions}</div>
                 <div class="cm-rationale" style="color:var(--status-elevated);">${protocol.disclaimer}</div>`
              : ""
          }
          <div style="margin-top:10px; font-size:11px; color:var(--text-3);">
            Status: <strong>${a.status}</strong>
            ${a.transmitted_at ? ` · transmitted ${new Date(a.transmitted_at).toLocaleTimeString()}` : " · queued, awaiting comms link"}
            ${a.acknowledged_at ? ` · acknowledged ${new Date(a.acknowledged_at).toLocaleTimeString()}` : ""}
          </div>
          ${
            a.status === "transmitted"
              ? `<button class="btn is-primary" style="margin-top:8px;" data-ack-alert="${a.id}">Acknowledge</button>`
              : ""
          }
        </div>
      `;
    }

    detailPanel.innerHTML = `
      <div class="detail-heading"><h1>Clinical Alerts</h1></div>
      <div style="margin-bottom:16px;">
        <strong>${pending.length}</strong> pending (queued, awaiting comms) ·
        <strong>${transmitted.length}</strong> transmitted, awaiting acknowledgment ·
        <strong>${acknowledged.length}</strong> acknowledged
      </div>
      ${pending.length ? `<h3 style="font-size:13px; color:var(--text-2);">Pending</h3>${pending.map(alertCard).join("")}` : ""}
      ${transmitted.length ? `<h3 style="font-size:13px; color:var(--text-2);">Transmitted — needs review</h3>${transmitted.map(alertCard).join("")}` : ""}
      ${acknowledged.length ? `<h3 style="font-size:13px; color:var(--text-2);">Acknowledged</h3>${acknowledged.map(alertCard).join("")}` : ""}
      ${!pending.length && !transmitted.length && !acknowledged.length ? `<div class="empty-state">No alerts yet — run a sweep to check for significant changes.</div>` : ""}
    `;

    document.querySelectorAll("[data-ack-alert]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        btn.textContent = "Acknowledging…";
        await RofehAPI.acknowledgeAlert(btn.dataset.ackAlert, "Reviewed via dashboard");
        await refreshAlertsBadge();
        renderAlertsView();
      });
    });
  }

  init();
})();
