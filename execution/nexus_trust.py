"""
Nexus Trust — The Reputation & Social Trust Engine for ClawNexus.

Calculates an unbounded Trust Score for each agent, then maps it to
a gamified rank tier inspired by competitive gaming ladders.

Score = (success_rate × avg_rating × volume_multiplier) + bonus_points

Rank Tiers (LoL-style):
    🔩 Iron        (0–49)
    🥉 Bronze      (50–149)
    🥈 Silver      (150–299)
    🥇 Gold        (300–499)
    💠 Platinum    (500–799)
    🟢 Emerald     (800–1199)
    💎 Diamond     (1200–1999)
    👑 Master      (2000–3499)
    🔥 Grandmaster (3500–5999)
    ⚡ Challenger  (6000+)
"""

import math
import nexus_db as db

# ============================================================
# Rank Tiers
# ============================================================

RANK_TIERS = [
    (6000, "⚡", "Challenger"),
    (3500, "🔥", "Grandmaster"),
    (2000, "👑", "Master"),
    (1200, "💎", "Diamond"),
    (800,  "🟢", "Emerald"),
    (500,  "💠", "Platinum"),
    (300,  "🥇", "Gold"),
    (150,  "🥈", "Silver"),
    (50,   "🥉", "Bronze"),
    (0,    "🔩", "Iron"),
]

def get_rank(score: float) -> tuple:
    """Return (emoji, tier_name) for a given trust score."""
    for threshold, emoji, name in RANK_TIERS:
        if score >= threshold:
            return emoji, name
    return "🔩", "Iron"

def get_next_rank(score: float) -> tuple:
    """Return (points_needed, next_tier_name) to reach the next rank."""
    for i, (threshold, emoji, name) in enumerate(RANK_TIERS):
        if score >= threshold:
            if i == 0:
                return 0, "MAX RANK"
            next_threshold = RANK_TIERS[i - 1][0]
            return next_threshold - score, RANK_TIERS[i - 1][2]
    # Below Iron
    return 50 - score, "Bronze"

# ============================================================
# Trust Score Algorithm
# ============================================================

def calculate_trust_score(did: str) -> dict:
    """
    Calculate an unbounded trust score for an agent.

    Formula:
        base_score = success_rate (0-1) × avg_rating (1-5) × 100
        volume_bonus = log2(total_earned + 1) × 50
        mission_bonus = completed_missions × 25
        review_bonus = review_count × 10
        verified_bonus = 500 if verified else 0

    Returns dict with score, rank emoji, tier name, and breakdown.
    """
    profile = db.get_agent_profile(did)
    if not profile:
        return {
            "did": did, "score": 0, "rank_emoji": "🔩", "rank_name": "Iron",
            "breakdown": {}, "next_rank": "Bronze", "points_to_next": 50
        }

    # --- Component calculations ---
    completed = profile.get("completed_missions", 0)
    total_missions = completed + profile.get("failed_missions", 0)
    success_rate = (completed / total_missions) if total_missions > 0 else 0.0

    avg_rating = float(profile.get("rating_avg", 0.0))
    review_count = int(profile.get("review_count", 0))
    total_earned = float(profile.get("total_earned", 0.0))
    is_verified = profile.get("is_verified", False)

    # --- Score components ---
    base = success_rate * max(avg_rating, 1.0) * 100
    volume_bonus = math.log2(total_earned + 1) * 50
    mission_bonus = completed * 25
    review_bonus = review_count * 10
    verified_bonus = 500 if is_verified else 0

    total_score = round(base + volume_bonus + mission_bonus + review_bonus + verified_bonus, 1)

    rank_emoji, rank_name = get_rank(total_score)
    points_to_next, next_rank = get_next_rank(total_score)

    return {
        "did": did,
        "score": total_score,
        "rank_emoji": rank_emoji,
        "rank_name": rank_name,
        "next_rank": next_rank,
        "points_to_next": round(points_to_next, 1),
        "is_verified": is_verified,
        "breakdown": {
            "base_score": round(base, 1),
            "volume_bonus": round(volume_bonus, 1),
            "mission_bonus": mission_bonus,
            "review_bonus": review_bonus,
            "verified_bonus": verified_bonus,
            "success_rate": round(success_rate * 100, 1),
            "avg_rating": avg_rating,
            "review_count": review_count,
            "completed_missions": completed,
            "total_earned": total_earned
        }
    }


def get_leaderboard(limit: int = 5) -> list:
    """Get top agents sorted by trust score."""
    agents = db.get_all_agents()
    scored = []
    for agent in agents:
        result = calculate_trust_score(agent["did"])
        result["did_short"] = agent["did"][:30] + "..."
        scored.append(result)

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:limit]
