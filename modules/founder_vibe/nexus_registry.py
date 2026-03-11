"""
Nexus Registry — The Yellow Pages for ClawNexus Agents.

Agents broadcast their skills, rates, and availability into a public
registry. Users and other agents can search by skill tags to find
the right expert for any job.
"""

from infrastructure import nexus_db as db


def register_agent(did: str, skills: list, description: str = "",
                   base_rate: float = 0.0) -> dict:
    """Register or update an agent's marketplace listing."""
    db.ensure_agent(did)

    # Upsert into registry
    existing = db.supabase.table("registry").select("agent_did").eq("agent_did", did).execute()

    data = {
        "agent_did": did,
        "skill_tags": skills,
        "description": description,
        "base_rate": base_rate,
        "is_active": True,
        "updated_at": "now()"
    }

    if existing.data:
        db.supabase_admin.table("registry").update(data).eq("agent_did", did).execute()
    else:
        db.supabase_admin.table("registry").insert(data).execute()

    return {
        "status": "ok",
        "did": did,
        "skills": skills,
        "base_rate": base_rate,
        "message": "Listing updated" if existing.data else "Listing created"
    }


def deactivate_listing(did: str) -> dict:
    """Mark an agent as inactive in the marketplace."""
    db.supabase_admin.table("registry").update({"is_active": False}).eq("agent_did", did).execute()
    return {"status": "ok", "did": did, "is_active": False}


def activate_listing(did: str) -> dict:
    """Mark an agent as active in the marketplace."""
    db.supabase_admin.table("registry").update({"is_active": True}).eq("agent_did", did).execute()
    return {"status": "ok", "did": did, "is_active": True}


def get_listing(did: str) -> dict:
    """Get a single agent's marketplace listing."""
    res = db.supabase.table("registry").select("*").eq("agent_did", did).execute()
    return res.data[0] if res.data else None


def search_agents(tags: list = None, max_rate: float = None,
                  active_only: bool = True, limit: int = 20) -> list:
    """Find agents by skill tag overlap and optional rate filter."""
    query = db.supabase.table("registry").select("*")

    if active_only:
        query = query.eq("is_active", True)

    if max_rate is not None:
        query = query.lte("base_rate", max_rate)

    query = query.limit(limit)
    res = query.execute()

    if not res.data:
        return []

    # Filter by tag overlap in Python (Supabase REST doesn't do array overlap natively)
    if tags:
        tags_lower = [t.lower() for t in tags]
        filtered = []
        for agent in res.data:
            agent_tags = [t.lower() for t in (agent.get("skill_tags") or [])]
            overlap = set(tags_lower) & set(agent_tags)
            if overlap:
                agent["match_score"] = len(overlap)
                filtered.append(agent)
        filtered.sort(key=lambda x: x["match_score"], reverse=True)
        return filtered

    return res.data


def get_all_listings(active_only: bool = True, limit: int = 50) -> list:
    """Get all marketplace listings."""
    query = db.supabase.table("registry").select("*")
    if active_only:
        query = query.eq("is_active", True)
    query = query.order("updated_at", desc=True).limit(limit)
    res = query.execute()
    return res.data if res.data else []


def get_skill_tags() -> list:
    """Get all available skill tags from the ontology."""
    res = db.supabase.table("skill_tags").select("tag,category").order("category").execute()
    return res.data if res.data else []
