(() => {
  let missionLengthDays = 210;
  let activeCrewId = null;
  let chart = null;

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
    const [meta, crew] = await Promise.all([RofehAPI.missionMeta(), RofehAPI.listCrew()]);
    missionLengthDays = meta.mission_length_days;
    missionNameEl.textContent = meta.name;
    missionDayEl.textContent = missionLengthDays;

    rosterList.innerHTML = "";
    for (const member of crew) {
      const li = document.createElement("li");
      li.className = "roster-item";
      li.dataset.crewId = member.id;
      li.innerHTML = `
        <span class="status-dot status-dot--nominal" data-role="dot"></span>
        <span class="roster-item-name">${member.name}</span>
        <span class="roster-item-day">${member.id}</span>
      `;
      li.addEventListener("click", () => selectCrew(member.id, member.name));
      rosterList.appendChild(li);
    }

    // Pre-fetch risk to color the roster dots, then select the first crew member.
    for (const member of crew) {
      const risk = await RofehAPI.risk(member.id, "sans", missionLengthDays);
      const dot = rosterList.querySelector(`[data-crew-id="${member.id}"] [data-role="dot"]`);
      const level = statusForRisk(risk.current_risk, risk.is_fallback);
      dot.className = `status-dot status-dot--${level}`;
    }

    if (crew.length) selectCrew(crew[0].id, crew[0].name);
  }

  async function selectCrew(crewId, name) {
    activeCrewId = crewId;
    document.querySelectorAll(".roster-item").forEach((el) => {
      el.classList.toggle("is-active", el.dataset.crewId === crewId);
    });

    detailPanel.innerHTML = `<div class="empty-state">Loading ${name}&rsquo;s trajectory&hellip;</div>`;

    const [obs, riskSans, riskImmune, cmSans, cmImmune] = await Promise.all([
      RofehAPI.observations(crewId, missionLengthDays),
      RofehAPI.risk(crewId, "sans", missionLengthDays),
      RofehAPI.risk(crewId, "immune_dysregulation", missionLengthDays),
      RofehAPI.countermeasure(crewId, "sans", missionLengthDays),
      RofehAPI.countermeasure(crewId, "immune_dysregulation", missionLengthDays),
    ]);

    render(name, obs, riskSans, riskImmune, cmSans, cmImmune);
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

  function countermeasurePanel(cmSans, cmImmune) {
    const items = [cmSans, cmImmune].filter(Boolean);
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

  function render(name, obs, riskSans, riskImmune, cmSans, cmImmune) {
    detailPanel.innerHTML = `
      <div class="detail-heading">
        <h1>${name}</h1>
        <span class="subtitle">${activeCrewId}</span>
      </div>
      <div class="grid">
        ${riskPanel("SANS risk trajectory", riskSans)}
        ${riskPanel("Immune dysregulation risk", riskImmune)}
        <div class="panel chart-panel">
          <div class="panel-title">Signal trends</div>
          <div class="chart-wrap"><canvas id="trend-chart"></canvas></div>
        </div>
        ${countermeasurePanel(cmSans, cmImmune)}
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
            data: obs.map((o) => o.onsd_mm),
            borderColor: "#E5615C",
            backgroundColor: "transparent",
            yAxisID: "y",
            pointRadius: 0,
            tension: 0.25,
          },
          {
            label: "EBV shedding (copies/mL)",
            data: obs.map((o) => o.ebv_shedding_copies_ml),
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

  init();
})();
