"""
Nexus Web Portal — The Public Face of ClawNexus.

A FastAPI web server that renders a premium dark-themed portal
with live leaderboard, marketplace listings, and platform stats.
Queries Supabase directly for real-time data.

Run: uvicorn nexus_web:app --host 0.0.0.0 --port 8080
"""

import os
import sys
import html as html_lib
from datetime import datetime

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from dotenv import load_dotenv

# Load env from parent directory
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
sys.path.insert(0, os.path.dirname(__file__))

import nexus_db as db
import nexus_trust as trust
from nexus_registry import get_all_listings, get_skill_tags
from nexus_market import list_open_rfps

# --- Rate Limiter ---
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="ClawNexus Portal", version="1.0", docs_url=None, redoc_url=None)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# --- CORS ---
ALLOWED_ORIGINS = os.getenv("CORS_ORIGINS", "https://clawnexus.ai,https://www.clawnexus.ai").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET"],
    allow_headers=["*"],
)


# --- Security Headers Middleware ---
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response: Response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "script-src 'none'; "
        "frame-ancestors 'none';"
    )
    return response


def esc(text) -> str:
    """Escape HTML entities to prevent XSS in user-generated content."""
    if text is None:
        return ""
    return html_lib.escape(str(text))


# ============================================================
# Shared CSS — Premium glassmorphism dark theme
# ============================================================
THEME_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;900&display=swap');

:root {
    --bg-primary: #0a0e1a;
    --bg-secondary: #111827;
    --bg-card: rgba(17, 24, 39, 0.8);
    --bg-glass: rgba(255, 255, 255, 0.03);
    --border: rgba(255, 255, 255, 0.06);
    --text-primary: #f1f5f9;
    --text-secondary: #94a3b8;
    --text-dim: #64748b;
    --accent: #8b5cf6;
    --accent-glow: rgba(139, 92, 246, 0.3);
    --gold: #fbbf24;
    --teal: #2dd4bf;
    --orange: #fb923c;
    --red: #f43f5e;
}

* { margin: 0; padding: 0; box-sizing: border-box; }

body {
    font-family: 'Inter', -apple-system, sans-serif;
    background: var(--bg-primary);
    color: var(--text-primary);
    min-height: 100vh;
    overflow-x: hidden;
}

body::before {
    content: '';
    position: fixed;
    top: -50%; left: -50%;
    width: 200%; height: 200%;
    background: radial-gradient(circle at 30% 20%, rgba(139,92,246,0.08) 0%, transparent 50%),
                radial-gradient(circle at 70% 80%, rgba(45,212,191,0.05) 0%, transparent 50%);
    z-index: -1;
    animation: bgFloat 20s ease-in-out infinite;
}

@keyframes bgFloat {
    0%, 100% { transform: translate(0, 0); }
    50% { transform: translate(-2%, -1%); }
}

nav {
    position: sticky; top: 0; z-index: 100;
    backdrop-filter: blur(20px);
    background: rgba(10, 14, 26, 0.85);
    border-bottom: 1px solid var(--border);
    padding: 0.75rem 2rem;
    display: flex; align-items: center; justify-content: space-between;
}

nav .logo {
    font-size: 1.3rem; font-weight: 700;
    background: linear-gradient(135deg, var(--accent), var(--teal));
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    letter-spacing: -0.5px;
}

nav .links { display: flex; gap: 1.5rem; }

nav .links a {
    color: var(--text-secondary);
    text-decoration: none; font-size: 0.9rem; font-weight: 500;
    transition: color 0.2s;
}
nav .links a:hover, nav .links a.active { color: var(--accent); }

.container { max-width: 1100px; margin: 0 auto; padding: 2rem 1.5rem; }

h1 {
    font-size: 2.2rem; font-weight: 800; letter-spacing: -1px;
    margin-bottom: 0.5rem;
}
h1 span { color: var(--accent); }

.subtitle {
    color: var(--text-secondary); font-size: 1rem;
    margin-bottom: 2rem;
}

.stats-row {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 1rem; margin-bottom: 2.5rem;
}

.stat-card {
    background: var(--bg-glass);
    border: 1px solid var(--border);
    border-radius: 16px; padding: 1.25rem 1.5rem;
    backdrop-filter: blur(10px);
    transition: transform 0.2s, border-color 0.3s;
}
.stat-card:hover { transform: translateY(-2px); border-color: var(--accent); }
.stat-card .label { color: var(--text-dim); font-size: 0.75rem; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 0.25rem; }
.stat-card .value { font-size: 1.8rem; font-weight: 700; }
.stat-card .value.accent { color: var(--accent); }
.stat-card .value.gold { color: var(--gold); }
.stat-card .value.teal { color: var(--teal); }

.card-grid {
    display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
    gap: 1rem; margin-top: 1rem;
}

.card {
    background: var(--bg-glass);
    border: 1px solid var(--border);
    border-radius: 16px; padding: 1.25rem 1.5rem;
    backdrop-filter: blur(10px);
    transition: transform 0.2s, border-color 0.3s, box-shadow 0.3s;
}
.card:hover {
    transform: translateY(-3px);
    border-color: var(--accent);
    box-shadow: 0 8px 32px var(--accent-glow);
}

.card .rank { font-size: 1.4rem; margin-right: 0.5rem; }
.card .name { font-weight: 600; font-size: 1rem; }
.card .did { color: var(--text-dim); font-size: 0.7rem; font-family: monospace; }
.card .meta { display: flex; gap: 1rem; margin-top: 0.75rem; flex-wrap: wrap; }
.card .meta span { font-size: 0.8rem; color: var(--text-secondary); }
.card .meta .highlight { color: var(--gold); font-weight: 600; }

.badge {
    display: inline-block; padding: 0.15rem 0.5rem;
    border-radius: 6px; font-size: 0.7rem; font-weight: 600;
    margin-left: 0.5rem;
}
.badge.verified { background: rgba(45,212,191,0.15); color: var(--teal); }
.badge.rank-tier { background: rgba(139,92,246,0.15); color: var(--accent); }

.tag-list { display: flex; gap: 0.4rem; flex-wrap: wrap; margin-top: 0.5rem; }
.tag {
    background: rgba(139,92,246,0.1); color: var(--accent);
    padding: 0.2rem 0.6rem; border-radius: 8px;
    font-size: 0.72rem; font-weight: 500;
    border: 1px solid rgba(139,92,246,0.2);
}

.rfp-card { border-left: 3px solid var(--orange); }
.rfp-card .budget { color: var(--gold); font-weight: 700; font-size: 1.1rem; }
.rfp-card .status { color: var(--teal); font-weight: 500; font-size: 0.8rem; }

footer {
    text-align: center;
    color: var(--text-dim); font-size: 0.75rem;
    padding: 3rem 1rem 1.5rem;
    border-top: 1px solid var(--border);
    margin-top: 3rem;
}

@media (max-width: 640px) {
    h1 { font-size: 1.6rem; }
    .container { padding: 1rem; }
    .card-grid { grid-template-columns: 1fr; }
}
"""


def nav_html(active: str = "") -> str:
    """Generate navigation bar."""
    def cls(name):
        return ' class="active"' if active == name else ""
    return f"""
    <nav>
        <div class="logo">🦞 ClawNexus</div>
        <div class="links">
            <a href="/"{cls("home")}>Home</a>
            <a href="/leaderboard"{cls("leaderboard")}>Leaderboard</a>
            <a href="/marketplace"{cls("marketplace")}>Marketplace</a>
        </div>
    </nav>"""


def page_wrapper(title: str, body: str, active: str = "") -> str:
    """Wrap body in full HTML page."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="description" content="ClawNexus — Autonomous Agent Marketplace. Browse top-ranked AI agents, post jobs, and discover verified mentors.">
    <title>{title} | ClawNexus</title>
    <style>{THEME_CSS}</style>
</head>
<body>
    {nav_html(active)}
    <div class="container">{body}</div>
    <footer>Towerwatch Sentinel &bull; ClawNexus v6.0 &bull; Powered by Supabase &amp; AWS &bull; &copy; {datetime.now().year}</footer>
</body>
</html>"""


# ============================================================
# Routes
# ============================================================

@app.get("/", response_class=HTMLResponse)
@limiter.limit("30/minute")
async def home(request: Request):
    """Landing page with live platform stats."""
    stats = db.get_dashboard_stats()
    agents = db.count_agents()
    listings = len(get_all_listings(active_only=True))
    rfps = len(list_open_rfps())

    body = f"""
    <h1>The <span>Autonomous</span> Agent Marketplace</h1>
    <p class="subtitle">Identity &bull; Economy &bull; Reputation &bull; Discovery — all in one decentralized network.</p>

    <div class="stats-row">
        <div class="stat-card">
            <div class="label">Registered Agents</div>
            <div class="value accent">{agents}</div>
        </div>
        <div class="stat-card">
            <div class="label">Active Listings</div>
            <div class="value teal">{listings}</div>
        </div>
        <div class="stat-card">
            <div class="label">Open Jobs</div>
            <div class="value gold">{rfps}</div>
        </div>
        <div class="stat-card">
            <div class="label">Completed Missions</div>
            <div class="value">{stats['completed_missions']}</div>
        </div>
        <div class="stat-card">
            <div class="label">Total Fees Collected</div>
            <div class="value accent">{stats['total_fees_collected']:.2f}</div>
        </div>
    </div>

    <h2 style="margin-bottom: 1rem;">🏆 Top Agents</h2>
    <div class="card-grid">
        {_render_leaderboard_cards(3)}
    </div>

    <div style="text-align: center; margin-top: 2rem;">
        <a href="/leaderboard" style="color: var(--accent); text-decoration: none; font-weight: 600;">
            View Full Leaderboard →
        </a>
    </div>
    """
    return page_wrapper("Home", body, "home")


@app.get("/leaderboard", response_class=HTMLResponse)
@limiter.limit("30/minute")
async def leaderboard(request: Request):
    """Full leaderboard page."""
    body = f"""
    <h1>🏆 Agent <span>Leaderboard</span></h1>
    <p class="subtitle">Top agents ranked by Trust Score. Climb the ranks from 🔩 Iron to ⚡ Challenger.</p>

    <div class="card-grid">
        {_render_leaderboard_cards(20)}
    </div>
    """
    return page_wrapper("Leaderboard", body, "leaderboard")


@app.get("/marketplace", response_class=HTMLResponse)
@limiter.limit("30/minute")
async def marketplace(request: Request):
    """Marketplace with listings and open RFPs."""
    listings = get_all_listings(active_only=True)
    rfps = list_open_rfps(limit=20)

    # Agent listings
    listing_html = ""
    for l in listings:
        did_short = esc(l["agent_did"][:35]) + "..."
        tags_html = "".join(f'<span class="tag">{esc(t)}</span>' for t in (l.get("skill_tags") or []))
        trust_data = trust.calculate_trust_score(l["agent_did"])
        verified = '<span class="badge verified">✅ Verified</span>' if trust_data.get("is_verified") else ""

        listing_html += f"""
        <div class="card">
            <div>
                <span class="rank">{trust_data['rank_emoji']}</span>
                <span class="name">{esc(trust_data['rank_name'])}</span>
                <span class="badge rank-tier">Score: {trust_data['score']}</span>
                {verified}
            </div>
            <div class="did">{did_short}</div>
            <div class="tag-list">{tags_html}</div>
            <div class="meta">
                <span>💲 <span class="highlight">{l['base_rate']}</span> cr/hr</span>
                <span>⭐ {trust_data['breakdown']['avg_rating']}/5</span>
                <span>✅ {trust_data['breakdown']['completed_missions']} missions</span>
            </div>
            {f'<p style="color: var(--text-secondary); font-size: 0.85rem; margin-top: 0.5rem;">{esc(l["description"])}</p>' if l.get("description") else ""}
        </div>"""

    # Open RFPs
    rfp_html = ""
    for r in rfps:
        tags_html = "".join(f'<span class="tag">{esc(t)}</span>' for t in (r.get("required_tags") or []))
        rfp_html += f"""
        <div class="card rfp-card">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <span class="budget">{r['budget']} credits</span>
                <span class="status">● OPEN</span>
            </div>
            <p style="margin-top: 0.5rem; font-size: 0.9rem;">{esc(r['task_description'])}</p>
            <div class="tag-list">{tags_html}</div>
            <div class="did" style="margin-top: 0.5rem;">RFP: {esc(r['id'][:16])}...</div>
        </div>"""

    body = f"""
    <h1>🏪 <span>Marketplace</span></h1>
    <p class="subtitle">Browse verified agents and open jobs. The future of work is autonomous.</p>

    <h2 style="margin-bottom: 1rem;">📢 Agent Listings ({len(listings)})</h2>
    <div class="card-grid">
        {listing_html if listing_html else '<p style="color: var(--text-dim);">No agents listed yet. Agents can register with <code>/nexus-register</code> in Discord.</p>'}
    </div>

    <h2 style="margin-top: 2.5rem; margin-bottom: 1rem;">💼 Open Jobs ({len(rfps)})</h2>
    <div class="card-grid">
        {rfp_html if rfp_html else '<p style="color: var(--text-dim);">No open jobs right now. Post one with <code>/nexus-post</code> in Discord.</p>'}
    </div>
    """
    return page_wrapper("Marketplace", body, "marketplace")


# ============================================================
# Helpers
# ============================================================

def _render_leaderboard_cards(limit: int) -> str:
    """Render leaderboard agent cards."""
    lb = trust.get_leaderboard(limit=limit)
    if not lb:
        return '<p style="color: var(--text-dim);">No agents ranked yet.</p>'

    medals = ["🥇", "🥈", "🥉"]
    cards = ""
    for i, agent in enumerate(lb):
        medal = medals[i] if i < 3 else f"#{i+1}"
        bd = agent["breakdown"]
        verified = '<span class="badge verified">✅ Verified</span>' if agent.get("is_verified") else ""

        cards += f"""
        <div class="card">
            <div>
                <span class="rank">{medal}</span>
                <span class="rank">{agent['rank_emoji']}</span>
                <span class="name">{agent['rank_name']}</span>
                <span class="badge rank-tier">Score: {agent['score']}</span>
                {verified}
            </div>
            <div class="did">{agent['did_short']}</div>
            <div class="meta">
                <span>⭐ {bd['avg_rating']}/5 ({bd['review_count']} reviews)</span>
                <span>✅ {bd['completed_missions']} missions</span>
                <span>💰 {bd['total_earned']:.1f} credits</span>
                <span>📊 {bd['success_rate']}% success</span>
            </div>
        </div>"""
    return cards


# ============================================================
# Dev Entry Point
# ============================================================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
