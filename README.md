# Skyline Car Rental — Ava (voice agent)

A browser-based voice agent for **Skyline Car Rental**. You talk to **Ava** at
the desk; she answers questions and handles reservations, incidents, roadside,
and lost & found — persisting every record to a local **Excel workbook**.

Built on the **AssemblyAI Voice Agent API**: one WebSocket handles speech-in,
the LLM (Ava's system prompt), TTS, turn detection, and tool calls.

```
Browser (mic + speaker)  ──ws──▶  AssemblyAI Voice Agent API
        │                              │  tool.call
        │  POST /tools/{name}          ▼
        └────────────────────▶  FastAPI ──▶ data/skyline.xlsx
                               GET /token  (mints the WS token; key stays server-side)
```

## What Ava can do
- Answer FAQs and quote totals (she does the tax math herself — there is no
  `check_availability` tool).
- **Tools that write to Excel:** `create_reservation`, `lookup_reservation`,
  `modify_reservation`, `cancel_reservation`, `file_incident_report`,
  `file_lost_item`, `start_roadside_assist`.

Records land in `data/skyline.xlsx`, one worksheet per category:
**Reservations**, **Incidents**, **Roadside**, **LostAndFound**. The file is
created with headers on first run.

> ⚠️ Don't keep `skyline.xlsx` open in Excel during a live call — Excel locks the
> file and can overwrite Ava's writes. Close it, take the call, then reopen.

## Run it

Requires **Python 3.10+** and an AssemblyAI API key.

```bash
pip install -r requirements.txt
export ASSEMBLYAI_API_KEY=your_key_here     # Windows: set ASSEMBLYAI_API_KEY=...
uvicorn app:app --port 8000
```

Open **http://localhost:8000**, click **Start call**, allow the microphone, and
talk. Click **End call** when you're done.

## Notes & knobs
- **Voice:** Ava uses `ivy`. Change `AGENT_VOICE` near the top of
  `voice-agent.html` to any valid Voice Agent voice id.
- **Region/EU:** set `SKYLINE_REGION=eu` and change `WS_URL` in
  `voice-agent.html` to `wss://agents.eu.assemblyai.com/v1/ws`.
- **System prompt:** lives in `voice-agent.html` as `SYSTEM_PROMPT`. A few
  policy figures in your original prompt were left as `$X` (fuel refuel fee, toll
  admin fee) — fill those in if you want Ava to quote them.
- **This is a prototype, not a transacting system.** It writes to a local Excel
  file with no payment, no real availability check, and no auth beyond
  phone + license matching. Don't point it at real customer data as-is.

## Moving to live Google Sheets later
Swap the read/append/update calls in `sheets.py` for `gspread` against a Google
**service account**: enable the Google Sheets API, create a service account,
download its JSON key, and share each spreadsheet with the service account's
`client_email` as **Editor**. The FastAPI layer and the browser client don't
change — only `sheets.py`.

## Files
| File | Purpose |
|------|---------|
| `app.py` | FastAPI: `/token` mint + `/tools/{name}` + serves the page |
| `sheets.py` | Excel (`openpyxl`) datastore layer |
| `voice-agent.html` | Browser voice client (Ava's prompt, tools, audio) |
| `requirements.txt` | Python deps |
| `.env.example` | Environment variables |
