// Point this at your running RofehAI backend.
// Production: served via Cloudflare Tunnel at app.rofehai.com (dashboard)
// and api.rofehai.com (backend) -- both proxy to the same VPS
// (srv693807, 167.88.38.169) over the tunnel, no open inbound ports
// needed. See docs/DEPLOYMENT.md.
window.ROFEHAI_CONFIG = {
  API_BASE: "https://api.rofehai.com/api",
};
