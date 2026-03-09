"""
send_test_mission.py — Simulates Sophia sending a MISSION_PROPOSAL to the Watchtower.

This sends a signed mission through the live NexusRelay so it appears
as an interactive embed in the Discord channel.
"""

import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from clawnexus_identity import generate_keypair, sign_payload
from claw_client import ClawClient

RELAY_URL = os.getenv("RELAY_URL", "http://localhost:8377")


async def main():
    # Generate Sophia's identity (or load from .env)
    sophia_priv, sophia_pub, sophia_did = generate_keypair()
    print(f"Sophia's DID: {sophia_did[:40]}...")

    # Load the Watchtower's DID from its keys file
    import json
    wt_keys_path = os.path.join(os.path.dirname(__file__), "..", ".watchtower_keys.json")
    if not os.path.exists(wt_keys_path):
        print("ERROR: Run the Watchtower first to generate its identity.")
        return
    with open(wt_keys_path) as f:
        wt_keys = json.load(f)
    watchtower_did = wt_keys["did"]
    print(f"Watchtower DID: {watchtower_did[:40]}...")

    # Build the mission payload
    client = ClawClient(RELAY_URL, sophia_priv, sophia_pub)
    payload = {
        "type": "MISSION_PROPOSAL",
        "mission_details": {
            "title": "Setup OpenClaw Linux Environment",
            "description": "Sophia will mentor Kevin on configuring a production Linux environment for OpenClaw development."
        },
        "economics": {
            "amount": 0.50,
            "currency": "USDC-BASE",
            "escrow_flag": True
        },
        "human_approval_required": True
    }
    await client.send_mission(payload, watchtower_did)
    print("Mission sent! Check your Discord channel.")


if __name__ == "__main__":
    asyncio.run(main())
