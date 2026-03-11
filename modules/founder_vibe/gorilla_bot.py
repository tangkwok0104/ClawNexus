"""
🦍 Gorilla — ClawNexus Community Manager Bot.

Owner-only community management bot that handles:
- Server structure scaffolding (categories, channels, permissions)
- Genesis Cohort auto-role assignment on member join
- Auto-role assignment for AI-Mentor / AI-Student
- Permission enforcement

Run: python -m modules.founder_vibe.gorilla_bot
Requires: GORILLA_BOT_TOKEN, DISCORD_OWNER_ID in .env
"""

import os
import logging

import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv

# --- Load Config ---
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

GORILLA_TOKEN = os.getenv("GORILLA_BOT_TOKEN", "")
DISCORD_OWNER_ID = int(os.getenv("DISCORD_OWNER_ID", "0"))

# --- Role Config ---
GENESIS_ROLE_NAME = "Genesis-Founder"
GENESIS_MAX_MEMBERS = 100
MENTOR_ROLE_NAME = "AI-Mentor"
STUDENT_ROLE_NAME = "AI-Student"
PROVIDER_ROLE_NAME = "Provider"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [Gorilla] %(levelname)s %(message)s"
)
log = logging.getLogger("Gorilla")


# ============================================================
# Server Blueprint — Categories, Channels, Permissions
# ============================================================
SERVER_BLUEPRINT = [
    {
        "name": "🌐 │ THE NEXUS PORTAL",
        "channels": [
            {"name": "📜│rules", "topic": "Read the community rules before participating."},
            {"name": "📢│announcements", "topic": "Official ClawNexus updates and news."},
            {"name": "🚀│get-started", "topic": "New here? Start your journey."},
        ],
        "everyone_perms": {"send_messages": False, "view_channel": True, "read_message_history": True},
        "role_overrides": {},  # Owner has admin — no override needed
    },
    {
        "name": "🦞 │ THE MARKETPLACE",
        "channels": [
            {"name": "💬│general", "topic": "The main trade floor. Chat, network, build."},
            {"name": "📋│jobs-board", "topic": "Post and find agent jobs."},
            {"name": "🤝│introductions", "topic": "Introduce yourself and your agents."},
            {"name": "🏆│leaderboard", "topic": "Top agents and rankings."},
        ],
        "everyone_perms": {
            "send_messages": True, "view_channel": True,
            "read_message_history": True, "embed_links": True,
            "attach_files": True, "add_reactions": True,
            "use_external_emojis": True, "mention_everyone": False,
        },
        "role_overrides": {},
    },
    {
        "name": "🛠️ │ THE FORGE",
        "channels": [
            {"name": "💻│dev-logs", "topic": "Live development updates and code changes."},
            {"name": "🐛│bug-reports", "topic": "Report bugs and issues."},
            {"name": "💡│feature-requests", "topic": "Suggest new features."},
        ],
        "everyone_perms": {"send_messages": False, "view_channel": True, "read_message_history": True},
        "role_overrides": {
            PROVIDER_ROLE_NAME: {"send_messages": True, "view_channel": True},
            GENESIS_ROLE_NAME: {"send_messages": True, "view_channel": True},
            MENTOR_ROLE_NAME: {"send_messages": True, "view_channel": True},
        },
    },
    {
        "name": "🛡️ │ WATCHTOWER COMMAND",
        "channels": [
            {"name": "🤖│bot-commands", "topic": "Run bot slash commands here."},
            {"name": "📊│analytics", "topic": "Platform metrics and dashboards."},
            {"name": "🔐│security-logs", "topic": "Audit trail and security events."},
        ],
        "everyone_perms": {"view_channel": False},
        "role_overrides": {},  # Only High Founder (admin) can see
    },
]


# ============================================================
# Helper: Owner-only check
# ============================================================
def is_owner():
    """Decorator to restrict a slash command to the bot owner."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.id != DISCORD_OWNER_ID:
            await interaction.response.send_message(
                "🦍 Only the High Founder can command Gorilla.", ephemeral=True
            )
            return False
        return True
    return app_commands.check(predicate)


# ============================================================
# Helper: Auto-assign role
# ============================================================
async def _auto_assign_role(guild: discord.Guild, member: discord.Member, role_name: str):
    """Safely assign a named role to a member."""
    role = discord.utils.get(guild.roles, name=role_name)
    if not role:
        log.warning(f"Role '{role_name}' not found. Skipping.")
        return False
    if role in member.roles:
        return False  # Already has it
    try:
        await member.add_roles(role, reason=f"Gorilla auto-assign: {role_name}")
        log.info(f"🦍 Role '{role_name}' → {member}")
        return True
    except discord.Forbidden:
        log.error(f"Cannot assign '{role_name}' — check bot role hierarchy.")
        return False


# ============================================================
# Bot Class
# ============================================================
class GorillaBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        for cmd in [gorilla_setup, gorilla_status, gorilla_assign]:
            self.tree.add_command(cmd)
        try:
            synced = await self.tree.sync()
            log.info(f"Synced {len(synced)} Gorilla command(s).")
        except Exception as e:
            log.error(f"Failed to sync commands: {e}")

    async def on_ready(self):
        log.info(f"🦍 Gorilla online as {self.user}")
        log.info(f"   Owner ID: {DISCORD_OWNER_ID}")
        log.info(f"   Guilds: {[g.name for g in self.guilds]}")
        # Set a custom status
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="the Nexus | /gorilla-setup"
            )
        )

    # ----- Genesis Cohort: Auto-assign on join -----
    async def on_member_join(self, member: discord.Member):
        """Auto-assign Genesis-Founder role to the first N members."""
        guild = member.guild

        genesis_role = discord.utils.get(guild.roles, name=GENESIS_ROLE_NAME)
        if not genesis_role:
            log.warning(f"'{GENESIS_ROLE_NAME}' role not found. Create it first.")
            return

        genesis_count = sum(1 for m in guild.members if genesis_role in m.roles)

        if genesis_count < GENESIS_MAX_MEMBERS:
            assigned = await _auto_assign_role(guild, member, GENESIS_ROLE_NAME)
            if assigned:
                # DM welcome
                try:
                    embed = discord.Embed(
                        title="🎁 Welcome to the Genesis Cohort!",
                        description=(
                            f"You are **Genesis Founder #{genesis_count + 1}** — "
                            f"one of the first {GENESIS_MAX_MEMBERS} members of ClawNexus.\n\n"
                            f"✅ Your **'{GENESIS_ROLE_NAME}'** badge is now active.\n"
                            f"💰 Use `/nexus-register` to claim **100 free test credits**!\n\n"
                            f"_This badge is permanent and exclusive._"
                        ),
                        color=discord.Color.gold()
                    )
                    await member.send(embed=embed)
                except discord.Forbidden:
                    log.warning(f"Could not DM {member} (DMs disabled).")

                # Announce in first available text channel
                for channel in guild.text_channels:
                    if channel.permissions_for(guild.me).send_messages:
                        announce = discord.Embed(
                            title="🆕 Genesis Founder Joined!",
                            description=(
                                f"**{member.display_name}** is Genesis Founder "
                                f"**#{genesis_count + 1}** / {GENESIS_MAX_MEMBERS}"
                            ),
                            color=discord.Color.gold()
                        )
                        spots = GENESIS_MAX_MEMBERS - genesis_count - 1
                        announce.set_footer(text=f"🎁 {spots} Genesis spots remaining")
                        await channel.send(embed=announce)
                        break
        else:
            log.info(f"{member} joined but Genesis cohort is full.")

    # ----- Listen for Watchtower registration embeds -----
    async def on_message(self, message: discord.Message):
        """Watch for Watchtower's 'New Agent Registered!' embeds to auto-assign roles."""
        if message.author == self.user:
            return
        if not message.author.bot:
            return  # Only react to bot messages
        if not message.embeds:
            return

        for embed in message.embeds:
            if embed.title and "New Agent Registered" in embed.title:
                # Extract the Discord user who registered from the embed description
                # Format: "**Username** just joined the ClawNexus marketplace!"
                if message.guild and embed.description:
                    # The registering user's name is in the embed, find them
                    desc = embed.description
                    if "**" in desc:
                        display_name = desc.split("**")[1]
                        member = discord.utils.find(
                            lambda m: m.display_name == display_name,
                            message.guild.members
                        )
                        if member:
                            await _auto_assign_role(message.guild, member, MENTOR_ROLE_NAME)
                            log.info(f"🦍 Auto-assigned AI-Mentor to {member} (from Watchtower embed)")

            elif embed.title and "New Job Posted" in embed.title:
                # Similar for AI-Student — posted a job
                if message.guild and embed.description:
                    # The poster info is in the footer
                    pass  # Jobs are posted via slash command, harder to map back

        await self.process_commands(message)


# ============================================================
# Slash Command: /gorilla-setup (Server Scaffolding)
# ============================================================
@app_commands.command(
    name="gorilla-setup",
    description="🦍 Scaffold the entire server structure (categories, channels, permissions). Owner only."
)
@is_owner()
async def gorilla_setup(interaction: discord.Interaction):
    """Create all categories, channels, and permissions from the blueprint."""
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    if not guild:
        await interaction.followup.send("Must be used in a server.", ephemeral=True)
        return

    results = []

    for category_def in SERVER_BLUEPRINT:
        cat_name = category_def["name"]

        # Find or create category
        category = discord.utils.get(guild.categories, name=cat_name)
        if not category:
            # Build @everyone permission overwrite
            everyone_perms = category_def["everyone_perms"]
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(**everyone_perms)
            }

            # Add role-specific overrides
            for role_name, perms in category_def.get("role_overrides", {}).items():
                role = discord.utils.get(guild.roles, name=role_name)
                if role:
                    overwrites[role] = discord.PermissionOverwrite(**perms)

            # Also ensure the bot itself can always see and send
            overwrites[guild.me] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, manage_channels=True
            )

            category = await guild.create_category(cat_name, overwrites=overwrites)
            results.append(f"✅ Created category: **{cat_name}**")
        else:
            results.append(f"⏭️ Category exists: **{cat_name}**")

        # Create channels in this category
        for ch_def in category_def["channels"]:
            ch_name = ch_def["name"]
            existing = discord.utils.get(guild.text_channels, name=ch_name, category=category)
            if not existing:
                await guild.create_text_channel(
                    ch_name,
                    category=category,
                    topic=ch_def.get("topic", "")
                )
                results.append(f"  📝 Created: #{ch_name}")
            else:
                results.append(f"  ⏭️ Exists: #{ch_name}")

    # Build result embed
    embed = discord.Embed(
        title="🦍 Server Scaffolding Complete",
        description="\n".join(results),
        color=discord.Color.green()
    )
    embed.set_footer(text=f"Blueprint: {len(SERVER_BLUEPRINT)} categories, "
                     f"{sum(len(c['channels']) for c in SERVER_BLUEPRINT)} channels")
    await interaction.followup.send(embed=embed, ephemeral=True)
    log.info(f"Server scaffolded by {interaction.user}")


# ============================================================
# Slash Command: /gorilla-status (Health Check)
# ============================================================
@app_commands.command(
    name="gorilla-status",
    description="🦍 Check Gorilla's status and role counts. Owner only."
)
@is_owner()
async def gorilla_status(interaction: discord.Interaction):
    """Show current Genesis spots, role counts, and server health."""
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    if not guild:
        await interaction.followup.send("Must be used in a server.", ephemeral=True)
        return

    # Count role members
    role_counts = {}
    for role_name in [GENESIS_ROLE_NAME, MENTOR_ROLE_NAME, STUDENT_ROLE_NAME, PROVIDER_ROLE_NAME]:
        role = discord.utils.get(guild.roles, name=role_name)
        if role:
            role_counts[role_name] = len([m for m in guild.members if role in m.roles])
        else:
            role_counts[role_name] = "❌ Not found"

    genesis_count = role_counts.get(GENESIS_ROLE_NAME, 0)
    spots = GENESIS_MAX_MEMBERS - genesis_count if isinstance(genesis_count, int) else "?"

    embed = discord.Embed(
        title="🦍 Gorilla Status Report",
        color=discord.Color.from_rgb(139, 92, 246)
    )
    embed.add_field(name="👥 Total Members", value=f"**{guild.member_count}**", inline=True)
    embed.add_field(name="📊 Categories", value=f"**{len(guild.categories)}**", inline=True)
    embed.add_field(name="💬 Channels", value=f"**{len(guild.text_channels)}**", inline=True)

    embed.add_field(name="🦞 Genesis-Founder", value=f"**{genesis_count}** ({spots} spots left)", inline=True)
    embed.add_field(name="🎓 AI-Mentor", value=f"**{role_counts[MENTOR_ROLE_NAME]}**", inline=True)
    embed.add_field(name="🛠️ AI-Student", value=f"**{role_counts[STUDENT_ROLE_NAME]}**", inline=True)
    embed.add_field(name="⚡ Provider", value=f"**{role_counts[PROVIDER_ROLE_NAME]}**", inline=True)

    embed.set_footer(text="🦍 Gorilla Community Manager • ClawNexus")
    embed.timestamp = discord.utils.utcnow()

    await interaction.followup.send(embed=embed, ephemeral=True)


# ============================================================
# Slash Command: /gorilla-assign (Manual Role Assignment)
# ============================================================
@app_commands.command(
    name="gorilla-assign",
    description="🦍 Manually assign a role to a user. Owner only."
)
@app_commands.describe(
    user="The user to assign the role to",
    role="Which role to assign"
)
@app_commands.choices(role=[
    app_commands.Choice(name="🦞 Genesis-Founder", value="Genesis-Founder"),
    app_commands.Choice(name="🎓 AI-Mentor", value="AI-Mentor"),
    app_commands.Choice(name="🛠️ AI-Student", value="AI-Student"),
    app_commands.Choice(name="⚡ Provider", value="Provider"),
])
@is_owner()
async def gorilla_assign(interaction: discord.Interaction,
                         user: discord.Member,
                         role: app_commands.Choice[str]):
    """Manually assign a ClawNexus role to a user."""
    await interaction.response.defer(ephemeral=True)

    assigned = await _auto_assign_role(interaction.guild, user, role.value)
    if assigned:
        embed = discord.Embed(
            title=f"✅ Role Assigned",
            description=f"**{user.display_name}** → **{role.name}**",
            color=discord.Color.green()
        )
    else:
        embed = discord.Embed(
            title="⏭️ Already Has Role",
            description=f"**{user.display_name}** already has **{role.name}** (or role not found).",
            color=discord.Color.greyple()
        )
    await interaction.followup.send(embed=embed, ephemeral=True)


# ============================================================
# Entry Point
# ============================================================
if __name__ == "__main__":
    if not GORILLA_TOKEN:
        log.error("GORILLA_BOT_TOKEN not set in .env. Cannot start.")
        exit(1)
    if not DISCORD_OWNER_ID:
        log.error("DISCORD_OWNER_ID not set in .env.")
        exit(1)

    log.info("🦍 Starting Gorilla Community Manager...")
    bot = GorillaBot()
    bot.run(GORILLA_TOKEN)
