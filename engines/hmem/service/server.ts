// RofehAI H-MEM service: exposes Meterless's H-MEM reference engine
// (../reference, Apache-2.0, vendored unmodified) over a tiny JSON/HTTP API so
// the Python briefing agent can write and recall memories across sessions.
// Internal-only: do not publish this port outside the docker network.
import http from "node:http";
import { HMEM } from "../reference/src/index.ts";

const PORT = Number(process.env.HMEM_PORT ?? 8910);
const DIR = process.env.HMEM_DIR ?? "/data";
const TOKEN = process.env.HMEM_TOKEN ?? "";

const hmem = new HMEM({ persistDir: DIR, actor: "rofehai-agent" });

function send(res: http.ServerResponse, code: number, body: unknown) {
  const out = JSON.stringify(body);
  res.writeHead(code, { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(out) });
  res.end(out);
}

async function readJson(req: http.IncomingMessage): Promise<any> {
  const chunks: Buffer[] = [];
  for await (const c of req) chunks.push(c as Buffer);
  const raw = Buffer.concat(chunks).toString("utf-8");
  return raw ? JSON.parse(raw) : {};
}

const view = (m: any, relevance?: number) => ({
  id: m.id, content: m.content, tags: m.tags, layer: m.layer, type: m.type,
  confidence: m.confidence, timestamp: m.timestamp, source: m.source, relevance,
});

const server = http.createServer(async (req, res) => {
  try {
    if (TOKEN && req.url !== "/health" && req.headers.authorization !== `Bearer ${TOKEN}`) {
      return send(res, 401, { error: "unauthorized" });
    }
    if (req.method === "GET" && req.url === "/health") {
      return send(res, 200, { ok: true, engine: "H-MEM reference", records: hmem.store.all().length, ledger: hmem.ledger.stats() });
    }
    if (req.method === "POST" && req.url === "/add") {
      const b = await readJson(req);
      const content = String(b.content ?? "").trim();
      if (content.length < 6) return send(res, 400, { error: "content too short" });
      const existing = hmem.store.all().find((m) => m.content === content);
      if (existing) return send(res, 200, { id: existing.id, deduped: true });
      const rec = hmem.add({
        content,
        type: b.type ?? "factual",
        layer: b.layer ?? "working",
        tags: Array.isArray(b.tags) ? b.tags.map(String) : [],
        confidence: typeof b.confidence === "number" ? b.confidence : 0.9,
        source: String(b.source ?? "rofehai-agent"),
        provenance: { origin: "model_response", learnedAt: Date.now(), label: String(b.label ?? "RofehAI briefing") },
      });
      return send(res, 200, { id: rec.id, deduped: false });
    }
    if (req.method === "POST" && req.url === "/query") {
      const b = await readJson(req);
      const need: string[] = Array.isArray(b.require_tags) ? b.require_tags.map(String) : [];
      // Over-fetch, then keep only memories carrying every required tag (e.g. the crew id).
      const r = hmem.query(String(b.query ?? ""), { topN: 200, threshold: typeof b.threshold === "number" ? b.threshold : 0.15 });
      const kept = r.memories
        .filter((x) => need.every((t) => x.memory.tags.includes(t)))
        .slice(0, Number(b.top_n ?? 5));
      return send(res, 200, { memories: kept.map((x) => view(x.memory, x.relevance)), trace: r.trace });
    }
    if (req.method === "POST" && req.url === "/feedback") {
      const b = await readJson(req);
      hmem.feedback(String(b.id), b.kind);
      return send(res, 200, { ok: true });
    }
    if (req.method === "GET" && req.url?.startsWith("/ledger")) {
      const id = new URL(req.url, "http://x").searchParams.get("id");
      return send(res, 200, { entries: id ? hmem.ledger.history(id) : hmem.ledger.all().slice(-50) });
    }
    send(res, 404, { error: "not found" });
  } catch (e: any) {
    send(res, 500, { error: String(e?.message ?? e) });
  }
});

server.listen(PORT, "0.0.0.0", () => console.log(`H-MEM service listening on :${PORT}, state in ${DIR}`));
