"""Skyline Car Rental — Ava voice agent backend (FastAPI).

Two jobs:
  1) GET  /token          -> mint a short-lived AssemblyAI Voice Agent token so
                             the API key never reaches the browser.
  2) POST /tools/{name}   -> run one of Ava's 7 tools against the Excel store.

Plus it serves voice-agent.html at "/".

Run:
    export ASSEMBLYAI_API_KEY=your_key_here
    pip install -r requirements.txt
    uvicorn app:app --port 8000
    # open http://localhost:8000

Region: US edge by default. For EU data residency set SKYLINE_REGION=eu, which
swaps the token host to agents.eu.assemblyai.com (and update WS_URL in the HTML
to match).
"""

from __future__ import annotations

import json
import os
import random
import urllib.error
import urllib.request
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response

import sheets

API_KEY = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
REGION = os.environ.get("SKYLINE_REGION", "us").strip().lower()

# Voice Agent API token endpoint. Bearer auth is REQUIRED on this product
# (unlike pre-recorded / streaming STT, which take the raw key).
TOKEN_HOST = "agents.eu.assemblyai.com" if REGION == "eu" else "agents.assemblyai.com"
TOKEN_URL = f"https://{TOKEN_HOST}/v1/token"
TOKEN_TTL_SECONDS = 600  # 1–600

HERE = Path(__file__).parent

app = FastAPI(title="Skyline Car Rental — Ava")


@app.on_event("startup")
def _startup() -> None:
    sheets.ensure_workbook()


# --------------------------------------------------------------------------- #
# Token mint (sync def -> FastAPI runs it in a threadpool, so the blocking
# urllib call doesn't stall the event loop).
# --------------------------------------------------------------------------- #
@app.get("/token")
def mint_token() -> Response:
    if not API_KEY:
        return JSONResponse(
            status_code=500,
            content={"error": "ASSEMBLYAI_API_KEY is not set on the server."},
        )
    req = urllib.request.Request(
        f"{TOKEN_URL}?expires_in_seconds={TOKEN_TTL_SECONDS}",
        headers={"Authorization": f"Bearer {API_KEY}"},  # Bearer: Voice Agent API only
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
        return Response(content=body, media_type="application/json",
                        headers={"cache-control": "no-store"})
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        return JSONResponse(status_code=e.code,
                            content={"error": "token_mint_failed", "detail": detail})
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=502,
                            content={"error": "token_mint_failed", "detail": str(e)})


# --------------------------------------------------------------------------- #
# Tools. Each returns a plain dict; the browser JSON-stringifies it into
# tool.result. On any failure we return {"error": ...} so Ava can gracefully
# offer a human instead of the call breaking.
# --------------------------------------------------------------------------- #
def _require(args: dict, *names: str) -> None:
    missing = [n for n in names if not str(args.get(n, "")).strip()]
    if missing:
        raise ValueError(f"Missing required field(s): {', '.join(missing)}")


def create_reservation(args: dict) -> dict:
    _require(args, "phone", "drivers_license", "car_type",
             "pickup_date", "dropoff_date", "email")
    confirmation_id = sheets.new_id("SKY")
    extras = args.get("extras", "")
    if isinstance(extras, list):
        extras = ", ".join(str(e) for e in extras)
    row = sheets.append("Reservations", {
        "confirmation_id": confirmation_id,
        "created_at": sheets._now(),
        "status": "confirmed",
        "phone": args.get("phone", ""),
        "drivers_license": args.get("drivers_license", ""),
        "email": args.get("email", ""),
        "car_type": args.get("car_type", ""),
        "pickup_date": args.get("pickup_date", ""),
        "pickup_time": args.get("pickup_time", ""),
        "dropoff_date": args.get("dropoff_date", ""),
        "dropoff_time": args.get("dropoff_time", ""),
        "protection": args.get("protection", ""),
        "extras": extras,
        "days": args.get("days", ""),
        "daily_rate": args.get("daily_rate", ""),
        "total": args.get("total", ""),
        "notes": args.get("notes", ""),
    })
    return {
        "ok": True,
        "confirmation_id": confirmation_id,
        "status": "confirmed",
        "car_type": row["car_type"],
        "total": row["total"],
        "email": row["email"],
        "message": f"Reservation {confirmation_id} confirmed.",
    }


def _public_reservation(r: dict) -> dict:
    """Strip internal fields before handing a reservation back to the agent."""
    return {k: v for k, v in r.items() if k != "_row"}


def lookup_reservation(args: dict) -> dict:
    _require(args, "phone", "drivers_license")
    r = sheets.find_reservation(args["phone"], args["drivers_license"])
    if not r:
        return {"found": False,
                "message": "No active reservation found for that phone and license."}
    return {"found": True, "reservation": _public_reservation(r)}


def modify_reservation(args: dict) -> dict:
    _require(args, "phone", "drivers_license")
    r = sheets.find_reservation(args["phone"], args["drivers_license"])
    if not r:
        return {"found": False,
                "message": "No active reservation found for that phone and license."}
    updatable = ["car_type", "pickup_date", "pickup_time", "dropoff_date",
                 "dropoff_time", "protection", "days", "daily_rate", "total"]
    updates = {k: args[k] for k in updatable if k in args and str(args[k]).strip()}
    note = args.get("note") or args.get("add_note")
    updated = sheets.update_reservation(r["_row"], updates, append_note=note)
    return {"ok": True, "confirmation_id": updated.get("confirmation_id"),
            "reservation": updated, "message": "Reservation updated."}


def cancel_reservation(args: dict) -> dict:
    _require(args, "phone", "drivers_license")
    r = sheets.find_reservation(args["phone"], args["drivers_license"])
    if not r:
        return {"found": False,
                "message": "No active reservation found for that phone and license."}
    sheets.update_reservation(r["_row"], {"status": "cancelled"},
                              append_note="Cancelled via Ava")
    return {"ok": True, "confirmation_id": r.get("confirmation_id"),
            "status": "cancelled",
            "message": "Reservation cancelled. A $25 fee applies if within 24 hours of pickup."}


def file_incident_report(args: dict) -> dict:
    _require(args, "what_happened", "location")
    incident_id = sheets.new_id("INC")
    sheets.append("Incidents", {
        "incident_id": incident_id,
        "created_at": sheets._now(),
        "phone": args.get("phone", ""),
        "drivers_license": args.get("drivers_license", ""),
        "what_happened": args.get("what_happened", ""),
        "location": args.get("location", ""),
        "occurred_at": args.get("occurred_at", args.get("datetime", "")),
        "drivable": args.get("drivable", ""),
        "status": "open",
    })
    return {"ok": True, "incident_id": incident_id,
            "message": f"Incident {incident_id} logged. A claims rep will follow up."}


def file_lost_item(args: dict) -> dict:
    _require(args, "item_description")
    case_id = sheets.new_id("LAF")
    sheets.append("LostAndFound", {
        "case_id": case_id,
        "created_at": sheets._now(),
        "phone": args.get("phone", ""),
        "drivers_license": args.get("drivers_license", ""),
        "item_description": args.get("item_description", ""),
        "location": args.get("location", args.get("vehicle", "")),
        "status": "open",
    })
    return {"ok": True, "case_id": case_id,
            "message": f"Lost item report {case_id} filed. The team will follow up if found."}


def start_roadside_assist(args: dict) -> dict:
    _require(args, "issue_type", "location")
    case_id = sheets.new_id("RSA")
    eta = random.choice([30, 35, 40, 45, 50])  # stand-in dispatch ETA
    sheets.append("Roadside", {
        "case_id": case_id,
        "created_at": sheets._now(),
        "phone": args.get("phone", ""),
        "drivers_license": args.get("drivers_license", ""),
        "issue_type": args.get("issue_type", ""),
        "location": args.get("location", ""),
        "eta_minutes": eta,
        "status": "dispatched",
    })
    return {"ok": True, "case_id": case_id, "eta_minutes": eta,
            "message": f"Help is on the way — about {eta} minutes out."}


TOOLS = {
    "create_reservation": create_reservation,
    "lookup_reservation": lookup_reservation,
    "modify_reservation": modify_reservation,
    "cancel_reservation": cancel_reservation,
    "file_incident_report": file_incident_report,
    "file_lost_item": file_lost_item,
    "start_roadside_assist": start_roadside_assist,
}


@app.post("/tools/{tool_name}")
async def run_tool(tool_name: str, request: Request) -> JSONResponse:
    handler = TOOLS.get(tool_name)
    if handler is None:
        raise HTTPException(status_code=404, detail=f"Unknown tool: {tool_name}")
    try:
        args = await request.json()
    except Exception:  # noqa: BLE001
        args = {}
    if not isinstance(args, dict):
        args = {}
    try:
        # Tools touch the workbook (blocking); run off the event loop.
        from anyio import to_thread
        result = await to_thread.run_sync(lambda: handler(args))
        return JSONResponse(content=result)
    except ValueError as e:
        return JSONResponse(content={"error": str(e)})
    except Exception as e:  # noqa: BLE001
        return JSONResponse(content={"error": f"Tool failed: {e}"})


# --------------------------------------------------------------------------- #
# Static: the single-page voice client.
# --------------------------------------------------------------------------- #
@app.get("/")
def index() -> FileResponse:
    return FileResponse(HERE / "voice-agent.html")
