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

from core.clawnexus_identity import DID_PREFIX, generate_keypair, sign_payload, verify_payload
from infrastructure.nexus_vault import lock_escrow, get_balance, deposit, release_escrow, refund_escrow, get_platform_balance, complete_mission
from infrastructure import nexus_db as db
from core import nexus_trust as trust
from modules.founder_vibe import nexus_registry as registry
from modules.founder_vibe import nexus_market as market

# --- Load Config ---
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
DISCORD_OWNER_ID = int(os.getenv("DISCORD_OWNER_ID", "0"))
RELAY_URL = os.getenv("RELAY_URL", "http://localhost:8377")
RELAY_AUTH_TOKEN = os.getenv("RELAY_AUTH_TOKEN", "")

# --- Genesis Cohort Config ---
GENESIS_ROLE_NAME = "Genesis-Founder"        # Must match the exact Discord role name
GENESIS_MAX_MEMBERS = 100                     # Cap for Genesis cohort
GENESIS_WELCOME_CREDITS = 100.0               # Free test credits on registration
MENTOR_ROLE_NAME = "AI-Mentor"                # Auto-assigned on /nexus-register
STUDENT_ROLE_NAME = "AI-Student"              # Auto-assigned on /nexus-post

# Watchtower's own identity (auto-generated on first run)
WATCHTOWER_KEYS_FILE = os.path.join(os.path.dirname(__file__), "..", "..", ".watchtower_keys.json")

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
        intents.members = True   # Required for on_member_join Genesis role
        super().__init__(command_prefix="!", intents=intents)
        self.wt_keys = get_watchtower_identity()
        self.channel = None

    async def setup_hook(self):
        self.poll_relay.start()
        for cmd in [nexus_stats, nexus_top, nexus_profile, nexus_verify, nexus_help,
                     nexus_register, nexus_wallet, nexus_post, nexus_market_cmd]:
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

    # ----- Genesis Cohort: Auto-assign role on join -----
    async def on_member_join(self, member: discord.Member):
        """Auto-assign 'Genesis Founder' role to the first N members."""
        guild = member.guild

        # Find or skip the Genesis role
        genesis_role = discord.utils.get(guild.roles, name=GENESIS_ROLE_NAME)
        if not genesis_role:
            log.warning(f"'{GENESIS_ROLE_NAME}' role not found in guild. Create it in Server Settings > Roles.")
            return

        # Count how many members already have the role
        genesis_count = sum(1 for m in guild.members if genesis_role in m.roles)

        if genesis_count < GENESIS_MAX_MEMBERS:
            try:
                await member.add_roles(genesis_role, reason="Genesis Cohort auto-assign")
                log.info(f"🎁 Genesis Founder role assigned to {member} (#{genesis_count + 1}/{GENESIS_MAX_MEMBERS})")

                # DM the new member with a welcome
                try:
                    welcome_embed = discord.Embed(
                        title="🎁 Welcome to the Genesis Cohort!",
                        description=(
                            f"You are **Genesis Founder #{genesis_count + 1}** — one of the first {GENESIS_MAX_MEMBERS} founders of ClawNexus.\n\n"
                            f"✅ Your **'{GENESIS_ROLE_NAME}'** badge is now active.\n"
                            f"💰 Register with `/nexus-register` to claim **{int(GENESIS_WELCOME_CREDITS)} free test credits**!\n\n"
                            f"_This badge is permanent and exclusive. It will never be issued again._"
                        ),
                        color=discord.Color.gold()
                    )
                    await member.send(embed=welcome_embed)
                except discord.Forbidden:
                    log.warning(f"Could not DM {member} (DMs disabled).")

                # Announce in the watchtower channel
                if self.channel:
                    announce = discord.Embed(
                        title="🆕 Genesis Founder Joined!",
                        description=f"**{member.display_name}** is Genesis Founder **#{genesis_count + 1}** / {GENESIS_MAX_MEMBERS}",
                        color=discord.Color.gold()
                    )
                    spots = GENESIS_MAX_MEMBERS - genesis_count - 1
                    announce.set_footer(text=f"🎁 {spots} Genesis spots remaining")
                    await self.channel.send(embed=announce)

            except discord.Forbidden:
                log.error(f"Bot lacks 'Manage Roles' permission or role is above bot's position.")
        else:
            log.info(f"{member} joined but Genesis cohort is full ({GENESIS_MAX_MEMBERS}/{GENESIS_MAX_MEMBERS}).")

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
# Helper: Auto-assign a Discord role from a slash command
# ============================================================
async def _auto_assign_role(interaction: discord.Interaction, role_name: str):
    """Safely assign a role to the user who triggered a slash command."""
    if not interaction.guild:
        return  # DM context — no guild roles
    role = discord.utils.get(interaction.guild.roles, name=role_name)
    if not role:
        log.warning(f"Role '{role_name}' not found in guild. Skipping auto-assign.")
        return
    member = interaction.guild.get_member(interaction.user.id)
    if not member:
        return
    if role in member.roles:
        return  # Already has the role
    try:
        await member.add_roles(role, reason=f"Auto-assigned via ClawNexus command")
        log.info(f"✅ Role '{role_name}' assigned to {member}")
    except discord.Forbidden:
        log.error(f"Cannot assign '{role_name}' — bot lacks Manage Roles permission or role is above bot.")


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
RANK_BADGES = {
    "Iron":     "[ I ]",
    "Bronze":   "[ II ]",
    "Silver":   "[ III ]",
    "Gold":     "[ IV ]",
    "Platinum": "[ V ]",
    "Diamond":  "[ VI ]",
}


@app_commands.command(name="nexus-register", description="📢 Register your agent in the marketplace.")
@app_commands.describe(
    skills="Comma-separated skill tags (e.g. Python,Docker,AI/ML)",
    rate="Your base rate in SOL per hour",
    description="A short description of your expertise"
)
async def nexus_register(interaction: discord.Interaction, skills: str,
                         rate: float, description: str = ""):
    """Public — register or update your marketplace listing with real crypto identity."""
    await interaction.response.defer(ephemeral=True)

    try:
        discord_id = str(interaction.user.id)

        # ── DUPLICATE CHECK: Has this user already registered? ──
        existing = db.get_agent_by_discord_id(discord_id)
        if existing:
            agent_did = existing["did"]
            rank = existing.get("rank", "Iron")
            rank_badge = RANK_BADGES.get(rank, "[ I ]")

            # Update their listing (skills/rate/description) but keep same DID
            skill_list = [s.strip() for s in skills.split(",") if s.strip()]
            registry.register_agent(agent_did, skill_list, description, rate)

            update_embed = discord.Embed(
                title="🦞 IDENTITY ALREADY EXISTS",
                description=(
                    f"Welcome back, **{interaction.user.display_name}**! "
                    f"Your listing has been updated.\n\n"
                    f"Use `/nexus-wallet` to check your balance."
                ),
                color=discord.Color.gold()
            )
            update_embed.add_field(name="🆔 Identity", value=f"`{agent_did[:40]}...`", inline=False)
            update_embed.add_field(name="🛡️ Status", value="VERIFIED", inline=True)
            update_embed.add_field(name="🏅 Rank", value=f"{rank_badge} {rank.upper()}", inline=True)
            update_embed.add_field(name="🎯 Skills", value=", ".join(skill_list), inline=False)
            update_embed.set_footer(text="Your DID is permanent. Skills & rate updated.")
            await interaction.followup.send(embed=update_embed, ephemeral=True)
            return

        # ── NEW REGISTRATION ──
        skill_list = [s.strip() for s in skills.split(",") if s.strip()]

        # Generate a REAL Ed25519 keypair for this agent
        private_hex, public_hex, agent_did = generate_keypair()

        # Register in marketplace (stores ONLY public key + DID)
        result = registry.register_agent(agent_did, skill_list, description, rate)

        # Store agent with discord_id link + Iron rank
        db.ensure_agent(agent_did, discord_id=discord_id, rank="Iron")

        # ── MESSAGE 1: PASSPORT (ephemeral — only the user sees this) ──
        rank = "Iron"
        rank_badge = RANK_BADGES[rank]

        passport = discord.Embed(
            title="🦞 CLAWNEXUS IDENTITY SECURED",
            description=(
                "Your identity has been etched into the Nexus Ledger.\n"
                "Head to **#agent-listings** to see your profile live."
            ),
            color=discord.Color.from_rgb(0, 255, 200)  # ClawNexus teal
        )
        passport.add_field(
            name="👤 Founder",
            value=f"**@{interaction.user.display_name}**",
            inline=True
        )
        passport.add_field(
            name="🛡️ Status",
            value="VERIFIED ✅",
            inline=True
        )
        passport.add_field(
            name="🏅 Rank",
            value=f"**{rank_badge} {rank.upper()}**",
            inline=True
        )
        passport.add_field(
            name="🆔 Identity",
            value=f"```{agent_did}```",
            inline=False
        )
        passport.add_field(
            name="🎯 Skills",
            value=", ".join(skill_list) if skill_list else "Not specified",
            inline=True
        )
        passport.add_field(
            name="💲 Rate",
            value=f"{rate} SOL/hr",
            inline=True
        )
        passport.set_footer(text="Welcome to the Nexus, Pioneer. 🦞")
        passport.timestamp = discord.utils.utcnow()

        await interaction.followup.send(embed=passport, ephemeral=True)

        # ── MESSAGE 2: PRIVATE KEY (ephemeral — ultra-sensitive) ──
        key_embed = discord.Embed(
            title="🔐 Your Private Key — SAVE THIS NOW!",
            description=(
                "⚠️ **This message is only visible to you.**\n\n"
                "Your private key is like a **master password**. "
                "If you lose it, you lose access to your agent identity forever.\n\n"
                "**Save it somewhere safe right now** (password manager, encrypted note).\n"
                "**Never share your private key with anyone — not even ClawNexus staff.**"
            ),
            color=discord.Color.red()
        )
        key_embed.add_field(
            name="🔑 Private Key (SECRET — never share!)",
            value=f"```{private_hex}```",
            inline=False
        )
        key_embed.add_field(
            name="🆔 Public Key",
            value=f"```{public_hex}```",
            inline=False
        )
        key_embed.set_footer(text="🛡️ Your private key is NEVER stored on our servers. Only YOU have it.")
        await interaction.followup.send(embed=key_embed, ephemeral=True)

        # ── PUBLIC ANNOUNCEMENT ──
        pub_embed = discord.Embed(
            title="🦞 New Identity Secured!",
            description=(
                f"**{interaction.user.display_name}** has joined the ClawNexus network.\n"
                f"Rank: **{rank_badge} {rank.upper()}**"
            ),
            color=discord.Color.teal()
        )
        pub_embed.add_field(name="🎯 Skills", value=", ".join(skill_list), inline=True)
        pub_embed.add_field(name="💲 Rate", value=f"{rate} SOL/hr", inline=True)
        if description:
            pub_embed.add_field(name="📝 Bio", value=description, inline=False)
        pub_embed.set_footer(text=f"DID: {agent_did[:40]}...")
        pub_embed.timestamp = discord.utils.utcnow()

        await interaction.channel.send(embed=pub_embed)

        # ── AUTO-ASSIGN: AI-Mentor role ──
        await _auto_assign_role(interaction, MENTOR_ROLE_NAME)

        # ── AUTO-DEPOSIT: Genesis Welcome Credits ──
        try:
            deposit(agent_did, GENESIS_WELCOME_CREDITS)
            credit_embed = discord.Embed(
                title="💰 Genesis Bonus Credited!",
                description=(
                    f"**{int(GENESIS_WELCOME_CREDITS)} test credits** deposited to your vault.\n"
                    f"Use them to post a job with `/nexus-post` or explore the marketplace!"
                ),
                color=discord.Color.green()
            )
            credit_embed.set_footer(text=f"DID: {agent_did[:40]}...")
            await interaction.followup.send(embed=credit_embed, ephemeral=True)
            log.info(f"Genesis credits ({GENESIS_WELCOME_CREDITS}) deposited for {agent_did[:20]}...")
        except Exception as e:
            log.error(f"Failed to deposit genesis credits: {e}")

    except Exception as e:
        log.error(f"Register error: {e}")
        await interaction.followup.send(f"⚠️ Error: `{e}`", ephemeral=True)


# ============================================================
# Slash Command: /nexus-wallet (Wallet & Balance Management)
# ============================================================
ESCROW_PROGRAM_ID = "tWrdP9vPV3j4DsJfdyWXdxLEZnRRLJuukkwHdmdipQv"
EXPLORER_URL = f"https://explorer.solana.com/address/{ESCROW_PROGRAM_ID}"

@app_commands.command(name="nexus-wallet", description="💰 Check your wallet balance or manage your SOL.")
@app_commands.describe(
    action="What would you like to do?",
)
@app_commands.choices(action=[
    app_commands.Choice(name="💰 Check My Balance", value="balance"),
    app_commands.Choice(name="📋 View Wallet Info", value="info"),
])
async def nexus_wallet(interaction: discord.Interaction,
                       action: app_commands.Choice[str]):
    """Public — manage your ClawNexus wallet (Vault)."""
    await interaction.response.defer(ephemeral=True)

    try:
        agent_did = f"did:clawnexus:discord:{interaction.user.id}"

        if action.value == "balance":
            balance = get_balance(agent_did)
            embed = discord.Embed(
                title="💰 Your Vault Balance",
                color=discord.Color.green() if balance > 0 else discord.Color.greyple()
            )
            embed.add_field(name="Available SOL", value=f"**{balance:.4f} SOL**", inline=True)
            embed.add_field(name="Status", value="✅ Active" if balance > 0 else "⚠️ Empty — complete missions to earn SOL", inline=True)
            embed.add_field(
                name="⛓️ On-Chain Escrow",
                value=f"Missions secured by [Solana smart contract]({EXPLORER_URL})\nProgram: `{ESCROW_PROGRAM_ID[:16]}...`",
                inline=False
            )
            embed.set_footer(text=f"DID: {agent_did[:40]}...")
            await interaction.followup.send(embed=embed, ephemeral=True)

        elif action.value == "info":
            balance = get_balance(agent_did)
            embed = discord.Embed(
                title="📋 Your Wallet Info",
                description="Your Vault is your personal account on ClawNexus. All mission payments are secured by an on-chain Solana smart contract.",
                color=discord.Color.teal()
            )
            embed.add_field(name="🆔 Your DID", value=f"```{agent_did}```", inline=False)
            embed.add_field(name="💰 Balance", value=f"{balance:.4f} SOL", inline=True)
            embed.add_field(name="💸 Platform Fee", value="2% per mission", inline=True)
            embed.add_field(
                name="⛓️ Smart Contract (Devnet)",
                value=f"[View on Solana Explorer →]({EXPLORER_URL})\n`{ESCROW_PROGRAM_ID}`",
                inline=False
            )
            embed.add_field(
                name="🛡️ Security Guarantees",
                value=(
                    "✅ Private keys **never stored** on our servers\n"
                    "✅ Even if DB is breached, attackers only get useless public keys\n"
                    "✅ No one — not even the founders — can access your private key"
                ),
                inline=False
            )
            embed.add_field(
                name="📖 How to Earn SOL",
                value=(
                    "• **Complete missions** as a Freelancer — the #1 way\n"
                    "• **Receive grants** from the platform or other agents\n"
                    "• **Direct Solana deposits** — connect external wallet (coming soon)"
                ),
                inline=False
            )
            embed.set_footer(text="🔒 Escrow powered by Solana blockchain — trustless & verifiable")
            await interaction.followup.send(embed=embed, ephemeral=True)

    except Exception as e:
        log.error(f"Wallet error: {e}")
        await interaction.followup.send(f"⚠️ Error: `{e}`", ephemeral=True)


# ============================================================
# Slash Command: /nexus-post (Post an RFP)
# ============================================================
@app_commands.command(name="nexus-post", description="💬 Post a job to the marketplace.")
@app_commands.describe(
    task="Describe what you need done",
    budget="Your budget in SOL",
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
            embed.add_field(name="💰 Budget", value=f"{budget} SOL", inline=True)
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

            # ── AUTO-ASSIGN: AI-Student role ──
            await _auto_assign_role(interaction, STUDENT_ROLE_NAME)
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
