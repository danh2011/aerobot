import os, json, logging
import discord
import feedparser
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

# ─── Config & Logging ──────────────────────────────────────────────────────────
load_dotenv()
TOKEN           = os.getenv("DISCORD_TOKEN")
NEWS_CHANNEL_ID = int(os.getenv("NEWS_CHANNEL_ID", 0))
FEED_URL        = os.getenv("FEED_URL", "https://simpleflying.com/feed")
BASE            = os.path.dirname(__file__)

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s:%(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("aerobot")

# ─── JSON Storage Helper ───────────────────────────────────────────────────────
class JSONStorage:
    def __init__(self, filename):
        self.path = os.path.join(BASE, filename)
        if not os.path.isfile(self.path):
            with open(self.path, 'w') as f: json.dump({}, f)
        self._data = json.load(open(self.path))

    def get(self): return self._data
    def save(self):
        with open(self.path, 'w') as f: json.dump(self._data, f, indent=2)

reaction_store = JSONStorage("reaction_roles.json")
news_store     = JSONStorage("news_state.json")

# ─── Bot Setup ────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree
_synced = False

# ─── Events ───────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    global _synced
    logger.info(f"Logged in as {bot.user}")
    if not _synced:
        await tree.sync()
        logger.info("Slash commands synced.")
        _synced = True
        if NEWS_CHANNEL_ID:
            fetch_news.start()

@bot.event
async def on_raw_reaction_add(p):
    mapping = reaction_store.get().get(f"{p.channel_id}-{p.message_id}", {})
    if (rid := mapping.get(str(p.emoji))):
        guild = bot.get_guild(p.guild_id)
        member, role = guild.get_member(p.user_id), guild.get_role(rid)
        await member.add_roles(role)
        logger.info(f"Added {role.name} to {member}")

@bot.event
async def on_raw_reaction_remove(p):
    mapping = reaction_store.get().get(f"{p.channel_id}-{p.message_id}", {})
    if (rid := mapping.get(str(p.emoji))):
        guild = bot.get_guild(p.guild_id)
        member, role = guild.get_member(p.user_id), guild.get_role(rid)
        await member.remove_roles(role)
        logger.info(f"Removed {role.name} from {member}")

@bot.event
async def on_app_command_error(inter, err):
    if isinstance(err, app_commands.errors.MissingPermissions):
        await inter.response.send_message("❌ You lack permissions.", ephemeral=True)
    else:
        logger.exception("Command error")
        await inter.response.send_message("⚠️ An error occurred.", ephemeral=True)

# ─── Moderation Slash Commands (kick, ban, mute) ──────────────────────────────
def mod_command(name, perm, action, emoji):
    @tree.command(name=name, description=f"{action.title()} a member")
    @app_commands.checks.has_permissions(**{perm: True})
    async def cmd(inter, member: discord.Member, *, reason: str="No reason provided"):
        await getattr(member, action)(reason=reason) if action!="mute" else await _mute(inter, member)
        await inter.response.send_message(f"{emoji} {member.mention} {action}ed. Reason: {reason}", ephemeral=True)
        logger.info(f"{inter.user} {action}ed {member} ({reason})")
    return cmd

async def _mute(inter, member):
    role = discord.utils.get(inter.guild.roles, name="Muted") or await inter.guild.create_role(name="Muted")
    for ch in inter.guild.channels: await ch.set_permissions(role, speak=False, send_messages=False)
    await member.add_roles(role)

mod_command("kick", "kick_members", "kick", "👢")
mod_command("ban",  "ban_members",  "ban",  "🔨")
mod_command("mute", "manage_roles", "mute", "🤐")

# ─── Reaction Role Setup ──────────────────────────────────────────────────────
@tree.command(name="add_reaction_role", description="Configure a reaction role")
@app_commands.checks.has_permissions(manage_roles=True)
async def add_reaction_role(inter, message_id: int, emoji: str, role: discord.Role):
    key = f"{inter.channel_id}-{message_id}"
    reaction_store.get().setdefault(key, {})[emoji] = role.id
    reaction_store.save()
    await inter.response.send_message(f"✅ React with {emoji} on message {message_id} to get {role.mention}", ephemeral=True)

# ─── Aviation News Loop ────────────────────────────────────────────────────────
@tasks.loop(minutes=60)
async def fetch_news():
    try:
        feed = feedparser.parse(FEED_URL)
        channel = bot.get_channel(NEWS_CHANNEL_ID)
        if not channel:
            logger.error("Invalid NEWS_CHANNEL_ID")
            return
        for e in feed.entries[:5]:
            if e.id not in news_store.get():
                await channel.send(f"📰 **{e.title}**\n{e.link}")
                news_store.get()[e.id] = True
        news_store.save()
        logger.info("Posted new aviation headlines.")
    except Exception:
        logger.exception("News fetch failed")

# ─── Run ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    bot.run(TOKEN)
