"""
Phase 2 QA Test — End-to-End Relay Simulation.

Spins up the NexusRelay in-process, then simulates:
  1. Sophia generates a keypair and sends a signed MISSION_PROPOSAL.
  2. Kevin generates a keypair and polls for the message.
  3. Kevin verifies Sophia's signature.
  4. A tampered message is rejected.

No external servers or real agents required.
"""

import asyncio
import logging
import os
import json
import sys
import unittest

# Ensure we can import from the repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from core.clawnexus_identity import generate_keypair, sign_payload, verify_payload
from core.nexus_relay import create_app
from core.claw_client import ClawClient

# --- Logging ---
os.makedirs(os.path.join(os.path.dirname(__file__), '..', '.tmp'), exist_ok=True)
logging.basicConfig(
    filename=os.path.join(os.path.dirname(__file__), '..', '.tmp', 'test_report_phase2.log'),
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
console = logging.StreamHandler()
console.setLevel(logging.INFO)
logging.getLogger('').addHandler(console)
log = logging.getLogger("Phase2QA")


class TestNexusRelay(unittest.IsolatedAsyncioTestCase):
    """Full integration test for the NexusRelay + ClawClient."""

    async def asyncSetUp(self):
        """Start relay server on a random port."""
        self.app = create_app()
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        # Get the actual port
        addr = self.site._server.sockets[0].getsockname()
        self.relay_url = f"http://127.0.0.1:{addr[1]}"
        log.info(f"Test relay started at {self.relay_url}")

    async def asyncTearDown(self):
        await self.runner.cleanup()

    async def test_health(self):
        """Test 1: /health endpoint."""
        log.info("[Test 1] Checking /health...")
        import aiohttp as aio
        async with aio.ClientSession() as s:
            async with s.get(f"{self.relay_url}/health") as resp:
                self.assertEqual(resp.status, 200)
                data = await resp.json()
                self.assertEqual(data["status"], "ok")
        log.info("[Test 1] PASSED — /health returns ok.")

    async def test_send_and_poll(self):
        """Test 2: Sophia sends → Kevin polls → Kevin verifies."""
        log.info("[Test 2] Full Sophia → Relay → Kevin handshake...")

        # Generate identities
        sophia_priv, sophia_pub, sophia_did = generate_keypair()
        kevin_priv, kevin_pub, kevin_did = generate_keypair()
        log.info(f"  Sophia DID: {sophia_did[:40]}...")
        log.info(f"  Kevin  DID: {kevin_did[:40]}...")

        sophia_client = ClawClient(self.relay_url, sophia_priv, sophia_pub)
        kevin_client = ClawClient(self.relay_url, kevin_priv, kevin_pub)

        # Sophia sends a mission
        payload = {
            "type": "MISSION_PROPOSAL",
            "mission_details": {
                "title": "Setup OpenClaw Environment",
                "description": "Phase 2 relay test."
            },
            "economics": {"amount": 0.50, "currency": "USDC-BASE", "escrow_flag": True},
            "human_approval_required": True
        }

        await sophia_client.send_mission(payload, kevin_did)
        log.info("  Sophia's message queued on relay.")

        # Kevin polls
        message = await kevin_client.poll_once(wait=5)
        self.assertIsNotNone(message, "Kevin should have received a message!")
        log.info(f"  Kevin received: {message.get('message_id', 'N/A')}")
        log.info("[Test 2] PASSED — Signature Verified & Message Delivered!")

    async def test_tampered_message_rejected(self):
        """Test 3: Tampered payload is rejected by the client."""
        log.info("[Test 3] Simulating relay-level tampering...")

        sophia_priv, sophia_pub, sophia_did = generate_keypair()
        kevin_priv, kevin_pub, kevin_did = generate_keypair()

        # Build and sign a message manually
        import uuid
        message = {
            "protocol_version": "1.0-ClawNexus",
            "message_id": str(uuid.uuid4()),
            "sender_did": sophia_did,
            "receiver_did": kevin_did,
            "payload": {"type": "MISSION_PROPOSAL", "economics": {"amount": 0.50}}
        }
        signature = sign_payload(message, sophia_priv)
        message["signature"] = signature

        # Tamper with the payload AFTER signing
        message["payload"]["economics"]["amount"] = 999.99

        # Post the tampered message directly to relay
        import aiohttp as aio
        async with aio.ClientSession() as s:
            await s.post(f"{self.relay_url}/send", json=message)

        # Kevin polls — client should REJECT
        kevin_client = ClawClient(self.relay_url, kevin_priv, kevin_pub)
        result = await kevin_client.poll_once(wait=5)

        self.assertIsNone(result, "Tampered message should be rejected!")
        log.info("[Test 3] PASSED — Tampered message was rejected by ClawClient.")

    async def test_poll_timeout(self):
        """Test 4: Poll with no message returns None (204)."""
        log.info("[Test 4] Testing poll timeout (empty mailbox)...")
        _, pub, _ = generate_keypair()
        priv, _, _ = generate_keypair()
        client = ClawClient(self.relay_url, priv, pub)
        result = await client.poll_once(wait=1)
        self.assertIsNone(result)
        log.info("[Test 4] PASSED — Empty poll returned None.")


if __name__ == "__main__":
    log.info("=== Phase 2 QA Test Suite Start ===")
    unittest.main(verbosity=2)
