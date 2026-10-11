// Standalone WebSocket server.
//
// Why not a Next.js route handler?  The App Router cannot cleanly upgrade
// HTTP → WebSocket inside route.ts. Running ws on its own port (3001) and
// proxying it through next.config.ts rewrites is the recommended pattern.
//
// Auth: the browser sends our HttpOnly access cookie automatically because
// the WS connects to the same origin. We pull `otlm_access` out of the
// upgrade request's Cookie header and verify the JWT before accepting.
// Connections without a valid cookie are rejected with HTTP 401.
//
// Per connection:
//   - subscribe to Redis pub/sub channel  live:{tenant_id}
//   - forward every message to the WS client as JSON text
//   - send a ping every 30s; close if no pong within 5s

import http from "node:http";
import { WebSocketServer } from "ws";
import { jwtVerify } from "jose";
import Redis from "ioredis";

const PORT = Number(process.env.WS_PORT ?? 3001);
const JWT_SECRET     = process.env.JWT_SECRET_KEY ?? "";
const JWT_ALGORITHM  = process.env.JWT_ALGORITHM ?? "HS256";
const REDIS_URL      = process.env.REDIS_URL ?? "redis://localhost:6379";
const ACCESS_COOKIE  = "otlm_access";
const PING_INTERVAL  = 30_000;
const PONG_TIMEOUT   = 5_000;

// Same rules as jwtSecret() in lib/env.ts: refuse to start without a real
// secret (the old fallback was public, so anyone could forge a session).
if (JWT_SECRET.length < 32 || /change-me|change_me|replace_me|replace-me/i.test(JWT_SECRET)) {
  console.error("ws-server: JWT_SECRET_KEY is missing, shorter than 32 chars, or a placeholder. " +
                "Run `bash scripts/dev-up.sh` to create .env with random secrets.");
  process.exit(1);
}
const _secret = new TextEncoder().encode(JWT_SECRET);

function parseCookies(header) {
  const out = {};
  if (!header) return out;
  for (const part of header.split(";")) {
    const [k, ...rest] = part.trim().split("=");
    if (k) out[k] = decodeURIComponent(rest.join("="));
  }
  return out;
}

async function verifyAccess(token) {
  try {
    const { payload } = await jwtVerify(token, _secret, { algorithms: [JWT_ALGORITHM] });
    if (payload.type !== "access") return null;
    return payload;
  } catch {
    return null;
  }
}

const server = http.createServer((req, res) => {
  // Health endpoint for docker healthcheck and the test suite
  if (req.url === "/health") {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ status: "ok" }));
    return;
  }
  res.writeHead(404);
  res.end();
});

const wss = new WebSocketServer({ noServer: true });

server.on("upgrade", async (req, socket, head) => {
  if (!req.url?.startsWith("/live-metrics")) {
    socket.write("HTTP/1.1 404 Not Found\r\n\r\n");
    socket.destroy();
    return;
  }

  const cookies = parseCookies(req.headers.cookie);
  const token = cookies[ACCESS_COOKIE];
  if (!token) {
    socket.write("HTTP/1.1 401 Unauthorized\r\n\r\n");
    socket.destroy();
    return;
  }

  const claims = await verifyAccess(token);
  if (!claims) {
    socket.write("HTTP/1.1 401 Unauthorized\r\n\r\n");
    socket.destroy();
    return;
  }

  wss.handleUpgrade(req, socket, head, (ws) => {
    ws._tenantId = claims.tenant_id;
    wss.emit("connection", ws, req);
  });
});

wss.on("connection", (ws) => {
  const tenantId = ws._tenantId;
  // One subscriber connection per WS client. ioredis enforces that a
  // subscriber connection can't run normal commands, so this is the
  // simplest and safest setup. For very large fan-out we'd use a single
  // pattern subscriber and route in Node — overkill for this scale.
  const sub = new Redis(REDIS_URL, { maxRetriesPerRequest: 3 });
  const channel = `live:${tenantId}`;

  sub.subscribe(channel).catch((err) => {
    console.error("ws subscribe failed", err);
    ws.close(1011, "subscribe_failed");
  });

  sub.on("message", (_chan, payload) => {
    if (ws.readyState === ws.OPEN) ws.send(payload);
  });

  let alive = true;
  ws.on("pong", () => { alive = true; });

  const ping = setInterval(() => {
    if (!alive) {
      ws.terminate();
      return;
    }
    alive = false;
    try { ws.ping(); } catch { /* socket dying */ }
    setTimeout(() => {
      if (!alive && ws.readyState === ws.OPEN) ws.terminate();
    }, PONG_TIMEOUT);
  }, PING_INTERVAL);

  ws.on("close", () => {
    clearInterval(ping);
    sub.unsubscribe(channel).catch(() => {});
    sub.quit().catch(() => {});
  });

  ws.on("error", (err) => {
    console.error("ws connection error", err);
  });

  // Greet so the client knows the channel is live
  ws.send(JSON.stringify({ event: "subscribed", channel }));
});

server.listen(PORT, () => {
  console.log(`ws-server listening on :${PORT}`);
});

// Graceful shutdown so docker stop completes quickly
for (const sig of ["SIGINT", "SIGTERM"]) {
  process.on(sig, () => {
    console.log(`ws-server: ${sig} received`);
    wss.clients.forEach((c) => c.close(1001, "server_shutdown"));
    server.close(() => process.exit(0));
  });
}
