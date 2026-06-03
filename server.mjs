// Vinyl Concierge — local token proxy + static server (zero dependencies).
//
// Why: browsers must not hold the AssemblyAI key, and minting the temp token
// directly from the page can be blocked by CORS. This tiny server keeps the
// key server-side and exposes a same-origin GET /token that the page calls.
//
// Run (Node 18+):
//   ASSEMBLYAI_API_KEY=your_key_here node server.mjs
//   # then open http://localhost:3000  (set AUTH_MODE = "proxy" in the HTML)
//
// Optional: PORT=4000 ASSEMBLYAI_API_KEY=... node server.mjs

import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname, join, normalize, sep } from "node:path";

const PORT = process.env.PORT || 3000;
const API_KEY = process.env.ASSEMBLYAI_API_KEY;

// US edge. For EU data residency use agents.eu.assemblyai.com here AND in the
// HTML's WS_URL.
const TOKEN_URL = "https://agents.assemblyai.com/v1/token";
const TOKEN_TTL_SECONDS = 600; // 1–600

const ROOT = process.cwd();
const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".map": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".ico": "image/x-icon",
  ".png": "image/png",
};

if (!API_KEY) {
  console.error(
    "ASSEMBLYAI_API_KEY is not set.\n" +
    "Start with:  ASSEMBLYAI_API_KEY=your_key node server.mjs"
  );
  process.exit(1);
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`);

  // --- Mint a fresh, single-use temp token (key never leaves the server) ---
  // Accept both paths so the same HTML works locally and on Vercel.
  if (url.pathname === "/token" || url.pathname === "/api/token") {
    try {
      const upstream = await fetch(
        `${TOKEN_URL}?expires_in_seconds=${TOKEN_TTL_SECONDS}`,
        { headers: { Authorization: `Bearer ${API_KEY}` } } // Bearer: VA API only
      );
      const body = await upstream.text();
      res.writeHead(upstream.status, {
        "content-type": "application/json; charset=utf-8",
        "cache-control": "no-store",
      });
      res.end(body);
    } catch (err) {
      res.writeHead(502, { "content-type": "application/json; charset=utf-8" });
      res.end(JSON.stringify({ error: "token_mint_failed", detail: String(err) }));
    }
    return;
  }

  // --- Static files (only GET, only within ROOT) ---
  if (req.method !== "GET") {
    res.writeHead(405).end("Method Not Allowed");
    return;
  }
  let pathname = decodeURIComponent(url.pathname);
  if (pathname === "/") pathname = "/voice-agent.html";
  const filePath = normalize(join(ROOT, pathname));
  if (filePath !== ROOT && !filePath.startsWith(ROOT + sep)) {
    res.writeHead(403).end("Forbidden");
    return;
  }
  try {
    const data = await readFile(filePath);
    res.writeHead(200, {
      "content-type": MIME[extname(filePath).toLowerCase()] || "application/octet-stream",
    });
    res.end(data);
  } catch {
    res.writeHead(404).end("Not found");
  }
});

server.listen(PORT, () => {
  console.log(`Vinyl Concierge running:  http://localhost:${PORT}`);
  console.log(`Token endpoint:           http://localhost:${PORT}/token`);
  console.log(`Set AUTH_MODE = "proxy" in voice-agent.html.`);
});
