import os
import json
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

BASE_DIR        = os.path.dirname(__file__)
REACTION_FILE   = os.path.join(BASE_DIR, "reaction_roles.json")
NEWS_STATE_FILE = os.path.join(BASE_DIR, "news_state.json")

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s:%(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("aerobot")

# ──────────────────────────────────────────────────────────────────────────────
# Helpers to persist JSON data
def load_json(path):
    if not os.path.isfile(path):
        with open(path, 'w') as fp:
            json.dump({}, fp)
    with open(path, 'r') as fp:
        return json.load(fp)

def save_json(path, data):
    with open(path, 'w') as fp:
        json.dump(data, fp, indent=2)

reaction_roles = load_json(REACTION_FILE)
news_state    = load_json(NEWS_STATE_FILE)

# ──────────────────────────────────────────────────────────────────────────────
# Bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree
_synced = False

# ──────────────────────────────────────────────────────────────────────────────
# Event handlers

@bot.event
async def on_ready():
    global _synced
    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    if not _synced:
        await tree.sync()
        logger.info("Slash commands synced.")
        _synced = True
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
        logger.info(f"Added role {role.name} to {member.display_name} via {payload.emoji}")

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
        logger.info(f"Removed role {role.name} from {member.display_name} via {payload.emoji}")

# ──────────────────────────────────────────────────────────────────────────────
# Slash commands

# Moderation: kick
@tree.command(name="kick", description="Kick a member from the server")
@app_commands.checks.has_permissions(kick_members=True)
async def slash_kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    await member.kick(reason=reason)
    await interaction.response.send_message(f"👢 {member.mention} has been kicked. Reason: {reason}", ephemeral=True)
    logger.info(f"{interaction.user} kicked {member} ({reason})")

# Moderation: ban
@tree.command(name="ban", description="Ban a member from the server")
@app_commands.checks.has_permissions(ban_members=True)
async def slash_ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    await member.ban(reason=reason)
    await interaction.response.send_message(f"🔨 {member.mention} has been banned. Reason: {reason}", ephemeral=True)
    logger.info(f"{interaction.user} banned {member} ({reason})")

# Moderation: mute
@tree.command(name="mute", description="Mute a member by adding a Muted role")
@app_commands.checks.has_permissions(manage_roles=True)
async def slash_mute(interaction: discord.Interaction, member: discord.Member):
    role = discord.utils.get(interaction.guild.roles, name="Muted")
    if not role:
        role = await interaction.guild.create_role(name="Muted")
        for c in interaction.guild.channels:
            await c.set_permissions(role, speak=False, send_messages=False)
    await member.add_roles(role)
    await interaction.response.send_message(f"🤐 {member.mention} has been muted.", ephemeral=True)
    logger.info(f"{interaction.user} muted {member}")

# Reaction role setup
@tree.command(name="add_reaction_role", description="Configure a reaction role on a message")
@app_commands.checks.has_permissions(manage_roles=True)
async def add_reaction_role(
    interaction: discord.Interaction,
    message_id: int,
    emoji: str,
    role: discord.Role
):
    key = f"{interaction.channel_id}-{message_id}"
    reaction_roles.setdefault(key, {})[emoji] = role.id
    save_json(REACTION_FILE, reaction_roles)
    await interaction.response.send_message(
        f"✅ React with {emoji} on message {message_id} to get {role.mention}",
        ephemeral=True
    )
    logger.info(f"Set reaction role in {interaction.channel.name} msg {message_id}: {emoji} → {role.name}")

# ──────────────────────────────────────────────────────────────────────────────
# Aviation news fetcher

@tasks.loop(minutes=60)
async def fetch_news():
    try:
        feed = feedparser.parse(FEED_URL)
        channel = bot.get_channel(NEWS_CHANNEL_ID)
        if channel is None:
            logger.error(f"Invalid NEWS_CHANNEL_ID: {NEWS_CHANNEL_ID}")
            return
        for entry in feed.entries[:5]:
            if entry.id not in news_state:
                await channel.send(f"📰 **{entry.title}**\n{entry.link}")
                news_state[entry.id] = True
        save_json(NEWS_STATE_FILE, news_state)
        logger.info("Aviation news fetched and posted.")
    except Exception:
        logger.exception("Error in fetch_news loop")

# ──────────────────────────────────────────────────────────────────────────────
# Error handler for slash commands

@bot.event
async def on_app_command_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.errors.MissingPermissions):
        await interaction.response.send_message("❌ You lack the required permissions.", ephemeral=True)
    else:
        await interaction.response.send_message("⚠️ An error occurred while processing the command.", ephemeral=True)
        logger.exception(f"Error in command {interaction.command.name}")

# ──────────────────────────────────────────────────────────────────────────────
# Start the bot

if __name__ == "__main__":
    bot.run(TOKEN)
