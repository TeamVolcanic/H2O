import discord
from discord import app_commands
from discord.ext import commands
import os
import datetime
import re
import asyncio
import json
from collections import deque
from dotenv import load_dotenv
from openai import AsyncOpenAI
try:
    import google.generativeai as genai
    GENAI_AVAILABLE = True
except ImportError:
    genai = None
    GENAI_AVAILABLE = False
import io

load_dotenv()

# === Configuration Constants ===
DEFAULT_VERIFY_ROLE_NAME = "🧑︱Member"
COMMANDS_DATA_FILE = "commands_data.json"
TICKETS_DATA_FILE = "tickets_data.json"
TICKET_CATEGORY_NAME = "Tickets"
SUPPORT_ROLES_FILE = "support_roles.json"
VERIFY_ROLES_FILE = "verify_roles.json"
MUSIC_QUEUES_FILE = "music_queues.json"

FEATURE_STATUS = {
    'info': True,
    'kick': True,
    'ban': True,
    'timeout': True,
    'cursing': True,
    'spamming': True,
    'dm': True,
    'warn': True
}

BAD_WORDS = [
    "fuck", "fucking", "fucked", "fucker", "fck", "f*ck",
    "shit", "shitty", "shitting", "bullshit", "horseshit",
    "goddamn", "bitch", "bitching", "bastard", "asshole",
    "ass", "arse", "arsehole", "crap", "crappy", "piss",
    "pissed", "pissing", "dick", "cock", "penis", "pussy",
    "vagina", "cunt", "whore", "slut", "hoe", "prostitute",
    "nigger", "nigga", "negro", "n*gger", "n*gga",
    "fag", "faggot", "f*ggot", "dyke", "retard", "retarded",
    "spastic", "coon", "chink", "gook", "wetback", "beaner",
    "kike", "towelhead", "terrorist", "rape", "raping", "rapist",
    "kill yourself", "kys", "suicide", "cancer", "aids",
    "holocaust", "dork", "nazi", "hitler", "ahh", "slave", "slavery"
]

SPAM_THRESHOLD = 5
SPAM_COOLDOWN = 6
SPAM_TIMEOUT_DURATION = datetime.timedelta(minutes=10)
CURSING_TIMEOUT_DURATION = datetime.timedelta(minutes=5)
TICKET_COOLDOWN_DURATION = 60
MAX_DM_PER_WARN = 5
DM_DELAY = 0.5
MAX_EMBED_LENGTH = 4096

# === Data Structures ===
user_messages = {}
active_dm_tasks = {}
user_warnings = {}
user_dm_limits = {}
prompt_messages = {}
ticket_counter = {}
active_tickets = {}
ticket_claims = {}
support_roles = {}
ticket_cooldowns = {}
verify_roles = {}
music_queues = {}

BAD_WORDS_PATTERN = re.compile(r'(' + '|'.join(re.escape(word) for word in BAD_WORDS) + r')', re.IGNORECASE)

# === Intents and Bot Setup ===
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.voice_states = True

class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await self.tree.sync()
        print("Slash commands synced!")

bot = MyBot()

# === API Setup ===
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if GEMINI_API_KEY and GENAI_AVAILABLE:
    genai.configure(api_key=GEMINI_API_KEY)
    gemini_client = True
else:
    gemini_client = None

# === Data Loading & Saving ===
# (Same as before; no changes to load_data and save_data functions)
# ... [unchanged functions load_data(), save_data(), can_manage_tickets()]

# === Ticket System ===
# ... [unchanged TicketPanelView, TicketControlsView, and ticket commands]

# === Moderation, AI, and Verification Commands ===
# ... [unchanged moderation, warn, verify, and feature commands]

# === Music System Fix ===
class MusicQueue:
    def __init__(self):
        self.queue = []
        self.current = None
        self.voice_client = None

    def add_song(self, song):
        self.queue.append(song)

    def next_song(self):
        if self.queue:
            self.current = self.queue.pop(0)
            return self.current
        self.current = None
        return None

    def clear(self):
        self.queue = []
        self.current = None

guild_music_queues = {}

def get_music_queue(guild_id):
    if guild_id not in guild_music_queues:
        guild_music_queues[guild_id] = MusicQueue()
    return guild_music_queues[guild_id]

# ✅ Fixed /music command
@bot.tree.command(name="music", description="Play music from a YouTube URL")
async def music(interaction: discord.Interaction, url: str):
    if not interaction.user.voice:
        await interaction.response.send_message("❌ You must be in a voice channel to use this command!", ephemeral=True)
        return

    await interaction.response.defer()

    guild_id = interaction.guild.id
    music_queue = get_music_queue(guild_id)

    if not music_queue.voice_client or not music_queue.voice_client.is_connected():
        try:
            music_queue.voice_client = await interaction.user.voice.channel.connect()
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to connect to voice channel: {e}")
            return

    try:
        import yt_dlp
        ydl_opts = {'format': 'bestaudio/best', 'quiet': True, 'noplaylist': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            audio_url = info["url"]
            title = info.get("title", "Unknown Title")

        song = {"url": audio_url, "title": title, "requested_by": interaction.user.id}
        music_queue.add_song(song)

        if not music_queue.voice_client.is_playing():
            await play_next_song(interaction.guild, music_queue)
            embed = discord.Embed(title="🎵 Now Playing", description=f"**{title}**", color=discord.Color.green())
        else:
            embed = discord.Embed(title="➕ Added to Queue", description=f"**{title}** (Position #{len(music_queue.queue)})", color=discord.Color.blue())

        embed.set_footer(text=f"Requested by {interaction.user.display_name}")
        await interaction.followup.send(embed=embed)

    except Exception as e:
        await interaction.followup.send(f"❌ Failed to play song: {e}")


async def play_next_song(guild, music_queue):
    song = music_queue.next_song()
    if not song:
        return

    ffmpeg_options = {
        'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
        'options': '-vn'
    }

    def after_playing(error):
        if error:
            print(f"Error in playback: {error}")
        asyncio.run_coroutine_threadsafe(play_next_song(guild, music_queue), bot.loop)

    source = discord.FFmpegPCMAudio(song["url"], **ffmpeg_options)
    music_queue.voice_client.play(source, after=after_playing)


@bot.tree.command(name="musicskip", description="Skip the current song")
async def musicskip(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    music_queue = get_music_queue(guild_id)

    if not music_queue.voice_client or not music_queue.voice_client.is_playing():
        await interaction.response.send_message("❌ Nothing is currently playing!", ephemeral=True)
        return

    skipped = music_queue.current["title"] if music_queue.current else "Unknown"
    music_queue.voice_client.stop()
    await interaction.response.send_message(
        embed=discord.Embed(title="⏭️ Song Skipped", description=f"Skipped: **{skipped}**", color=discord.Color.orange())
    )


@bot.tree.command(name="musicstop", description="Stop music playback and clear the queue")
@app_commands.checks.has_permissions(administrator=True)
async def musicstop(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    music_queue = get_music_queue(guild_id)

    if not music_queue.voice_client or not music_queue.voice_client.is_connected():
        await interaction.response.send_message("❌ Bot is not in a voice channel!", ephemeral=True)
        return

    music_queue.clear()
    music_queue.voice_client.stop()
    await music_queue.voice_client.disconnect()
    music_queue.voice_client = None

    embed = discord.Embed(title="⏹️ Music Stopped", description="Playback stopped and queue cleared. Bot disconnected.", color=discord.Color.red())
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="musicqueue", description="Show the current music queue")
async def musicqueue(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    music_queue = get_music_queue(guild_id)

    if not music_queue.current and not music_queue.queue:
        await interaction.response.send_message("❌ The music queue is empty!", ephemeral=True)
        return

    embed = discord.Embed(title="🎵 Music Queue", color=discord.Color.purple())

    if music_queue.current:
        requester = interaction.guild.get_member(music_queue.current['requested_by'])
        requester_name = requester.display_name if requester else "Unknown"
        embed.add_field(name="Now Playing", value=f"**{music_queue.current['title']}**\nRequested by: {requester_name}", inline=False)

    if music_queue.queue:
        queue_text = ""
        for i, song in enumerate(music_queue.queue[:10], 1):
            requester = interaction.guild.get_member(song['requested_by'])
            requester_name = requester.display_name if requester else "Unknown"
            queue_text += f"{i}. **{song['title']}**\n   Requested by: {requester_name}\n"

        if len(music_queue.queue) > 10:
            queue_text += f"\n...and {len(music_queue.queue) - 10} more songs"

        embed.add_field(name="Up Next", value=queue_text, inline=False)

    await interaction.response.send_message(embed=embed)


# === Run the Bot ===
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
if not DISCORD_TOKEN:
    print("❌ Error: DISCORD_TOKEN not found in environment variables.")
    exit(1)

bot.run(DISCORD_TOKEN)
