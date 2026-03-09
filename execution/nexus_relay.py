"""
NexusRelay Server — The Highway for ClawNexus A2A Communication.

A stateful in-memory message queue using aiohttp and Long Polling.
Agents POST signed C.C.P payloads to /send, and receivers GET them via /poll.

Run:  python nexus_relay.py
Port: 8377 (configurable via RELAY_PORT env var)
"""

import asyncio
import json
import os
import logging
from collections import defaultdict

from aiohttp import web
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

# --- Configuration ---
RELAY_PORT = int(os.getenv("RELAY_PORT", 8377))
RELAY_AUTH_TOKEN = os.getenv("RELAY_AUTH_TOKEN", "")
DEFAULT_POLL_WAIT = 30  # seconds

# --- In-Memory Mailbox ---
# Each receiver_did gets its own asyncio.Queue
mailboxes: dict[str, asyncio.Queue] = defaultdict(asyncio.Queue)

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [NexusRelay] %(levelname)s %(message)s"
)
log = logging.getLogger("NexusRelay")


# ============================================================
# Middleware: Bearer Token Authentication
# ============================================================
def check_auth(request: web.Request) -> bool:
    """Validates the Authorization: Bearer <token> header."""
    if not RELAY_AUTH_TOKEN:
        # If no token is configured, allow all (dev mode)
        return True
    auth_header = request.headers.get("Authorization", "")
    return auth_header == f"Bearer {RELAY_AUTH_TOKEN}"


# ============================================================
# POST /send — Accept a C.C.P payload and enqueue it
# ============================================================
async def handle_send(request: web.Request) -> web.Response:
    if not check_auth(request):
        return web.json_response({"error": "Unauthorized"}, status=401)

    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    # --- Validate C.C.P structure (relay acts as postman, not verifier) ---
    header = body.get("header") or {}
    receiver_did = body.get("receiver_did", "") or header.get("receiver", {}).get("did", "")

    if not receiver_did:
        return web.json_response(
            {"error": "Missing receiver_did in payload"},
            status=400
        )

    # Enqueue the message into the receiver's mailbox
    await mailboxes[receiver_did].put(body)
    log.info(f"Message queued for {receiver_did[:30]}...")

    return web.json_response({"status": "queued", "receiver": receiver_did})


# ============================================================
# GET /poll?did=<did>&wait=<seconds> — Long Polling endpoint
# ============================================================
async def handle_poll(request: web.Request) -> web.Response:
    if not check_auth(request):
        return web.json_response({"error": "Unauthorized"}, status=401)

    did = request.query.get("did", "")
    if not did:
        return web.json_response({"error": "Missing 'did' query parameter"}, status=400)

    wait = min(int(request.query.get("wait", DEFAULT_POLL_WAIT)), 60)

    queue = mailboxes[did]

    try:
        # Wait for a message to arrive, or timeout
        message = await asyncio.wait_for(queue.get(), timeout=wait)
        log.info(f"Message delivered to {did[:30]}...")
        return web.json_response(message)
    except asyncio.TimeoutError:
        # No message arrived within the wait window
        return web.Response(status=204)


# ============================================================
# GET /health — Basic status check
# ============================================================
async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({
        "status": "ok",
        "service": "NexusRelay",
        "protocol": "1.0-ClawNexus",
        "active_mailboxes": len(mailboxes)
    })


# ============================================================
# App Factory
# ============================================================
def create_app() -> web.Application:
    app = web.Application()
    app.router.add_post("/send", handle_send)
    app.router.add_get("/poll", handle_poll)
    app.router.add_get("/health", handle_health)
    return app


if __name__ == "__main__":
    log.info(f"🦞 NexusRelay starting on port {RELAY_PORT}...")
    if not RELAY_AUTH_TOKEN:
        log.warning("⚠️  No RELAY_AUTH_TOKEN set — running in OPEN/DEV mode!")
    web.run_app(create_app(), port=RELAY_PORT)
