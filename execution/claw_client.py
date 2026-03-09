"""
ClawClient — The Agent SDK for communicating through the NexusRelay.

Integrates Phase 1 identity (clawnexus_identity.py) with the Phase 2 relay.
Provides:
  - send_mission(): Sign and POST a C.C.P payload to the relay.
  - poll_loop():    Continuously long-poll for incoming messages, verify signatures.

Usage:
  from claw_client import ClawClient
  client = ClawClient(relay_url, private_key_hex, public_key_hex, token)
  await client.send_mission(payload, receiver_did)
  await client.poll_loop()
"""

import asyncio
import json
import uuid
import logging

import aiohttp

from clawnexus_identity import (
    DID_PREFIX,
    sign_payload,
    verify_payload,
)

log = logging.getLogger("ClawClient")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ClawClient] %(levelname)s %(message)s"
)


class ClawClient:
    """
    A client representing a single OpenClaw agent that can send and receive
    messages through the NexusRelay using the C.C.P protocol.
    """

    def __init__(
        self,
        relay_url: str,
        private_key_hex: str,
        public_key_hex: str,
        auth_token: str = "",
    ):
        self.relay_url = relay_url.rstrip("/")
        self.private_key_hex = private_key_hex
        self.public_key_hex = public_key_hex
        self.did = f"{DID_PREFIX}{public_key_hex}"
        self.auth_token = auth_token

    def _headers(self) -> dict:
        """Returns the standard request headers including Bearer auth."""
        h = {"Content-Type": "application/json"}
        if self.auth_token:
            h["Authorization"] = f"Bearer {self.auth_token}"
        return h

    # ----------------------------------------------------------------
    # SENDER: Wrap payload in C.C.P, sign it, POST to /send
    # ----------------------------------------------------------------
    async def send_mission(
        self,
        payload: dict,
        receiver_did: str,
        session: aiohttp.ClientSession | None = None,
    ) -> dict:
        """
        Constructs a full C.C.P message, signs the payload, and POSTs
        it to the NexusRelay /send endpoint.
        """
        # Build the C.C.P envelope
        message = {
            "protocol_version": "1.0-ClawNexus",
            "message_id": str(uuid.uuid4()),
            "sender_did": self.did,
            "receiver_did": receiver_did,
            "payload": payload,
        }

        # Sign the entire message body
        signature = sign_payload(message, self.private_key_hex)
        message["signature"] = signature

        # POST to relay
        close_after = session is None
        session = session or aiohttp.ClientSession()
        try:
            async with session.post(
                f"{self.relay_url}/send",
                json=message,
                headers=self._headers(),
            ) as resp:
                result = await resp.json()
                log.info(f"Sent mission to {receiver_did[:30]}... → {result}")
                return result
        finally:
            if close_after:
                await session.close()

    # ----------------------------------------------------------------
    # POLLER: Long-poll /poll, verify signature on receipt
    # ----------------------------------------------------------------
    async def poll_once(
        self,
        wait: int = 30,
        session: aiohttp.ClientSession | None = None,
    ) -> dict | None:
        """
        Performs a single long-poll request. Returns the verified message
        or None if no message arrived within the wait window.
        """
        close_after = session is None
        session = session or aiohttp.ClientSession()
        try:
            async with session.get(
                f"{self.relay_url}/poll",
                params={"did": self.did, "wait": str(wait)},
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=wait + 5),
            ) as resp:
                if resp.status == 204:
                    return None  # No message yet

                message = await resp.json()

                # --- SECURITY: Verify the sender's signature ---
                signature = message.pop("signature", "")
                sender_did = message.get("sender_did", "")

                if not sender_did.startswith(DID_PREFIX):
                    log.warning(f"Rejected message — invalid sender DID: {sender_did}")
                    return None

                sender_pubkey = sender_did[len(DID_PREFIX):]
                is_valid = verify_payload(message, signature, sender_pubkey)

                if not is_valid:
                    log.warning("⚠️  REJECTED: Signature verification FAILED! Possible tampering.")
                    return None

                log.info(f"✅ Verified message from {sender_did[:30]}...")
                # Re-attach signature for downstream logging
                message["signature"] = signature
                return message
        finally:
            if close_after:
                await session.close()

    async def poll_loop(self, wait: int = 30, callback=None):
        """
        Continuously long-polls the relay for messages.
        Calls `callback(message)` on each verified message, or prints it.
        """
        log.info(f"🦞 Polling started for {self.did[:30]}...")
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    message = await self.poll_once(wait=wait, session=session)
                    if message:
                        if callback:
                            callback(message)
                        else:
                            print(f"\n📨 Incoming Mission:\n{json.dumps(message, indent=2)}\n")
                except asyncio.CancelledError:
                    log.info("Poll loop cancelled.")
                    break
                except Exception as e:
                    log.error(f"Poll error: {e}. Retrying in 3s...")
                    await asyncio.sleep(3)
