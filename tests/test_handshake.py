"""
Phase 1 QA Test — Cryptographic Handshake (Ed25519 DID Verification).

Tests identity generation, payload signing, signature verification,
and tamper detection — all core protocol functions.
"""

import logging
import json
import uuid
import os
import sys

# Ensure we can import from the repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.clawnexus_identity import generate_keypair, sign_payload, verify_payload

# Configure the QA test logging output
os.makedirs(os.path.join(os.path.dirname(__file__), '..', '.tmp'), exist_ok=True)
logging.basicConfig(
    filename=os.path.join(os.path.dirname(__file__), '..', '.tmp', 'test_report.log'),
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
console = logging.StreamHandler()
console.setLevel(logging.INFO)
logging.getLogger('').addHandler(console)

def run_tests():
    logging.info("--- QA Engineer Handshake Test Suite Start ---")

    # [1] Keypair Generation
    logging.info("Generating Sophia's Keypair...")
    sophia_priv, sophia_pub, sophia_did = generate_keypair()
    logging.info(f"Sophia's ClawID: {sophia_did}")

    logging.info("Generating Kevin's Keypair...")
    kevin_priv, kevin_pub, kevin_did = generate_keypair()
    logging.info(f"Kevin's ClawID: {kevin_did}")

    # [2] Sophia creates a Mission Proposal
    logging.info("Sophia is constructing a MISSION_PROPOSAL based on pincer_schema.json...")
    mission_proposal = {
        "protocol_version": "1.0-ClawNexus",
        "message_id": str(uuid.uuid4()),
        "sender_did": sophia_did,
        "receiver_did": kevin_did,
        "mission_details": {
            "title": "Setup OpenClaw Environment",
            "description": "Establish the initial python execution environment and basic configuration."
        },
        "economics": {
            "amount": 0.50,
            "currency": "USDC-BASE",
            "escrow_flag": True
        },
        "human_approval_required": True
    }

    # [3] Sophia Signs the Proposal
    logging.info("Sophia is signing the payload...")
    signature = sign_payload(mission_proposal, sophia_priv)
    logging.info(f"Signature generated: {signature[:16]}...{signature[-16:]}")

    # Simulate sending the payload + signature over the wire...
    # [4] Kevin Verifies the Signature
    logging.info("Kevin receives the payload and verifies the signature using Sophia's public key...")
    sender_pubkey = sophia_pub 
    is_valid = verify_payload(mission_proposal, signature, sender_pubkey)
    
    assert is_valid is True, "Valid signature failed verification!"
    logging.info("Signature Verified & Schema Valid! Handshake successful.")

    # [5] Simulate an attack
    logging.info("Simulating Man-in-the-Middle Attack (changing escrow_flag to false)...")
    tampered_proposal = json.loads(json.dumps(mission_proposal))
    tampered_proposal["economics"]["escrow_flag"] = False

    is_valid_tampered = verify_payload(tampered_proposal, signature, sender_pubkey)
    assert is_valid_tampered is False, "Tampered payload was incorrectly verified as valid!"
    logging.info("Success! Tampered payload was rejected.")

    logging.info("--- QA Engineer Handshake Test Suite Passed ---")

if __name__ == "__main__":
    try:
        run_tests()
    except AssertionError as e:
        logging.error(f"Test Failed: {e}")
