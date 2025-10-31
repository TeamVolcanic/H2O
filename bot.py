import discord
from discord import app_commands
from discord.ext import commands
import os
import datetime
import asyncio
import re
from dotenv import load_dotenv

load_dotenv()

DEFAULT_VERIFY_ROLE_NAME = "🧑︱Member"
BAD_WORDS = ["fuck", "shit", "bitch", "asshole"]

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
        print("✅ Commands synced successfully.")

bot = MyBot()

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


def is_valid_youtube_url(url):
    youtube_regex = (
        r'(https?://)?(www\.)?'
        r'(youtube|youtu|youtube-nocookie)\.(com|be)/'
        r'(watch\?v=|embed/|v/|.+\?v=)?([^&=%\?]{11})'
    )
    youtube_regex_match = re.match(youtube_regex, url)
    return youtube_regex_match is not None


async def extract_video_info_with_retry(url, max_retries=3):
    import yt_dlp
    
    for attempt in range(max_retries):
        try:
            ydl_opts = {
                'format': 'bestaudio[ext=m4a]/bestaudio/best',
                'quiet': True,
                'no_warnings': True,
                'noplaylist': True,
                'extract_flat': False,
                'socket_timeout': 30,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                return info
        except yt_dlp.utils.DownloadError as e:
            error_msg = str(e).lower()
            if 'unavailable' in error_msg or 'private' in error_msg or 'deleted' in error_msg:
                raise Exception("This video is unavailable, private, or has been deleted")
            if 'copyright' in error_msg:
                raise Exception("This video is blocked due to copyright restrictions")
            if attempt == max_retries - 1:
                raise Exception(f"Failed to load video after {max_retries} attempts")
            await asyncio.sleep(2 ** attempt)
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(2 ** attempt)
    
    raise Exception("Failed to extract video information")


@bot.tree.command(name="music", description="Play music from a YouTube URL")
async def music(interaction: discord.Interaction, url: str):
    if not interaction.user.voice:
        await interaction.response.send_message("❌ You must be in a voice channel!", ephemeral=True)
        return

    await interaction.response.defer()

    if not is_valid_youtube_url(url):
        await interaction.followup.send(
            "❌ Invalid URL! Please provide a valid YouTube URL.\n"
            "Examples:\n"
            "• https://www.youtube.com/watch?v=VIDEO_ID\n"
            "• https://youtu.be/VIDEO_ID",
            ephemeral=True
        )
        return

    guild_id = interaction.guild.id
    music_queue = get_music_queue(guild_id)

    if not music_queue.voice_client or not music_queue.voice_client.is_connected():
        try:
            music_queue.voice_client = await interaction.user.voice.channel.connect()
            print(f"[{interaction.guild.name}] Connected to voice channel.")
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to connect to voice channel: {e}")
            return

    try:
        info = await extract_video_info_with_retry(url)
        title = info.get('title', 'Unknown')
        duration = info.get('duration', 0)
        
        if duration > 3600:
            await interaction.followup.send(
                "⚠️ This video is longer than 1 hour. It may cause performance issues.",
                ephemeral=True
            )

        song = {'url': url, 'title': title, 'requested_by': interaction.user.id}
        music_queue.add_song(song)

        if not music_queue.voice_client.is_playing():
            await play_next_song(interaction.guild, music_queue)
            embed = discord.Embed(title="🎵 Now Playing", description=f"**{title}**", color=discord.Color.green())
        else:
            embed = discord.Embed(title="➕ Added to Queue", description=f"**{title}**\nPosition in queue: #{len(music_queue.queue)}", color=discord.Color.blue())

        embed.set_footer(text=f"Requested by {interaction.user.display_name}")
        await interaction.followup.send(embed=embed)
    except Exception as e:
        error_message = str(e)
        if "unavailable" in error_message.lower() or "private" in error_message.lower():
            await interaction.followup.send("❌ This video is unavailable, private, or has been deleted.")
        elif "copyright" in error_message.lower():
            await interaction.followup.send("❌ This video is blocked due to copyright restrictions.")
        elif "unsupported url" in error_message.lower():
            await interaction.followup.send("❌ This URL is not supported. Please use a YouTube video link.")
        else:
            await interaction.followup.send(f"❌ Failed to load video: {error_message}")
        print(f"[{interaction.guild.name}] Error loading {url}: {e}")


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
            'no_warnings': True,
            'noplaylist': True,
            'socket_timeout': 30,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(song['url'], download=False)
            stream_url = info['url']

        print(f"[{guild.name}] Stream URL extracted successfully")

        ffmpeg_options = {
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -http_persistent 0',
            'options': '-vn -b:a 128k'
        }

        def after_playing(error):
            if error:
                print(f"[{guild.name}] Playback error: {error}")
            else:
                print(f"[{guild.name}] Finished: {song['title']}")
            asyncio.run_coroutine_threadsafe(play_next_song(guild, music_queue), bot.loop)

        source = discord.FFmpegPCMAudio(stream_url, executable="ffmpeg", **ffmpeg_options)
        music_queue.voice_client.play(source, after=after_playing)
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

    music_queue.voice_client.source = discord.PCMVolumeTransformer(music_queue.voice_client.source)
    music_queue.voice_client.source.volume = percent / 100
    await interaction.response.send_message(f"🔊 Volume set to **{percent}%**")


DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
if not DISCORD_TOKEN:
    print("❌ Missing DISCORD_TOKEN in environment variables.")
    exit(1)

bot.run(DISCORD_TOKEN)
