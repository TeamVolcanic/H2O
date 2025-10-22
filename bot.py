# railway_discord_bot.py
# Discord moderation + music bot ready for Railway
# Requires: Python 3.10+

# Features:
# - Slash commands: ban/unban, timeout/untimeout, warn/unwarn/viewwarns, dm/dmstop, dmeveryone/dmeveryonestop
# - Toggleable features via /feature enable|disable <featurename>
# - Anti-cursing (auto-timeout, customizable)
# - Anti-spam (3 identical messages -> timeout, customizable)
# - Persistent storage for warnings and settings (JSON files)
# - Music commands: /music play <youtube_link>, /music skip, /music stop, /music queue
# - Queueing, skip, stop supported

# IMPORTANT: Put your bot token and optionally GUILD_ID in a .env file on Railway:
# DISCORD_TOKEN=your_token_here
# (optional) GUILD_ID=your_guild_id (as integer) -- if provided will register commands to that guild for fast updates

# Requirements (put in requirements.txt):
# discord.py>=2.3.2
# PyNaCl
# yt-dlp
# aiohttp
# python-dotenv

# On Railway you also need FFmpeg available. If their environment doesn't already include ffmpeg,
# you need to add a build step or use a Docker deployment that includes ffmpeg.

import discord
from discord import app_commands
from discord.ext import commands, tasks
import os
import json
import asyncio
import yt_dlp
from dotenv import load_dotenv
from datetime import datetime, timedelta
from pathlib import Path
import re

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
GUILD_ID = int(os.getenv('GUILD_ID')) if os.getenv('GUILD_ID') else None

DATA_DIR = Path('data')
DATA_DIR.mkdir(exist_ok=True)
WARN_FILE = DATA_DIR / 'warns.json'
FEATURE_FILE = DATA_DIR / 'features.json'
DM_TASKS_FILE = DATA_DIR / 'dm_tasks.json'  # keep track of cancel tokens (not necessary to persist but fine)

# helper to load/save JSON
def load_json(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return default
    return default

def save_json(path, data):
    path.write_text(json.dumps(data, indent=2))

# persistent stores
warns = load_json(WARN_FILE, {})
features = load_json(FEATURE_FILE, {})
# default features per guild if not set
# structure: features[guild_id] = {"anti_cursing": True, "anti_cursing_timeout_mins":5, "anti_spam": True, "anti_spam_timeout_mins":3, "dm_cancellation": False }

# quick list of cursewords (edit to taste)
CURSE_WORDS = [
    "fuck","shit","bitch","asshole","bastard","damn","cunt","motherfucker"
]
curse_pattern = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in CURSE_WORDS) + r")\b", flags=re.IGNORECASE)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix='!', intents=intents)

# Music state per guild
class MusicPlayer:
    def __init__(self, guild_id):
        self.guild_id = guild_id
        self.queue = asyncio.Queue()
        self.current = None
        self.voice_client = None
        self.play_task = None
        self.skip_event = asyncio.Event()
        self.stopped = False

    async def connect(self, voice_channel: discord.VoiceChannel):
        if self.voice_client and self.voice_client.is_connected():
            await self.voice_client.move_to(voice_channel)
            return self.voice_client
        self.voice_client = await voice_channel.connect()
        return self.voice_client

    async def add_song(self, source):
        await self.queue.put(source)
        if not self.play_task or self.play_task.done():
            self.play_task = asyncio.create_task(self.player_loop())

    async def player_loop(self):
        while not self.stopped:
            try:
                source = await self.queue.get()
            except asyncio.CancelledError:
                break
            self.current = source
            # play
            if not self.voice_client or not self.voice_client.is_connected():
                break
            self.skip_event.clear()
            fut = asyncio.get_event_loop().create_future()

            def after_play(err):
                if err:
                    bot.loop.call_soon_threadsafe(fut.set_exception, err)
                else:
                    bot.loop.call_soon_threadsafe(fut.set_result, None)

            self.voice_client.play(source, after=after_play)
            try:
                await fut
            except Exception:
                pass
            self.current = None
            # if stopped, break
            if self.stopped:
                break
        # cleanup
        if self.voice_client and self.voice_client.is_connected():
            try:
                await self.voice_client.disconnect()
            except Exception:
                pass

music_players = {}  # guild_id -> MusicPlayer

# yt-dlp helper to get best audio stream URL
YTDLP_OPTS = {
    'format': 'bestaudio/best',
    'quiet': True,
    'no_warnings': True,
    'skip_download': True,
}

dyld = yt_dlp.YoutubeDL(YTDLP_OPTS)

async def create_ffmpeg_source(url):
    # use yt-dlp to extract direct audio url
    loop = asyncio.get_event_loop()
    info = await loop.run_in_executor(None, lambda: dyld.extract_info(url, download=False))
    if 'url' in info:
        audio_url = info['url']
    elif 'entries' in info and len(info['entries'])>0:
        audio_url = info['entries'][0]['url']
    else:
        raise RuntimeError('Could not extract audio')
    # ffmpeg options - using pipe through FFmpegPCMAudio
    return discord.FFmpegPCMAudio(audio_url, executable='ffmpeg', options='-vn')

# ---------- utility functions for persistent settings ----------

def ensure_guild_features(guild_id: int):
    if str(guild_id) not in features:
        features[str(guild_id)] = {
            'anti_cursing': True,
            'anti_cursing_timeout_mins': 5,
            'anti_spam': True,
            'anti_spam_timeout_mins': 3,
            'dm_cancel_tokens': {},
        }
        save_json(FEATURE_FILE, features)

# warnings helpers

def add_warn(guild_id: int, member_id: int, reason: str, moderator_id: int):
    g = str(guild_id)
    m = str(member_id)
    if g not in warns:
        warns[g] = {}
    if m not in warns[g]:
        warns[g][m] = []
    warns[g][m].append({'reason': reason, 'moderator': moderator_id, 'time': datetime.utcnow().isoformat()})
    save_json(WARN_FILE, warns)

def remove_warn(guild_id: int, member_id: int, index: int):
    g = str(guild_id)
    m = str(member_id)
    if g in warns and m in warns[g] and 0 <= index < len(warns[g][m]):
        warns[g][m].pop(index)
        save_json(WARN_FILE, warns)
        return True
    return False

# message history for anti-spam detection (in-memory)
recent_messages = {}  # guild_id -> member_id -> list of last messages (content str)

# ---------- event handlers ----------

@bot.event
async def on_ready():
    print('Bot ready. Logged in as', bot.user)
    # register commands to guild if provided (fast updates) or globally if not.
    if GUILD_ID:
        guild = discord.Object(id=GUILD_ID)
        try:
            await bot.tree.sync(guild=guild)
            print('Synced commands to guild', GUILD_ID)
        except Exception as e:
            print('Failed to sync to guild:', e)
    else:
        try:
            await bot.tree.sync()
            print('Synced global commands')
        except Exception as e:
            print('Failed to global sync:', e)

@bot.event
async def on_message(message: discord.Message):
    # ignore bot messages
    if message.author.bot:
        return
    guild = message.guild
    if not guild:
        return
    ensure_guild_features(guild.id)
    gf = features[str(guild.id)]

    # anti-cursing
    if gf.get('anti_cursing', False):
        if curse_pattern.search(message.content or ''):
            # timeout the user for configured minutes
            mins = gf.get('anti_cursing_timeout_mins', 5)
            until = datetime.utcnow() + timedelta(minutes=mins)
            try:
                await message.author.edit(timed_out_until=until)
                await message.channel.send(f"{message.author.mention} was automatically timed out for {mins} minutes (anti-cursing).")
            except Exception:
                await message.channel.send("Failed to apply timeout (missing permissions?)")
            # optionally delete the message
            try:
                await message.delete()
            except Exception:
                pass
            return
    # anti-spam (three identical messages)
    if gf.get('anti_spam', False):
        gid = str(guild.id)
        mid = str(message.author.id)
        recent_messages.setdefault(gid, {}).setdefault(mid, [])
        hist = recent_messages[gid][mid]
        hist.append(message.content)
        # keep last 5
        if len(hist) > 5:
            hist.pop(0)
        if len(hist) >= 3 and hist[-1] == hist[-2] == hist[-3]:
            mins = gf.get('anti_spam_timeout_mins', 3)
            until = datetime.utcnow() + timedelta(minutes=mins)
            try:
                await message.author.edit(timed_out_until=until)
                await message.channel.send(f"{message.author.mention} was automatically timed out for {mins} minutes (anti-spam).")
            except Exception:
                await message.channel.send("Failed to apply timeout (missing permissions?)")
            # clear history for that user
            recent_messages[gid][mid] = []
            # delete message(s)
            try:
                await message.delete()
            except Exception:
                pass
            return
    # process commands (discord.py builtin message handling is disabled for slash-only, but we keep on_message to monitor content)
    await bot.process_commands(message)

# ---------- Slash commands (app_commands) ----------

def mod_check(interaction: discord.Interaction):
    # require manage_messages or administrator
    if not interaction.user.guild_permissions.manage_messages and not interaction.user.guild_permissions.administrator:
        raise app_commands.AppCommandError("You must have Manage Messages or Administrator permissions to use this command.")
    return True

# Ban
@bot.tree.command(name='ban', description='Ban a user')
@app_commands.describe(member='Member to ban', reason='Reason for ban')
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = 'No reason provided'):
    try:
        await interaction.response.defer()
        await member.ban(reason=reason)
        await interaction.followup.send(f'{member} has been banned. Reason: {reason}')
    except Exception as e:
        await interaction.followup.send('Could not ban user: ' + str(e))

# Unban
@bot.tree.command(name='unban', description='Unban a user by ID')
@app_commands.describe(user_id='User ID to unban')
async def unban(interaction: discord.Interaction, user_id: str):
    try:
        await interaction.response.defer()
        user = await bot.fetch_user(int(user_id))
        await interaction.guild.unban(user)
        await interaction.followup.send(f'Unbanned {user}.')
    except Exception as e:
        await interaction.followup.send('Could not unban: ' + str(e))

# Timeout
@bot.tree.command(name='timeout', description='Timeout a member for X minutes')
@app_commands.describe(member='Member to timeout', minutes='Minutes to timeout', reason='Reason')
async def timeout(interaction: discord.Interaction, member: discord.Member, minutes: int = 5, reason: str = 'No reason provided'):
    try:
        await interaction.response.defer()
        until = datetime.utcnow() + timedelta(minutes=minutes)
        await member.edit(timed_out_until=until)
        await interaction.followup.send(f'{member.mention} timed out for {minutes} minutes. Reason: {reason}')
    except Exception as e:
        await interaction.followup.send('Could not timeout: ' + str(e))

# Untimeout
@bot.tree.command(name='untimeout', description='Remove timeout from a member')
@app_commands.describe(member='Member to remove timeout')
async def untimeout(interaction: discord.Interaction, member: discord.Member):
    try:
        await interaction.response.defer()
        await member.edit(timed_out_until=None)
        await interaction.followup.send(f'{member.mention} is no longer timed out.')
    except Exception as e:
        await interaction.followup.send('Could not remove timeout: ' + str(e))

# Warn
@bot.tree.command(name='warn', description='Add a warning to a user')
@app_commands.describe(member='Member to warn', reason='Reason for warn')
async def warn_cmd(interaction: discord.Interaction, member: discord.Member, reason: str):
    try:
        await interaction.response.defer()
        add_warn(interaction.guild.id, member.id, reason, interaction.user.id)
        await interaction.followup.send(f'{member.mention} has been warned. Reason: {reason}')
    except Exception as e:
        await interaction.followup.send('Could not add warn: ' + str(e))

# Unwarn - remove by index (0-based shown to moderator)
@bot.tree.command(name='unwarn', description='Remove a warn by index (0-based)')
@app_commands.describe(member='Member', index='Index of warn to remove (0-based)')
async def unwarn(interaction: discord.Interaction, member: discord.Member, index: int):
    try:
        await interaction.response.defer()
        ok = remove_warn(interaction.guild.id, member.id, index)
        if ok:
            await interaction.followup.send('Warning removed.')
        else:
            await interaction.followup.send('Could not remove warning (invalid index).')
    except Exception as e:
        await interaction.followup.send('Error: ' + str(e))

# Viewwarns
@bot.tree.command(name='viewwarns', description='View warns for a member')
@app_commands.describe(member='Member to view warns for')
async def viewwarns(interaction: discord.Interaction, member: discord.Member):
    try:
        await interaction.response.defer()
        g = str(interaction.guild.id)
        m = str(member.id)
        if g in warns and m in warns[g] and warns[g][m]:
            items = warns[g][m]
            lines = []
            for i, w in enumerate(items):
                lines.append(f"{i}: {w['time']} by <@{w['moderator']}> — {w['reason']}")
            await interaction.followup.send('\n'.join(lines))
        else:
            await interaction.followup.send('No warnings for that member.')
    except Exception as e:
        await interaction.followup.send('Error: ' + str(e))

# DM single user
@bot.tree.command(name='dm', description='Send a DM to a user')
@app_commands.describe(member='Member to DM', message='Message content')
async def dm(interaction: discord.Interaction, member: discord.Member, message: str):
    try:
        await interaction.response.defer()
        await member.send(message)
        await interaction.followup.send('DM sent.')
    except Exception as e:
        await interaction.followup.send('Could not send DM: ' + str(e))

# DM everyone (careful) — this will spawn a background task but supports cancellation via dmeveryonestop
@bot.tree.command(name='dmeveryone', description='DM every member (admins only)')
@app_commands.describe(message='Message to send to everyone')
async def dmeveryone(interaction: discord.Interaction, message: str):
    # permission check
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message('Administrator permission required.')
        return
    await interaction.response.defer()
    gid = str(interaction.guild.id)
    ensure_guild_features(interaction.guild.id)
    cancel_token = str(datetime.utcnow().timestamp())
    features[gid].setdefault('dm_cancel_tokens', {})[cancel_token] = False
    save_json(FEATURE_FILE, features)

    async def sender_task():
        try:
            for member in interaction.guild.members:
                if features[gid].get('dm_cancel_tokens', {}).get(cancel_token):
                    # canceled
                    break
                if member.bot:
                    continue
                try:
                    await member.send(message)
                except Exception:
                    pass
                await asyncio.sleep(0.3)  # small delay to avoid rate limits
        finally:
            # clean token
            features[gid].get('dm_cancel_tokens', {}).pop(cancel_token, None)
            save_json(FEATURE_FILE, features)

    bot.loop.create_task(sender_task())
    await interaction.followup.send('Started DMeveryone task. Use /dmeveryonestop to cancel.')

@bot.tree.command(name='dmeveryonestop', description='Stop active DMeveryone task')
async def dmeveryonestop(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message('Administrator permission required.')
        return
    gid = str(interaction.guild.id)
    ensure_guild_features(interaction.guild.id)
    # set all tokens to True (cancel)
    for t in list(features[gid].get('dm_cancel_tokens', {}).keys()):
        features[gid]['dm_cancel_tokens'][t] = True
    save_json(FEATURE_FILE, features)
    await interaction.response.send_message('Requested cancellation of any active DMeveryone tasks.')

# DM stop — cancel ongoing individual DM runs if you implement any long running ones; here it's alias
@bot.tree.command(name='dmstop', description='(Alias) Stop active DM tasks')
async def dmstop(interaction: discord.Interaction):
    await dmeveryonestop(interaction)

# Feature enable/disable
@bot.tree.command(name='feature', description='Enable or disable a named feature')
@app_commands.describe(action='enable or disable', feature='Feature name')
async def feature(interaction: discord.Interaction, action: str, feature: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message('Administrator permission required.')
        return
    await interaction.response.defer()
    gid = str(interaction.guild.id)
    ensure_guild_features(interaction.guild.id)
    feat = feature.lower()
    if action.lower() == 'enable':
        features[gid][feat] = True
        save_json(FEATURE_FILE, features)
        await interaction.followup.send(f'Feature {feat} enabled.')
    elif action.lower() == 'disable':
        features[gid][feat] = False
        save_json(FEATURE_FILE, features)
        await interaction.followup.send(f'Feature {feat} disabled.')
    else:
        await interaction.followup.send('Action must be enable or disable.')

# Set timeout values for features
@bot.tree.command(name='settimeout', description='Set timeout minutes for a feature (anti_cursing / anti_spam)')
@app_commands.describe(feature='Feature name', minutes='Minutes')
async def settimeout(interaction: discord.Interaction, feature: str, minutes: int):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message('Administrator permission required.')
        return
    await interaction.response.defer()
    gid = str(interaction.guild.id)
    ensure_guild_features(interaction.guild.id)
    if feature.lower() == 'anti_cursing':
        features[gid]['anti_cursing_timeout_mins'] = minutes
        save_json(FEATURE_FILE, features)
        await interaction.followup.send(f'anti_cursing timeout set to {minutes} minutes')
    elif feature.lower() == 'anti_spam':
        features[gid]['anti_spam_timeout_mins'] = minutes
        save_json(FEATURE_FILE, features)
        await interaction.followup.send(f'anti_spam timeout set to {minutes} minutes')
    else:
        await interaction.followup.send('Unknown feature. Use anti_cursing or anti_spam')

# Music group
music_group = app_commands.Group(name='music', description='Music commands')

@bot.tree.command(name='music', description='Music commands group - use subcommands')
async def music_root(interaction: discord.Interaction):
    await interaction.response.send_message('Use subcommands: play/skip/stop/queue')

@bot.tree.command(name='music_play', description='Play a YouTube link in your VC')
@app_commands.describe(url='YouTube URL')
async def music_play(interaction: discord.Interaction, url: str):
    # alias to music play
    await music_play_impl(interaction, url)

async def music_play_impl(interaction: discord.Interaction, url: str):
    await interaction.response.defer()
    if not interaction.user.voice or not interaction.user.voice.channel:
        await interaction.followup.send('You must be in a voice channel.')
        return
    voice_channel = interaction.user.voice.channel
    gid = interaction.guild.id
    player = music_players.get(gid)
    if not player:
        player = MusicPlayer(gid)
        music_players[gid] = player
    try:
        vc = await player.connect(voice_channel)
    except Exception as e:
        await interaction.followup.send('Could not connect to VC: ' + str(e))
        return
    # create ffmpeg source via yt-dlp
    try:
        source = await create_ffmpeg_source(url)
    except Exception as e:
        await interaction.followup.send('Could not extract audio: ' + str(e))
        return
    await player.add_song(source)
    await interaction.followup.send('Added to queue.')

@bot.tree.command(name='music_skip', description='Skip current song')
async def music_skip(interaction: discord.Interaction):
    gid = interaction.guild.id
    player = music_players.get(gid)
    if not player or not player.voice_client:
        await interaction.response.send_message('Nothing playing.')
        return
    # stop current playback to move to next
    player.voice_client.stop()
    await interaction.response.send_message('Skipped current track.')

@bot.tree.command(name='music_stop', description='Stop playback and clear queue')
async def music_stop(interaction: discord.Interaction):
    gid = interaction.guild.id
    player = music_players.get(gid)
    if not player:
        await interaction.response.send_message('Nothing to stop.')
        return
    player.stopped = True
    # cancel queue
    while not player.queue.empty():
        try:
            player.queue.get_nowait()
        except Exception:
            break
    if player.voice_client and player.voice_client.is_connected():
        player.voice_client.stop()
        await player.voice_client.disconnect()
    music_players.pop(gid, None)
    await interaction.response.send_message('Stopped playback and cleared queue.')

@bot.tree.command(name='music_queue', description='Show queue length')
async def music_queue(interaction: discord.Interaction):
    gid = interaction.guild.id
    player = music_players.get(gid)
    if not player:
        await interaction.response.send_message('Queue is empty.')
        return
    qsize = player.queue.qsize()
    await interaction.response.send_message(f'Queue size: {qsize}')

# Run the bot
if __name__ == '__main__':
    if not TOKEN:
        print('DISCORD_TOKEN missing in .env')
    else:
        bot.run(TOKEN)
