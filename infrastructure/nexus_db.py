"""
Nexus DB — The Persistent Storage Layer for ClawNexus Economics.
v2: Supabase (PostgreSQL) Implementation.

Database: Supabase (https://rvyfsveukwetbqvuyqpp.supabase.co)
"""

import os
import uuid
import threading
import logging
from datetime import datetime, timezone
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

# --- Configuration ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set in .env")

# Supabase Client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Thread lock for safe concurrent access
_lock = threading.Lock()

log = logging.getLogger("NexusDB")

# ============================================================
# Agent Operations
# ============================================================

def get_agent_by_discord_id(discord_id: str) -> dict | None:
    """Look up an agent by their Discord ID. Returns agent row or None."""
    res = supabase.table("agents").select("*").eq("discord_id", discord_id).execute()
    return res.data[0] if res.data else None


def ensure_agent(did: str, discord_id: str = None, rank: str = "Iron"):
    """Create an agent record if it doesn't exist."""
    with _lock:
        # Check if exists
        res = supabase.table("agents").select("did").eq("did", did).execute()
        if not res.data:
            row = {"did": did, "balance": 0.0, "rank": rank}
            if discord_id:
                row["discord_id"] = discord_id
            supabase.table("agents").insert(row).execute()

def get_agent_balance(did: str) -> float:
    """Get an agent's current balance."""
    ensure_agent(did)
    res = supabase.table("agents").select("balance").eq("did", did).execute()
    if res.data:
        return float(res.data[0]["balance"])
    return 0.0

def update_agent_balance(did: str, delta: float):
    """Adjust an agent's balance by delta.
    Note: Supabase does not have an atomic 'increment' directly in REST without RPC.
    We read and write inside a lock for now (safe for single process Watchtower).
    """
    ensure_agent(did)
    with _lock:
        res = supabase.table("agents").select("balance").eq("did", did).execute()
        if res.data:
            current_balance = float(res.data[0]["balance"])
            new_balance = round(current_balance + delta, 4)
            supabase.table("agents").update({"balance": new_balance}).eq("did", did).execute()

# ============================================================
# Platform Treasury
# ============================================================

def get_treasury() -> dict:
    """Get the platform treasury balance and total earned."""
    res = supabase.table("platform_treasury").select("balance,total_earned").eq("id", 1).execute()
    if res.data:
        return {
            "balance": float(res.data[0]["balance"]),
            "total_earned": float(res.data[0]["total_earned"])
        }
    return {"balance": 0.0, "total_earned": 0.0}

def credit_treasury(amount: float):
    """Add commission to the platform treasury."""
    with _lock:
        res = supabase.table("platform_treasury").select("balance,total_earned").eq("id", 1).execute()
        if res.data:
            cur = res.data[0]
            new_balance = round(float(cur["balance"]) + amount, 4)
            new_total = round(float(cur["total_earned"]) + amount, 4)
            supabase.table("platform_treasury").update({
                "balance": new_balance,
                "total_earned": new_total
            }).eq("id", 1).execute()

# ============================================================
# Mission Lifecycle
# ============================================================

def create_mission(mission_id: str, sender_did: str, receiver_did: str = None,
                   title: str = "", description: str = "",
                   gross_amount: float = 0.0, commission: float = 0.0,
                   net_amount: float = 0.0, status: str = "ESCROWED"):
    """Insert a new mission record."""
    with _lock:
        data = {
            "mission_id": mission_id,
            "sender_did": sender_did,
            "receiver_did": receiver_did,
            "title": title,
            "description": description,
            "gross_amount": gross_amount,
            "commission": commission,
            "net_amount": net_amount,
            "status": status
        }
        supabase.table("missions").insert(data).execute()

def get_mission(mission_id: str) -> dict:
    """Look up a mission by ID."""
    res = supabase.table("missions").select("*").eq("mission_id", mission_id).execute()
    return res.data[0] if res.data else None

def update_mission_status(mission_id: str, status: str):
    """Update a mission's status (ESCROWED → COMPLETED / REFUNDED)."""
    with _lock:
        data = {"status": status}
        if status == "COMPLETED":
            data["completed_at"] = datetime.now(timezone.utc).isoformat()
        supabase.table("missions").update(data).eq("mission_id", mission_id).execute()

def list_missions(status: str = None, limit: int = 50) -> list:
    """List missions, optionally filtered by status."""
    query = supabase.table("missions").select("*").order("created_at", desc=True).limit(limit)
    if status:
        query = query.eq("status", status)
    res = query.execute()
    return res.data

# ============================================================
# Transaction Log (Immutable Audit Trail)
# ============================================================

def log_transaction(tx_type: str, agent_did: str, amount: float,
                    fee_collected: float = 0.0, mission_id: str = None,
                    details: str = ""):
    """Append an immutable transaction record."""
    tx_id = str(uuid.uuid4())[:12]
    with _lock:
        data = {
            "tx_id": tx_id,
            "tx_type": tx_type,
            "agent_did": agent_did,
            "amount": amount,
            "fee_collected": fee_collected,
            "mission_id": mission_id,
            "details": details
        }
        supabase.table("transactions").insert(data).execute()
    return tx_id

def get_transactions(agent_did: str = None, limit: int = 50) -> list:
    """Retrieve transaction history."""
    query = supabase.table("transactions").select("*").order("created_at", desc=True).limit(limit)
    if agent_did:
        query = query.eq("agent_did", agent_did)
    res = query.execute()
    return res.data

# ============================================================
# Agent Earnings Tracking
# ============================================================

def update_agent_total_earned(did: str, amount: float):
    """Increment an agent's lifetime total_earned."""
    ensure_agent(did)
    with _lock:
        res = supabase.table("agents").select("total_earned").eq("did", did).execute()
        if res.data:
            current = float(res.data[0].get("total_earned", 0.0))
            new_total = round(current + amount, 4)
            supabase.table("agents").update({"total_earned": new_total}).eq("did", did).execute()

# ============================================================
# Aggregation Helpers (for /nexus-stats Dashboard)
# ============================================================

def count_agents() -> int:
    """Count total registered agents."""
    res = supabase.table("agents").select("did", count="exact").execute()
    return res.count if res.count is not None else len(res.data)

def count_missions_by_status(status: str = None) -> int:
    """Count missions, optionally filtered by status."""
    query = supabase.table("missions").select("mission_id", count="exact")
    if status:
        query = query.eq("status", status)
    res = query.execute()
    return res.count if res.count is not None else len(res.data)

def get_dashboard_stats() -> dict:
    """Aggregate stats for the Founder's Dashboard."""
    treasury = get_treasury()
    return {
        "treasury_balance": treasury["balance"],
        "total_fees_collected": treasury["total_earned"],
        "active_missions": count_missions_by_status("ESCROWED"),
        "completed_missions": count_missions_by_status("COMPLETED"),
        "total_agents": count_agents()
    }

# ============================================================
# Reviews & Reputation
# ============================================================

def insert_review(mission_id: str, reviewer_did: str, agent_did: str,
                  rating: int, comment: str = ""):
    """Insert a review and recalculate the agent's rating."""
    with _lock:
        supabase.table("reviews").insert({
            "mission_id": mission_id,
            "reviewer_did": reviewer_did,
            "agent_did": agent_did,
            "rating": rating,
            "comment": comment
        }).execute()
    recalc_agent_rating(agent_did)

def recalc_agent_rating(agent_did: str):
    """Recompute rating_avg and review_count from the reviews table."""
    res = supabase.table("reviews").select("rating").eq("agent_did", agent_did).execute()
    if res.data:
        ratings = [r["rating"] for r in res.data]
        avg = round(sum(ratings) / len(ratings), 2)
        count = len(ratings)
    else:
        avg = 0.0
        count = 0
    with _lock:
        supabase.table("agents").update({
            "rating_avg": avg,
            "review_count": count
        }).eq("did", agent_did).execute()

def get_agent_profile(did: str) -> dict:
    """Full agent profile for trust scoring and /nexus-profile."""
    ensure_agent(did)
    res = supabase.table("agents").select("*").eq("did", did).execute()
    if not res.data:
        return None
    agent = res.data[0]

    # Count completed and failed missions for this agent (as receiver)
    completed = count_missions_by_status_for_agent(did, "COMPLETED")
    failed = count_missions_by_status_for_agent(did, "REFUNDED")

    agent["completed_missions"] = completed
    agent["failed_missions"] = failed
    return agent

def count_missions_by_status_for_agent(did: str, status: str) -> int:
    """Count missions where this agent was the receiver with a given status."""
    res = supabase.table("missions").select("mission_id", count="exact").eq("receiver_did", did).eq("status", status).execute()
    return res.count if res.count is not None else len(res.data)

def get_all_agents() -> list:
    """Get all registered agents."""
    res = supabase.table("agents").select("did,balance,total_earned,rating_avg,review_count,is_verified").execute()
    return res.data if res.data else []

def set_agent_verified(did: str, verified: bool = True):
    """Toggle verification badge on an agent."""
    ensure_agent(did)
    with _lock:
        supabase.table("agents").update({"is_verified": verified}).eq("did", did).execute()

def get_reviews_for_agent(agent_did: str, limit: int = 10) -> list:
    """Get recent reviews for an agent."""
    res = supabase.table("reviews").select("*").eq("agent_did", agent_did).order("created_at", desc=True).limit(limit).execute()
    return res.data if res.data else []

# Note: Unlike SQLite, we don't need init_db() here because
# Supabase migrations are applied centrally via MCP.
