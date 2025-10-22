import discord
from discord import app_commands
from discord.ext import commands
import os
import datetime
import asyncio
import json
from dotenv import load_dotenv

# Install ffmpeg for Railway or Linux hosts
os.system("apt-get update && apt-get install -y ffmpeg > /dev/null 2>&1")

# === Load environment variables ===
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

# === Discord Intents ===
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True

# === Bot Class ===
class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await self.tree.sync()
        print("✅ Commands synced successfully.")

bot = MyBot()

# === MUSIC SYSTEM ===
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


# === MUSIC COMMANDS ===
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
        print(f"[{guild.name}] Queue empty — leaving voice channel.")
        if music_queue.voice_client and music_queue.voice_client.is_connected():
            await music_queue.voice_client.disconnect()
        return

    print(f"[{guild.name}] Loading: {song['title']}")

    try:
        import yt_dlp
        ydl_opts = {'format': 'bestaudio/best', 'quiet': True, 'noplaylist': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(song['url'], download=False)
            stream_url = info['url']

        ffmpeg_exe = "ffmpeg"
        ffmpeg_options = {
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
            'options': '-vn'
        }

        def after_playing(error):
            if error:
                print(f"[{guild.name}] Playback error: {error}")
            else:
                print(f"[{guild.name}] Finished: {song['title']}")
            asyncio.run_coroutine_threadsafe(play_next_song(guild, music_queue), bot.loop)

        music_queue.voice_client.play(
            discord.FFmpegPCMAudio(stream_url, executable=ffmpeg_exe, **ffmpeg_options),
            after=after_playing
        )

        music_queue.current = song
        print(f"[{guild.name}] Now playing: {song['title']}")

    except Exception as e:
        print(f"[{guild.name}] ❌ Error playing {song['title']}: {e}")
        await play_next_song(guild, music_queue)


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


# === OPTIONAL: Volume Command ===
@bot.tree.command(name="volume", description="Change playback volume (1-100)")
async def volume(interaction: discord.Interaction, percent: int):
    guild_id = interaction.guild.id
    music_queue = get_music_queue(guild_id)

    if not music_queue.voice_client or not music_queue.voice_client.is_playing():
        await interaction.response.send_message("❌ Nothing is playing!", ephemeral=True)
        return

    if percent < 1 or percent > 100:
        await interaction.response.send_message("⚠️ Please set a value between 1 and 100.", ephemeral=True)
        return

    # Note: Discord.py doesn't have built-in volume, must wrap source
    music_queue.voice_client.source = discord.PCMVolumeTransformer(music_queue.voice_client.source)
    music_queue.voice_client.source.volume = percent / 100
    await interaction.response.send_message(f"🔊 Volume set to **{percent}%**")


# === BOT RUN ===
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
if not DISCORD_TOKEN:
    print("❌ Missing DISCORD_TOKEN in environment.")
    exit(1)

bot.run(DISCORD_TOKEN)
