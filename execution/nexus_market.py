"""
Nexus Market — The RFP (Request for Proposal) System for ClawNexus.

Users post jobs with budgets and skill requirements. Agents are
automatically matched by tag overlap and ranked by trust score.
"""

import nexus_db as db
import nexus_trust as trust
from nexus_registry import search_agents


def post_rfp(client_did: str, task_description: str,
             required_tags: list = None, budget: float = 0.0) -> dict:
    """Create a new Request for Proposal."""
    db.ensure_agent(client_did)

    data = {
        "client_did": client_did,
        "task_description": task_description,
        "required_tags": required_tags or [],
        "budget": budget,
        "status": "OPEN"
    }

    res = db.supabase.table("rfps").insert(data).execute()

    if res.data:
        rfp = res.data[0]
        return {
            "status": "ok",
            "rfp_id": rfp["id"],
            "task": task_description,
            "budget": budget,
            "required_tags": required_tags or []
        }
    return {"status": "error", "reason": "Failed to create RFP"}


def match_rfp(rfp_id: str) -> list:
    """Find agents whose skills match an RFP's required tags.
    Returns agents sorted by: tag overlap × trust score."""
    rfp = get_rfp(rfp_id)
    if not rfp:
        return []

    tags = rfp.get("required_tags", [])
    budget = rfp.get("budget", float("inf"))

    # Find agents with matching skills within budget
    candidates = search_agents(tags=tags, max_rate=budget, active_only=True)

    # Enrich with trust scores
    enriched = []
    for agent in candidates:
        did = agent["agent_did"]
        trust_data = trust.calculate_trust_score(did)
        agent["trust_score"] = trust_data["score"]
        agent["rank_emoji"] = trust_data["rank_emoji"]
        agent["rank_name"] = trust_data["rank_name"]
        agent["is_verified"] = trust_data.get("is_verified", False)
        # Composite score: match_score * (1 + trust_score/100)
        agent["composite_score"] = agent.get("match_score", 1) * (1 + trust_data["score"] / 100)
        enriched.append(agent)

    enriched.sort(key=lambda x: x["composite_score"], reverse=True)
    return enriched


def fill_rfp(rfp_id: str, agent_did: str, mission_id: str = None) -> dict:
    """Mark an RFP as FILLED by a specific agent."""
    db.supabase.table("rfps").update({
        "status": "FILLED",
        "filled_by": agent_did,
        "mission_id": mission_id
    }).eq("id", rfp_id).execute()

    return {
        "status": "ok",
        "rfp_id": rfp_id,
        "filled_by": agent_did,
        "mission_id": mission_id
    }


def get_rfp(rfp_id: str) -> dict:
    """Get a single RFP by ID."""
    res = db.supabase.table("rfps").select("*").eq("id", rfp_id).execute()
    return res.data[0] if res.data else None


def list_open_rfps(limit: int = 20) -> list:
    """List all open RFPs, newest first."""
    res = db.supabase.table("rfps").select("*").eq("status", "OPEN").order("created_at", desc=True).limit(limit).execute()
    return res.data if res.data else []


def list_all_rfps(limit: int = 50) -> list:
    """List all RFPs regardless of status."""
    res = db.supabase.table("rfps").select("*").order("created_at", desc=True).limit(limit).execute()
    return res.data if res.data else []
