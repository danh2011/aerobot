import os
import json
import asyncio
import logging
from datetime import datetime
import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv
import feedparser

# ──────────────────────────────────────────────────────────────────────────────
# Load environment & configure logging
load_dotenv()
TOKEN           = os.getenv("DISCORD_TOKEN")
NEWS_CHANNEL_ID = int(os.getenv("NEWS_CHANNEL_ID", 0))
FEED_URL        = os.getenv("FEED_URL", "https://simpleflying.com/feed")
REACTION_FILE   = "reaction_roles.json"

logger = logging.getLogger('aerobot')
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter(
    '[%(asctime)s] %(levelname)s:%(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
))
logger.addHandler(handler)

# ──────────────────────────────────────────────────────────────────────────────
# Intents & Bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# ──────────────────────────────────────────────────────────────────────────────
# Utilities for reaction-role persistence
def load_reaction_roles():
    if os.path.isfile(REACTION_FILE):
        with open(REACTION_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_reaction_roles(data):
    with open(REACTION_FILE, 'w') as f:
        json.dump(data, f, indent=2)

reaction_roles = load_reaction_roles()

# ──────────────────────────────────────────────────────────────────────────────
# Events
@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    await tree.sync()
    logger.info("Slash commands synced.")
    if NEWS_CHANNEL_ID:
        fetch_news.start()

@bot.event
async def on_raw_reaction_add(payload):
    key = f"{payload.channel_id}-{payload.message_id}"
    mapping = reaction_roles.get(key, {})
    role_id = mapping.get(str(payload.emoji))
    if role_id:
        guild = bot.get_guild(payload.guild_id)
        member = guild.get_member(payload.user_id)
        role = guild.get_role(role_id)
        await member.add_roles(role)
        logger.info(f"Added role {role.name} to {member.display_name} via reaction {payload.emoji}")

@bot.event
async def on_raw_reaction_remove(payload):
    key = f"{payload.channel_id}-{payload.message_id}"
    mapping = reaction_roles.get(key, {})
    role_id = mapping.get(str(payload.emoji))
    if role_id:
        guild = bot.get_guild(payload.guild_id)
        member = guild.get_member(payload.user_id)
        role = guild.get_role(role_id)
        await member.remove_roles(role)
        logger.info(f"Removed role {role.name} from {member.display_name} via reaction {payload.emoji}")

# ──────────────────────────────────────────────────────────────────────────────
# Slash Commands

## Moderation
@tree.command(name="kick", description="Kick a member")
@app_commands.checks.has_permissions(kick_members=True)
async def slash_kick(interaction: discord.Interaction, member: discord.Member, reason: str="No reason provided"):
    await member.kick(reason=reason)
    await interaction.response.send_message(f"👢 {member} kicked: {reason}", ephemeral=True)
    logger.info(f"{interaction.user} kicked {member} ({reason})")

@tree.command(name="ban", description="Ban a member")
@app_commands.checks.has_permissions(ban_members=True)
async def slash_ban(interaction: discord.Interaction, member: discord.Member, reason: str="No reason provided"):
    await member.ban(reason=reason)
    await interaction.response.send_message(f"🔨 {member} banned: {reason}", ephemeral=True)
    logger.info(f"{interaction.user} banned {member} ({reason})")

@tree.command(name="mute", description="Mute a member (add 'Muted' role)")
@app_commands.checks.has_permissions(manage_roles=True)
async def slash_mute(interaction: discord.Interaction, member: discord.Member):
    role = discord.utils.get(interaction.guild.roles, name="Muted")
    if not role:
        role = await interaction.guild.create_role(name="Muted")
        for ch in interaction.guild.channels:
            await ch.set_permissions(role, speak=False, send_messages=False)
    await member.add_roles(role)
    await interaction.response.send_message(f"🤐 {member} has been muted.", ephemeral=True)
    logger.info(f"{interaction.user} muted {member}")

## Reaction Roles Setup
@tree.command(name="add_reaction_role", description="Set up a reaction role")
@app_commands.checks.has_permissions(manage_roles=True)
async def add_reaction_role(
    interaction: discord.Interaction,
    message_id: str,
    emoji: str,
    role: discord.Role
):
    key = f"{interaction.channel_id}-{message_id}"
    mapping = reaction_roles.get(key, {})
    mapping[emoji] = role.id
    reaction_roles[key] = mapping
    save_reaction_roles(reaction_roles)
    await interaction.response.send_message(
        f"✅ Reaction role set: React with {emoji} on message {message_id} to get {role.mention}",
        ephemeral=True
    )
    logger.info(f"Reaction role configured on {message_id} {emoji}→{role.name}")

## Aviation News Management
@tasks.loop(minutes=60)
async def fetch_news():
    logger.info("Fetching aviation news feed...")
    feed = feedparser.parse(FEED_URL)
    channel = bot.get_channel(NEWS_CHANNEL_ID)
    now = datetime.utcnow()
    for entry in feed.entries[:5]:
        # Use published_parsed or fallback
        pub = datetime(*entry.published_parsed[:6]) if 'published_parsed' in entry else now
        key = f"news:{entry.id}"
        if not bot.cache.get(key):
            await channel.send(f"📰 **{entry.title}**\n{entry.link}")
            bot.cache[key] = pub.isoformat()
            logger.info(f"Posted news: {entry.title}")

@fetch_news.before_loop
async def before_fetch_news():
    await bot.wait_until_ready()

# ──────────────────────────────────────────────────────────────────────────────
# In-memory cache (won't persist across restarts—but avoids repost spam)
bot.cache = {}

# ──────────────────────────────────────────────────────────────────────────────
# Error handling
@bot.event
async def on_app_command_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.errors.MissingPermissions):
        await interaction.response.send_message("❌ You lack the required permissions.", ephemeral=True)
    else:
        await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)
        logger.exception(f"Error in command {interaction.command}:")

# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    bot.run(TOKEN)
