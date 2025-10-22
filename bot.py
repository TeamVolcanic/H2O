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

# Ensure ffmpeg is available on Railway
os.system("apt-get update && apt-get install -y ffmpeg > /dev/null 2>&1")

load_dotenv()

# === Configuration ===
DEFAULT_VERIFY_ROLE_NAME = "🧑︱Member"
COMMANDS_DATA_FILE = "commands_data.json"
TICKETS_DATA_FILE = "tickets_data.json"
TICKET_CATEGORY_NAME = "Tickets"
SUPPORT_ROLES_FILE = "support_roles.json"
VERIFY_ROLES_FILE = "verify_roles.json"

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

BAD_WORDS = ["fuck", "shit", "bitch", "asshole"]  # shortened for brevity

SPAM_THRESHOLD = 5
SPAM_COOLDOWN = 6
SPAM_TIMEOUT_DURATION = datetime.timedelta(minutes=10)
CURSING_TIMEOUT_DURATION = datetime.timedelta(minutes=5)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True

class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
    async def setup_hook(self):
        await self.tree.sync()
        print("Commands synced.")

bot = MyBot()

# === MUSIC SYSTEM (FIXED FOR RAILWAY) ===
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

guild_music_queues = {}

def get_music_queue(guild_id):
    if guild_id not in guild_music_queues:
        guild_music_queues[guild_id] = MusicQueue()
    return guild_music_queues[guild_id]

@bot.tree.command(name="music", description="Play music from a YouTube URL")
async def music(interaction: discord.Interaction, url: str):
    if not interaction.user.voice:
        await interaction.response.send_message("❌ You must be in a voice channel!", ephemeral=True)
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

    import yt_dlp
    try:
        ydl_opts = {'format': 'bestaudio', 'quiet': True, 'noplaylist': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            title = info.get('title', 'Unknown')

        song = {'url': url, 'title': title, 'requested_by': interaction.user.id}
        music_queue.add_song(song)

        if not music_queue.voice_client.is_playing():
            await play_next_song(interaction.guild, music_queue)
            embed = discord.Embed(title="🎵 Now Playing", description=f"**{title}**", color=discord.Color.green())
        else:
            embed = discord.Embed(title="➕ Added to Queue", description=f"**{title}** (#{len(music_queue.queue)})", color=discord.Color.blue())
        embed.set_footer(text=f"Requested by {interaction.user.display_name}")
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to load: {e}")

async def play_next_song(guild, music_queue):
    song = music_queue.next_song()
    if not song:
        return

    def after_playing(error):
        if error:
            print(f"Playback error: {error}")
        asyncio.run_coroutine_threadsafe(play_next_song(guild, music_queue), bot.loop)

    try:
        import yt_dlp
        ydl_opts = {'format': 'bestaudio', 'quiet': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(song['url'], download=False)
            stream_url = info['url']

        ffmpeg_options = {
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
            'options': '-vn'
        }

        music_queue.voice_client.play(
            discord.FFmpegPCMAudio(stream_url, executable="/usr/bin/ffmpeg", **ffmpeg_options),
            after=after_playing
        )
        print(f"Now playing: {song['title']}")

    except Exception as e:
        print(f"FFmpeg or stream error: {e}")

@bot.tree.command(name="musicskip", description="Skip the current song")
async def musicskip(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    music_queue = get_music_queue(guild_id)

    if not music_queue.voice_client or not music_queue.voice_client.is_playing():
        await interaction.response.send_message("❌ Nothing is playing!", ephemeral=True)
        return

    skipped = music_queue.current['title'] if music_queue.current else 'Unknown'
    music_queue.voice_client.stop()
    await interaction.response.send_message(embed=discord.Embed(title="⏭️ Skipped", description=f"**{skipped}**", color=discord.Color.orange()))

@bot.tree.command(name="musicstop", description="Stop music and clear the queue")
@app_commands.checks.has_permissions(administrator=True)
async def musicstop(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    music_queue = get_music_queue(guild_id)

    if not music_queue.voice_client or not music_queue.voice_client.is_connected():
        await interaction.response.send_message("❌ Not connected!", ephemeral=True)
        return

    music_queue.voice_client.stop()
    await music_queue.voice_client.disconnect()
    music_queue.queue.clear()
    await interaction.response.send_message(embed=discord.Embed(title="⏹️ Stopped", description="Music stopped and bot disconnected.", color=discord.Color.red()))

# === BOT RUN ===
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
if not DISCORD_TOKEN:
    print("❌ Missing DISCORD_TOKEN in environment.")
    exit(1)

bot.run(DISCORD_TOKEN)
