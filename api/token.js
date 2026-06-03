// Vercel Serverless Function  →  GET /api/token
// Mints a short-lived AssemblyAI Voice Agent token so the browser never holds
// the API key. Set ASSEMBLYAI_API_KEY in your Vercel project's Environment
// Variables (Settings → Environment Variables). Node 18+ provides global fetch.

module.exports = async (req, res) => {
  const apiKey = process.env.ASSEMBLYAI_API_KEY;
  if (!apiKey) {
    res.status(500).json({ error: "ASSEMBLYAI_API_KEY is not set in Vercel env." });
    return;
  }
  try {
    const upstream = await fetch(
      "https://agents.assemblyai.com/v1/token?expires_in_seconds=600",
      { headers: { Authorization: `Bearer ${apiKey}` } } // Bearer: Voice Agent API
    );
    const body = await upstream.text();
    res.setHeader("content-type", "application/json; charset=utf-8");
    res.setHeader("cache-control", "no-store");
    res.status(upstream.status).send(body);
  } catch (err) {
    res.status(502).json({ error: "token_mint_failed", detail: String(err) });
  }
};
