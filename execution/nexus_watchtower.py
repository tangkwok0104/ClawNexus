"""
ClawNexus Watchtower — The Discord Bot for Human-in-the-Loop Authority.

Polls the NexusRelay for incoming Mission Proposals, verifies signatures,
displays rich embeds with Approve/Reject buttons, and manages escrow.

Run: python nexus_watchtower.py
Requires: DISCORD_BOT_TOKEN, DISCORD_CHANNEL_ID, DISCORD_OWNER_ID in .env
"""

import asyncio
import json
import os
import uuid
import logging

import discord
from discord.ext import tasks, commands
from discord import app_commands, ui
from dotenv import load_dotenv
import aiohttp

from clawnexus_identity import DID_PREFIX, generate_keypair, sign_payload, verify_payload
from nexus_vault import lock_escrow, get_balance, deposit, release_escrow, refund_escrow, get_platform_balance, complete_mission
import nexus_db as db
import nexus_trust as trust
import nexus_registry as registry
import nexus_market as market

# --- Load Config ---
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
DISCORD_OWNER_ID = int(os.getenv("DISCORD_OWNER_ID", "0"))
RELAY_URL = os.getenv("RELAY_URL", "http://localhost:8377")
RELAY_AUTH_TOKEN = os.getenv("RELAY_AUTH_TOKEN", "")

# Watchtower's own identity (auto-generated on first run)
WATCHTOWER_KEYS_FILE = os.path.join(os.path.dirname(__file__), "..", ".watchtower_keys.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [Watchtower] %(levelname)s %(message)s"
)
log = logging.getLogger("Watchtower")


def get_watchtower_identity() -> dict:
    """Load or generate the Watchtower's own ClawID."""
    if os.path.exists(WATCHTOWER_KEYS_FILE):
        with open(WATCHTOWER_KEYS_FILE) as f:
            return json.load(f)
    priv, pub, did = generate_keypair()
    keys = {"private_key": priv, "public_key": pub, "did": did}
    with open(WATCHTOWER_KEYS_FILE, "w") as f:
        json.dump(keys, f, indent=2)
    log.info(f"Generated Watchtower identity: {did[:40]}...")
    return keys


# ============================================================
# Discord UI: Approve / Reject Buttons
# ============================================================
class MissionApprovalView(ui.View):
    """Interactive buttons for approving or rejecting a mission."""

    def __init__(self, mission_data: dict, signature: str, sender_pubkey: str, watchtower_keys: dict):
        super().__init__(timeout=300)  # 5 min timeout
        self.mission_data = mission_data
        self.signature = signature
        self.sender_pubkey = sender_pubkey
        self.wt_keys = watchtower_keys
        self.decided = False

    def _extract_mission_id(self) -> str:
        return self.mission_data.get("message_id", str(uuid.uuid4())[:8])

    def _extract_amount(self) -> float:
        payload = self.mission_data.get("payload", {})
        econ = payload.get("economics", {})
        return econ.get("amount", econ.get("bid_amount", 0.0))

    @ui.button(label="Approve", style=discord.ButtonStyle.green, emoji="✅")
    async def approve_button(self, interaction: discord.Interaction, button: ui.Button):
        # Owner-only check
        if interaction.user.id != DISCORD_OWNER_ID:
            await interaction.response.send_message("Only the owner can approve missions.", ephemeral=True)
            return
        if self.decided:
            await interaction.response.send_message("Already decided.", ephemeral=True)
            return
        self.decided = True

        mission_id = self._extract_mission_id()
        amount = self._extract_amount()
        sender_did = self.mission_data.get("sender_did", "")

        # Lock escrow (2% commission auto-collected)
        result = lock_escrow(mission_id, sender_did, amount)

        if result["status"] == "error":
            embed = discord.Embed(
                title="Escrow Failed",
                description=f"**Reason:** {result['reason']}\nBalance: {result.get('balance', 'N/A')} credits",
                color=discord.Color.orange()
            )
            await interaction.response.edit_message(embed=embed, view=None)
            return

        # Send APPROVAL_GRANTED back to relay
        approval_msg = {
            "protocol_version": "1.0-ClawNexus",
            "message_id": str(uuid.uuid4()),
            "sender_did": self.wt_keys["did"],
            "receiver_did": sender_did,
            "payload": {
                "type": "APPROVAL_GRANTED",
                "mission_id": mission_id,
                "escrow": result
            }
        }
        sig = sign_payload(approval_msg, self.wt_keys["private_key"])
        approval_msg["signature"] = sig

        # POST to relay
        headers = {"Content-Type": "application/json"}
        if RELAY_AUTH_TOKEN:
            headers["Authorization"] = f"Bearer {RELAY_AUTH_TOKEN}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{RELAY_URL}/send", json=approval_msg, headers=headers) as resp:
                    log.info(f"Approval sent to relay: {await resp.json()}")
        except Exception as e:
            log.error(f"Failed to send approval: {e}")

        # Update the Discord message
        embed = discord.Embed(
            title="✅ Mission Approved",
            description=f"**{self.mission_data.get('payload', {}).get('mission_details', {}).get('title', 'Unknown')}**\n\n"
                        f"💰 **{amount}** credits locked in escrow\n"
                        f"🏦 Platform commission: **{result['commission']}** credits\n"
                        f"📋 Mission ID: `{mission_id[:12]}...`",
            color=discord.Color.green()
        )
        await interaction.response.edit_message(embed=embed, view=None)

    @ui.button(label="Reject", style=discord.ButtonStyle.red, emoji="❌")
    async def reject_button(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != DISCORD_OWNER_ID:
            await interaction.response.send_message("Only the owner can reject missions.", ephemeral=True)
            return
        if self.decided:
            await interaction.response.send_message("Already decided.", ephemeral=True)
            return
        self.decided = True

        sender_did = self.mission_data.get("sender_did", "")
        mission_id = self._extract_mission_id()

        # Send MISSION_REJECTED back to relay
        reject_msg = {
            "protocol_version": "1.0-ClawNexus",
            "message_id": str(uuid.uuid4()),
            "sender_did": self.wt_keys["did"],
            "receiver_did": sender_did,
            "payload": {"type": "MISSION_REJECTED", "mission_id": mission_id}
        }
        sig = sign_payload(reject_msg, self.wt_keys["private_key"])
        reject_msg["signature"] = sig

        headers = {"Content-Type": "application/json"}
        if RELAY_AUTH_TOKEN:
            headers["Authorization"] = f"Bearer {RELAY_AUTH_TOKEN}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{RELAY_URL}/send", json=reject_msg, headers=headers) as resp:
                    log.info(f"Rejection sent to relay: {await resp.json()}")
        except Exception as e:
            log.error(f"Failed to send rejection: {e}")

        embed = discord.Embed(
            title="❌ Mission Rejected",
            description=f"Mission `{mission_id[:12]}...` was rejected by the owner.",
            color=discord.Color.red()
        )
        await interaction.response.edit_message(embed=embed, view=None)


# ============================================================
# Discord Bot
# ============================================================
class WatchtowerBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.wt_keys = get_watchtower_identity()
        self.channel = None

    async def setup_hook(self):
        self.poll_relay.start()
        for cmd in [nexus_stats, nexus_top, nexus_profile, nexus_verify, nexus_help,
                     nexus_register, nexus_post, nexus_market_cmd]:
            self.tree.add_command(cmd)
        try:
            synced = await self.tree.sync()
            log.info(f"Synced {len(synced)} slash command(s).")
        except Exception as e:
            log.error(f"Failed to sync commands: {e}")

    async def on_ready(self):
        try:
            self.channel = await self.fetch_channel(DISCORD_CHANNEL_ID)
        except Exception as e:
            log.error(f"Failed to fetch channel {DISCORD_CHANNEL_ID}: {e}")
            self.channel = None
        log.info(f"Watchtower online as {self.user}")
        log.info(f"Watchtower DID: {self.wt_keys['did'][:40]}...")
        log.info(f"Monitoring channel: {self.channel}")

        if self.channel:
            embed = discord.Embed(
                title="🦞 ClawNexus Watchtower Online",
                description=f"Monitoring relay at `{RELAY_URL}`\nWatchtower DID: `{self.wt_keys['did'][:30]}...`",
                color=discord.Color.blue()
            )
            platform = get_platform_balance()
            embed.add_field(name="Platform Balance", value=f"{platform['balance']} credits")
            embed.add_field(name="Total Earned", value=f"{platform['total_earned']} credits")
            await self.channel.send(embed=embed)

    @tasks.loop(seconds=1)
    async def poll_relay(self):
        """Continuously long-poll the relay for missions addressed to the Watchtower."""
        if not self.channel:
            return

        headers = {}
        if RELAY_AUTH_TOKEN:
            headers["Authorization"] = f"Bearer {RELAY_AUTH_TOKEN}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{RELAY_URL}/poll",
                    params={"did": self.wt_keys["did"], "wait": "30"},
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=35)
                ) as resp:
                    if resp.status == 204:
                        return  # No messages

                    message = await resp.json()

                    # --- Verify Signature ---
                    signature = message.pop("signature", "")
                    sender_did = message.get("sender_did", "")

                    if not sender_did.startswith(DID_PREFIX):
                        log.warning(f"Invalid sender DID: {sender_did}")
                        return

                    sender_pubkey = sender_did[len(DID_PREFIX):]
                    is_valid = verify_payload(message, signature, sender_pubkey)

                    if not is_valid:
                        log.warning("REJECTED: Invalid signature on incoming mission.")
                        await self.channel.send("⚠️ **SECURITY ALERT**: Received a mission with an invalid signature. Blocked.")
                        return

                    # --- Route by message type ---
                    payload = message.get("payload", {})
                    msg_type = payload.get("type", "")

                    if msg_type == "MISSION_COMPLETE":
                        await self._handle_mission_complete(message, payload, sender_did)
                        return

                    if msg_type == "AGENT_ADVERTISE":
                        await self._handle_agent_advertise(payload, sender_did)
                        return

                    # --- Build Mission Proposal Embed ---
                    mission_details = payload.get("mission_details", payload.get("content", {}))
                    economics = payload.get("economics", {})

                    title = mission_details.get("title", mission_details.get("mission_title", "Unknown Mission"))
                    description = mission_details.get("description", "No description provided.")
                    amount = economics.get("amount", economics.get("bid_amount", 0.0))
                    currency = economics.get("currency", "CREDITS")
                    human_approval = payload.get("human_approval_required",
                                                 payload.get("safety", {}).get("requires_human_approval", False))

                    embed = discord.Embed(
                        title=f"📋 Mission Proposal: {title}",
                        description=description,
                        color=discord.Color.gold()
                    )
                    embed.add_field(name="💰 Cost", value=f"**{amount}** {currency}", inline=True)
                    embed.add_field(name="🔐 Escrow", value=f"{'Required' if economics.get('escrow_flag', economics.get('escrow_required', False)) else 'None'}", inline=True)
                    embed.add_field(name="👤 Human Approval", value=f"{'Yes' if human_approval else 'No'}", inline=True)
                    embed.add_field(name="📤 From", value=f"`{sender_did[:30]}...`", inline=False)
                    embed.set_footer(text=f"Mission ID: {message.get('message_id', 'N/A')} | Signature: Verified ✅")

                    # Send with interactive buttons
                    view = MissionApprovalView(message, signature, sender_pubkey, self.wt_keys)
                    await self.channel.send(embed=embed, view=view)
                    log.info(f"Mission displayed: {title}")

        except asyncio.TimeoutError:
            pass  # Normal long-poll timeout
        except Exception as e:
            log.error(f"Poll error: {e}")
            await asyncio.sleep(5)

    async def _handle_mission_complete(self, message: dict, payload: dict, sender_did: str):
        """Handle MISSION_COMPLETE: release escrow to the mentor."""
        mission_id = payload.get("mission_id", "")
        mentor_did = payload.get("mentor_did", sender_did)

        result = complete_mission(mission_id, mentor_did)

        if result["status"] == "ok":
            embed = discord.Embed(
                title="🎓 Mission Completed!",
                description=f"**Funds released to mentor.**\n\n"
                            f"💰 **{result['amount']}** credits paid to mentor\n"
                            f"📋 Mission ID: `{mission_id[:12]}...`\n"
                            f"👤 Mentor: `{mentor_did[:30]}...`",
                color=discord.Color.purple()
            )
            platform = get_platform_balance()
            embed.add_field(name="🏦 Platform Treasury", value=f"{platform['balance']} credits")
            await self.channel.send(embed=embed)
            log.info(f"Mission completed: {mission_id[:12]}... → funds released")

            # --- Send rating DM to owner ---
            try:
                owner = await self.fetch_user(DISCORD_OWNER_ID)
                if owner:
                    rating_embed = discord.Embed(
                        title="⭐ Rate This Mission",
                        description=f"Mission `{mission_id[:12]}...` is complete!\n"
                                    f"How was **{mentor_did[:30]}...**'s service?",
                        color=discord.Color.gold()
                    )
                    view = MissionRatingView(mission_id, sender_did, mentor_did)
                    await owner.send(embed=rating_embed, view=view)
                    log.info(f"Rating DM sent for mission {mission_id[:12]}...")
            except Exception as e:
                log.warning(f"Could not send rating DM: {e}")
        else:
            embed = discord.Embed(
                title="⚠️ Completion Failed",
                description=f"**Reason:** {result.get('reason', 'Unknown')}\nMission: `{mission_id[:12]}...`",
                color=discord.Color.orange()
            )
            await self.channel.send(embed=embed)
            log.warning(f"Mission completion failed: {result.get('reason')}")

    async def _handle_agent_advertise(self, payload: dict, sender_did: str):
        """Handle AGENT_ADVERTISE: register agent's skills in marketplace."""
        skills = payload.get("skill_tags", [])
        description = payload.get("description", "")
        base_rate = payload.get("base_rate", 0.0)

        result = registry.register_agent(sender_did, skills, description, base_rate)

        if self.channel:
            embed = discord.Embed(
                title="📢 Agent Listing Updated",
                description=f"`{sender_did[:30]}...` is now in the marketplace!",
                color=discord.Color.teal()
            )
            embed.add_field(name="🎯 Skills", value=", ".join(skills) or "None", inline=True)
            embed.add_field(name="💲 Rate", value=f"{base_rate} credits/hr", inline=True)
            await self.channel.send(embed=embed)
        log.info(f"Agent registered: {sender_did[:20]}... with skills {skills}")

    @poll_relay.before_loop
    async def before_poll(self):
        await self.wait_until_ready()


# ============================================================
# Slash Command: /nexus-stats (Founder's Dashboard)
# ============================================================
@app_commands.command(name="nexus-stats", description="📊 Founder's Dashboard — ClawNexus platform statistics.")
async def nexus_stats(interaction: discord.Interaction):
    """Owner-only command that queries Supabase for platform stats."""
    # Owner gate
    if interaction.user.id != DISCORD_OWNER_ID:
        await interaction.response.send_message(
            "🔒 This command is restricted to the platform owner.", ephemeral=True
        )
        return

    await interaction.response.defer()

    try:
        stats = db.get_dashboard_stats()

        embed = discord.Embed(
            title="📊 ClawNexus Founder's Dashboard",
            description="Real-time platform economics from Supabase.",
            color=discord.Color.from_rgb(138, 43, 226)  # Royal Purple
        )
        embed.add_field(
            name="🏦 Treasury Balance",
            value=f"**{stats['treasury_balance']:.2f}** credits",
            inline=True
        )
        embed.add_field(
            name="💰 Total Fees Collected",
            value=f"**{stats['total_fees_collected']:.2f}** credits",
            inline=True
        )
        embed.add_field(
            name="\u200b",  # Spacer
            value="\u200b",
            inline=True
        )
        embed.add_field(
            name="📋 Active Missions",
            value=f"**{stats['active_missions']}** in escrow",
            inline=True
        )
        embed.add_field(
            name="✅ Completed Missions",
            value=f"**{stats['completed_missions']}** delivered",
            inline=True
        )
        embed.add_field(
            name="👥 Registered Agents",
            value=f"**{stats['total_agents']}** DIDs",
            inline=True
        )
        embed.set_footer(text="Towerwatch Sentinel • ClawNexus Economic Engine")
        embed.timestamp = discord.utils.utcnow()

        await interaction.followup.send(embed=embed)
        log.info(f"Dashboard stats served to owner {interaction.user}")

    except Exception as e:
        log.error(f"Dashboard error: {e}")
        await interaction.followup.send(f"⚠️ Error fetching stats: `{e}`")


# ============================================================
# Discord UI: Star Rating Buttons (DM after mission complete)
# ============================================================
class MissionRatingView(ui.View):
    """1-5 star rating buttons sent via DM after mission payout."""

    def __init__(self, mission_id: str, reviewer_did: str, agent_did: str):
        super().__init__(timeout=600)  # 10 min timeout
        self.mission_id = mission_id
        self.reviewer_did = reviewer_did
        self.agent_did = agent_did
        self.rated = False

        # Dynamically add star buttons
        for i in range(1, 6):
            button = ui.Button(
                label="⭐" * i,
                style=discord.ButtonStyle.secondary if i < 4 else discord.ButtonStyle.primary,
                custom_id=f"rate_{mission_id}_{i}",
                row=0
            )
            button.callback = self._make_callback(i)
            self.add_item(button)

    def _make_callback(self, stars: int):
        async def callback(interaction: discord.Interaction):
            if self.rated:
                await interaction.response.send_message("Already rated!", ephemeral=True)
                return
            self.rated = True

            db.insert_review(self.mission_id, self.reviewer_did, self.agent_did, stars)
            trust_data = trust.calculate_trust_score(self.agent_did)

            embed = discord.Embed(
                title=f"{'⭐' * stars} Rating Submitted!",
                description=f"You rated `{self.agent_did[:30]}...` **{stars}/5** stars.\n\n"
                            f"{trust_data['rank_emoji']} **{trust_data['rank_name']}** — Score: **{trust_data['score']}**\n"
                            f"📈 Next: {trust_data['points_to_next']} pts to {trust_data['next_rank']}",
                color=discord.Color.green()
            )
            await interaction.response.edit_message(embed=embed, view=None)
        return callback


# ============================================================
# Slash Command: /nexus-top (Public Leaderboard)
# ============================================================
@app_commands.command(name="nexus-top", description="🏆 Top agents by trust score.")
async def nexus_top(interaction: discord.Interaction):
    """Public leaderboard — Top 5 agents by reputation."""
    await interaction.response.defer()

    try:
        leaderboard = trust.get_leaderboard(limit=5)

        if not leaderboard:
            await interaction.followup.send("No agents registered yet!")
            return

        embed = discord.Embed(
            title="🏆 ClawNexus Leaderboard",
            description="Top agents ranked by Trust Score.",
            color=discord.Color.from_rgb(255, 215, 0)  # Gold
        )

        for i, agent in enumerate(leaderboard):
            verified = " ✅" if agent.get("is_verified") else ""
            bd = agent["breakdown"]
            embed.add_field(
                name=f"{['🥇','🥈','🥉','4️⃣','5️⃣'][i]} {agent['rank_emoji']} {agent['rank_name']}{verified}",
                value=f"Score: **{agent['score']}** · ⭐ {bd['avg_rating']}/5 · "
                      f"{bd['completed_missions']} missions · {bd['total_earned']:.1f} credits\n"
                      f"`{agent['did_short']}`",
                inline=False
            )

        embed.set_footer(text="Towerwatch Sentinel • Nexus Reputation Engine")
        embed.timestamp = discord.utils.utcnow()
        await interaction.followup.send(embed=embed)

    except Exception as e:
        log.error(f"Leaderboard error: {e}")
        await interaction.followup.send(f"⚠️ Error: `{e}`")


# ============================================================
# Slash Command: /nexus-profile (Agent Reputation Card)
# ============================================================
@app_commands.command(name="nexus-profile", description="👤 View an agent's reputation card.")
@app_commands.describe(agent_did="The agent's DID (or partial DID)")
async def nexus_profile(interaction: discord.Interaction, agent_did: str):
    """Public command — view any agent's trust profile."""
    await interaction.response.defer()

    try:
        result = trust.calculate_trust_score(agent_did)
        bd = result["breakdown"]
        verified = " ✅ Verified" if result.get("is_verified") else ""

        embed = discord.Embed(
            title=f"{result['rank_emoji']} Agent Profile{verified}",
            description=f"`{agent_did[:40]}...`",
            color=discord.Color.from_rgb(138, 43, 226)
        )
        embed.add_field(name="🏅 Trust Score", value=f"**{result['score']}**", inline=True)
        embed.add_field(name="🎖️ Rank", value=f"{result['rank_emoji']} {result['rank_name']}", inline=True)
        embed.add_field(name="📈 Next Rank", value=f"{result['points_to_next']} pts → {result['next_rank']}", inline=True)
        embed.add_field(name="⭐ Avg Rating", value=f"{bd['avg_rating']}/5 ({bd['review_count']} reviews)", inline=True)
        embed.add_field(name="✅ Completed", value=f"{bd['completed_missions']} missions", inline=True)
        embed.add_field(name="📊 Success Rate", value=f"{bd['success_rate']}%", inline=True)
        embed.add_field(name="💰 Total Earned", value=f"{bd['total_earned']:.2f} credits", inline=True)
        embed.set_footer(text="Towerwatch Sentinel • Nexus Reputation Engine")
        embed.timestamp = discord.utils.utcnow()

        await interaction.followup.send(embed=embed)

    except Exception as e:
        log.error(f"Profile error: {e}")
        await interaction.followup.send(f"⚠️ Error: `{e}`")


# ============================================================
# Slash Command: /nexus-verify (Owner-Only Badge Toggle)
# ============================================================
@app_commands.command(name="nexus-verify", description="✅ Toggle verification badge on an agent (Owner only).")
@app_commands.describe(agent_did="The agent's DID to verify/unverify")
async def nexus_verify(interaction: discord.Interaction, agent_did: str):
    """Owner-only — toggle verification badge."""
    if interaction.user.id != DISCORD_OWNER_ID:
        await interaction.response.send_message("🔒 Owner only.", ephemeral=True)
        return

    await interaction.response.defer()

    try:
        profile = db.get_agent_profile(agent_did)
        if not profile:
            await interaction.followup.send(f"Agent `{agent_did[:30]}...` not found.")
            return

        new_status = not profile.get("is_verified", False)
        db.set_agent_verified(agent_did, new_status)

        emoji = "✅" if new_status else "❌"
        status = "VERIFIED" if new_status else "UNVERIFIED"

        embed = discord.Embed(
            title=f"{emoji} Agent {status}",
            description=f"`{agent_did[:40]}...`\nVerification badge **{'granted' if new_status else 'revoked'}**.",
            color=discord.Color.green() if new_status else discord.Color.red()
        )
        await interaction.followup.send(embed=embed)

    except Exception as e:
        log.error(f"Verify error: {e}")
        await interaction.followup.send(f"⚠️ Error: `{e}`")


# ============================================================
# Slash Command: /nexus-help (Command Directory)
# ============================================================
@app_commands.command(name="nexus-help", description="📖 List all ClawNexus commands.")
async def nexus_help(interaction: discord.Interaction):
    """Public — list all available Watchtower commands."""
    embed = discord.Embed(
        title="📖 ClawNexus Command Directory",
        description="All available Towerwatch Sentinel commands.",
        color=discord.Color.from_rgb(0, 191, 255)  # Deep Sky Blue
    )
    embed.add_field(
        name="🏆 `/nexus-top`",
        value="View the Top 5 agents by Trust Score.",
        inline=False
    )
    embed.add_field(
        name="👤 `/nexus-profile <agent_did>`",
        value="View an agent's full reputation card.",
        inline=False
    )
    embed.add_field(
        name="📊 `/nexus-stats`",
        value="🔒 *Owner only* — Platform economics dashboard.",
        inline=False
    )
    embed.add_field(
        name="✅ `/nexus-verify <agent_did>`",
        value="🔒 *Owner only* — Toggle verification badge.",
        inline=False
    )
    embed.add_field(
        name="📖 `/nexus-help`",
        value="Show this command list.",
        inline=False
    )
    embed.set_footer(text="Towerwatch Sentinel • ClawNexus v5.0")
    embed.timestamp = discord.utils.utcnow()

    await interaction.response.send_message(embed=embed)


# ============================================================
# Slash Command: /nexus-register (Agent Marketplace Registration)
# ============================================================
@app_commands.command(name="nexus-register", description="📢 Register your agent in the marketplace.")
@app_commands.describe(
    skills="Comma-separated skill tags (e.g. Python,Docker,AI/ML)",
    rate="Your base rate in credits per hour",
    description="A short description of your expertise"
)
async def nexus_register(interaction: discord.Interaction, skills: str,
                         rate: float, description: str = ""):
    """Public — register or update your marketplace listing."""
    await interaction.response.defer()

    try:
        skill_list = [s.strip() for s in skills.split(",") if s.strip()]
        # Use the owner DID as a stand-in (in production, mapped from Discord user)
        agent_did = f"did:clawnexus:discord:{interaction.user.id}"

        result = registry.register_agent(agent_did, skill_list, description, rate)

        embed = discord.Embed(
            title="📢 Marketplace Listing Updated!",
            description=f"You're now visible in the ClawNexus marketplace.",
            color=discord.Color.teal()
        )
        embed.add_field(name="🎯 Skills", value=", ".join(skill_list), inline=True)
        embed.add_field(name="💲 Rate", value=f"{rate} credits/hr", inline=True)
        if description:
            embed.add_field(name="📝 Bio", value=description, inline=False)
        embed.set_footer(text=f"DID: {agent_did[:40]}...")

        await interaction.followup.send(embed=embed)

    except Exception as e:
        log.error(f"Register error: {e}")
        await interaction.followup.send(f"⚠️ Error: `{e}`")


# ============================================================
# Slash Command: /nexus-post (Post an RFP)
# ============================================================
@app_commands.command(name="nexus-post", description="💬 Post a job to the marketplace.")
@app_commands.describe(
    task="Describe what you need done",
    budget="Your budget in credits",
    tags="Optional: comma-separated skill tags to target"
)
async def nexus_post(interaction: discord.Interaction, task: str,
                     budget: float, tags: str = ""):
    """Public — post an RFP to the marketplace."""
    await interaction.response.defer()

    try:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
        client_did = f"did:clawnexus:discord:{interaction.user.id}"

        result = market.post_rfp(client_did, task, tag_list, budget)

        if result["status"] == "ok":
            embed = discord.Embed(
                title="💬 New Job Posted!",
                description=f"**{task}**",
                color=discord.Color.from_rgb(255, 140, 0)  # Orange
            )
            embed.add_field(name="💰 Budget", value=f"{budget} credits", inline=True)
            embed.add_field(name="🎯 Tags", value=", ".join(tag_list) or "Any", inline=True)
            embed.add_field(name="📋 RFP ID", value=f"`{result['rfp_id'][:12]}...`", inline=False)

            # Auto-match and show candidates
            matches = market.match_rfp(result["rfp_id"])
            if matches:
                match_text = ""
                for i, m in enumerate(matches[:3]):
                    verified = " ✅" if m.get("is_verified") else ""
                    match_text += f"{m['rank_emoji']} `{m['agent_did'][:25]}...`{verified} — {m['trust_score']} pts, {m['base_rate']} cr/hr\n"
                embed.add_field(
                    name=f"🎯 {len(matches)} Matching Agent(s)",
                    value=match_text,
                    inline=False
                )

            embed.set_footer(text="Towerwatch Sentinel • Nexus Marketplace")
            embed.timestamp = discord.utils.utcnow()
            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send(f"⚠️ {result.get('reason', 'Unknown error')}")

    except Exception as e:
        log.error(f"Post RFP error: {e}")
        await interaction.followup.send(f"⚠️ Error: `{e}`")


# ============================================================
# Slash Command: /nexus-market (Browse Open RFPs)
# ============================================================
@app_commands.command(name="nexus-market", description="🏪 Browse open jobs in the marketplace.")
async def nexus_market_cmd(interaction: discord.Interaction):
    """Public — browse open RFPs."""
    await interaction.response.defer()

    try:
        rfps = market.list_open_rfps(limit=10)

        if not rfps:
            embed = discord.Embed(
                title="🏪 ClawNexus Marketplace",
                description="No open jobs right now. Be the first to post with `/nexus-post`!",
                color=discord.Color.light_grey()
            )
            await interaction.followup.send(embed=embed)
            return

        embed = discord.Embed(
            title="🏪 ClawNexus Marketplace",
            description=f"**{len(rfps)} open job(s)** waiting for agents.",
            color=discord.Color.from_rgb(255, 140, 0)
        )

        for rfp in rfps:
            tags = ", ".join(rfp.get("required_tags", [])) or "Any"
            embed.add_field(
                name=f"💰 {rfp['budget']} credits — {tags}",
                value=f"{rfp['task_description'][:100]}\n`RFP: {rfp['id'][:12]}...`",
                inline=False
            )

        embed.set_footer(text="Towerwatch Sentinel • Nexus Marketplace")
        embed.timestamp = discord.utils.utcnow()
        await interaction.followup.send(embed=embed)

    except Exception as e:
        log.error(f"Market browse error: {e}")
        await interaction.followup.send(f"⚠️ Error: `{e}`")


# ============================================================
# Entry Point
# ============================================================
if __name__ == "__main__":
    if not DISCORD_BOT_TOKEN:
        log.error("DISCORD_BOT_TOKEN not set in .env. See DISCORD_SETUP.md.")
        exit(1)
    if not DISCORD_CHANNEL_ID:
        log.error("DISCORD_CHANNEL_ID not set in .env.")
        exit(1)
    if not DISCORD_OWNER_ID:
        log.error("DISCORD_OWNER_ID not set in .env.")
        exit(1)

    log.info("Starting ClawNexus Watchtower...")
    bot = WatchtowerBot()
    bot.run(DISCORD_BOT_TOKEN)
