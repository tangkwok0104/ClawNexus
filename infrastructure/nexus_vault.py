"""
Nexus Vault v3 — The Economic Engine for ClawNexus (Supabase-backed).

Manages agent balances, escrow locks/releases, and platform commission
(2% fee on every approved mission). All data persisted to Supabase Cloud Postgres.

Backward-compatible API: same function signatures as v1 (JSON-based).
"""

import uuid
from datetime import datetime, timezone

from infrastructure import nexus_db as db

# --- Configuration ---
PLATFORM_COMMISSION_RATE = 0.02  # 2% infrastructure fee


def calculate_fees(gross_amount: float) -> dict:
    """
    Calculate the platform fee breakdown for a given amount.
    Returns: {"gross": ..., "commission": ..., "net": ...}
    """
    commission = round(gross_amount * PLATFORM_COMMISSION_RATE, 4)
    net = round(gross_amount - commission, 4)
    return {"gross": gross_amount, "commission": commission, "net": net}


# ============================================================
# Public API (backward-compatible with Phase 3)
# ============================================================

def deposit(agent_did: str, amount: float) -> dict:
    """Add credits to an agent's wallet."""
    db.update_agent_balance(agent_did, amount)
    db.log_transaction("DEPOSIT", agent_did, amount)
    return {"status": "ok", "balance": db.get_agent_balance(agent_did)}


def get_balance(agent_did: str) -> float:
    """Get an agent's current available balance."""
    return db.get_agent_balance(agent_did)


def get_platform_balance() -> dict:
    """Get the platform's treasury wallet."""
    return db.get_treasury()


def lock_escrow(mission_id: str, payer_did: str, amount: float,
                title: str = "", description: str = "", receiver_did: str = None) -> dict:
    """
    Move funds from an agent's balance into escrow for a mission.
    Deducts the 2% platform commission upfront.
    """
    balance = db.get_agent_balance(payer_did)
    if balance < amount:
        return {
            "status": "error",
            "reason": "Insufficient balance",
            "balance": balance,
            "required": amount
        }

    existing = db.get_mission(mission_id)
    if existing:
        return {"status": "error", "reason": "Mission already in escrow"}

    fees = calculate_fees(amount)

    # Deduct full amount from payer
    db.update_agent_balance(payer_did, -amount)

    # Credit platform treasury with commission
    db.credit_treasury(fees["commission"])

    # Create mission record with escrow
    db.create_mission(
        mission_id=mission_id,
        sender_did=payer_did,
        receiver_did=receiver_did,
        title=title,
        description=description,
        gross_amount=fees["gross"],
        commission=fees["commission"],
        net_amount=fees["net"],
        status="ESCROWED"
    )

    # Log it
    db.log_transaction(
        "ESCROW_LOCK", payer_did, amount,
        fee_collected=fees["commission"],
        mission_id=mission_id
    )

    return {
        "status": "ok",
        "mission_id": mission_id,
        "gross_amount": fees["gross"],
        "commission": fees["commission"],
        "net_escrowed": fees["net"],
        "remaining_balance": db.get_agent_balance(payer_did)
    }


def release_escrow(mission_id: str, payee_did: str) -> dict:
    """
    Task completed — release escrowed funds to the payee (mentor).
    """
    mission = db.get_mission(mission_id)
    if not mission:
        return {"status": "error", "reason": "Mission not found in escrow"}
    if mission["status"] != "ESCROWED":
        return {"status": "error", "reason": f"Mission status is {mission['status']}, not ESCROWED"}

    # Pay the mentor the net amount
    net = mission["net_amount"]
    db.update_agent_balance(payee_did, net)
    db.update_agent_total_earned(payee_did, net)
    db.update_mission_status(mission_id, "COMPLETED")

    db.log_transaction(
        "ESCROW_RELEASE", payee_did, net,
        mission_id=mission_id,
        details=f"paid_by={mission['sender_did']}"
    )

    return {
        "status": "ok",
        "mission_id": mission_id,
        "paid_to": payee_did,
        "amount": net
    }


def refund_escrow(mission_id: str) -> dict:
    """
    Task rejected/failed — return escrowed funds to the payer.
    Platform keeps commission (non-refundable processing fee).
    """
    mission = db.get_mission(mission_id)
    if not mission:
        return {"status": "error", "reason": "Mission not found in escrow"}
    if mission["status"] != "ESCROWED":
        return {"status": "error", "reason": f"Mission status is {mission['status']}, not ESCROWED"}

    # Refund net amount to payer
    db.update_agent_balance(mission["sender_did"], mission["net_amount"])
    db.update_mission_status(mission_id, "REFUNDED")

    db.log_transaction(
        "ESCROW_REFUND", mission["sender_did"], mission["net_amount"],
        mission_id=mission_id,
        details=f"commission_retained={mission['commission']}"
    )

    return {
        "status": "ok",
        "mission_id": mission_id,
        "refunded_to": mission["sender_did"],
        "amount": mission["net_amount"],
        "commission_retained": mission["commission"]
    }


def complete_mission(mission_id: str, payee_did: str) -> dict:
    """
    Called when a MISSION_COMPLETE message is verified.
    Releases escrow to the mentor who completed the work.
    """
    return release_escrow(mission_id, payee_did)


# Alias for Phase 4 prompt compatibility
process_mission_payout = complete_mission
