import discord
from discord import app_commands
from discord.ext import commands
import os
import datetime
import asyncio
from dotenv import load_dotenv

# === Ensure ffmpeg is available ===
os.system("apt update && apt install -y ffmpeg > /dev/null 2>&1 || true")

# === Load environment variables ===
load_dotenv()

# === Configuration ===
DEFAULT_VERIFY_ROLE_NAME = "🧑︱Member"
BAD_WORDS = ["fuck", "shit", "bitch", "asshole"]

SPAM_THRESHOLD = 5
SPAM_COOLDOWN = 6
SPAM_TIMEOUT_DURATION = datetime.timedelta(minutes=10)
CURSING_TIMEOUT_DURATION = datetime.timedelta(minutes=5)

# === Discord Intents ===
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True

# === Bot Setup ===
class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await self.tree.sync()
        print("✅ Commands synced successfully.")

bot = MyBot()

# === MUSIC QUEUE SYSTEM ===
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
            print(f"[{interaction.guild.name}] Connected to voice channel.")
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to connect to voice channel: {e}")
            return

    import yt_dlp
    try:
        ydl_opts = {
            'format': 'bestaudio[ext=m4a]/bestaudio/best',
            'quiet': True,
            'noplaylist': True
        }
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
        print(f"[{guild.name}] Queue empty — waiting instead of disconnecting.")
        return

    print(f"[{guild.name}] Loading: {song['title']}")

    try:
        import yt_dlp
        ydl_opts = {
            'format': 'bestaudio[ext=m4a]/bestaudio/best',
            'quiet': True,
            'noplaylist': True
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(song['url'], download=False)
            stream_url = info['url']

        print(f"[DEBUG] Streaming URL: {stream_url[:100]}...")
        print(f"[DEBUG] Using FFmpeg executable: ffmpeg")

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

        # Play the song
        source = discord.FFmpegPCMAudio(stream_url, executable="ffmpeg", **ffmpeg_options)
        music_queue.voice_client.play(source, after=after_playing)
        music_queue.current = song
        print(f"[{guild.name}] Now playing: {song['title']}")

    except Exception as e:
        print(f"[{guild.name}] ❌ Error playing {song['title']}: {e}")
        # Skip the broken song and continue
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
    await interaction.response.send_message(embed=discord.Embed(
        title="⏭️ Skipped",
        description=f"**{skipped}**",
        color=discord.Color.orange()
    ))


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
    await interaction.response.send_message(embed=discord.Embed(
        title="⏹️ Stopped",
        description="Music stopped and bot disconnected.",
        color=discord.Color.red()
    ))


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

    # Wrap audio in volume transformer
    music_queue.voice_client.source = discord.PCMVolumeTransformer(music_queue.voice_client.source)
    music_queue.voice_client.source.volume = percent / 100
    await interaction.response.send_message(f"🔊 Volume set to **{percent}%**")


# === BOT RUN ===
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
if not DISCORD_TOKEN:
    print("❌ Missing DISCORD_TOKEN in environment variables.")
    exit(1)

bot.run(DISCORD_TOKEN)
