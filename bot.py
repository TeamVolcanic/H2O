# NOTE: This patch adds a runtime fallback installer for the google.generativeai package.
# It tries to pip-install the package at startup if it's missing, then imports it.
import discord
from discord import app_commands
from discord.ext import commands
import os
import datetime
import asyncio
import json
from collections import deque
from dotenv import load_dotenv

# Runtime package installer helper
import importlib
import subprocess
import sys
import time

def try_runtime_install(package_name: str, max_attempts: int = 1, pause_seconds: int = 2) -> bool:
    """
    Try to install `package_name` at runtime using pip.
    Returns True on success, False on failure.
    """
    for attempt in range(1, max_attempts + 1):
        print(f"📦 Attempt {attempt}/{max_attempts} to install '{package_name}' via pip...")
        cmd = [sys.executable, "-m", "pip", "install", "--no-cache-dir", package_name]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            print("pip stdout:")
            print(proc.stdout)
            print("pip stderr:")
            print(proc.stderr)
            if proc.returncode == 0:
                print(f"✅ Successfully installed {package_name}.")
                # Ensure import caches are refreshed
                importlib.invalidate_caches()
                return True
            else:
                print(f"❌ pip returned exit code {proc.returncode} attempting to install {package_name}.")
        except Exception as e:
            print(f"❌ Exception while trying to pip install {package_name}: {e}")
        if attempt < max_attempts:
            time.sleep(pause_seconds)
    return False

# Try to import Gemini client, but allow the bot to run without it.
AI_AVAILABLE = False
genai = None
GenerationConfig = None
APIError = Exception

# Preferred PyPI package name
PYPI_PACKAGE_NAME = "google-generative-ai"

try:
    import google.generativeai as genai
    from google.generativeai.types import GenerationConfig
    from google.generativeai.errors import APIError
    AI_AVAILABLE = True
except ModuleNotFoundError:
    print("⚠️ google.generativeai not installed at build time.")
    # Try runtime install as a fallback (best-effort)
    installed = try_runtime_install(PYPI_PACKAGE_NAME, max_attempts=1)
    if installed:
        try:
            import google.generativeai as genai
            from google.generativeai.types import GenerationConfig
            from google.generativeai.errors import APIError
            AI_AVAILABLE = True
        except Exception as e:
            print(f"⚠️ Failed to import google.generativeai after runtime install: {e}")
    else:
        # Try alternate package name (if upstream renamed package)
        alt_name = "google-generativeai"
        print(f"⚠️ Runtime install of '{PYPI_PACKAGE_NAME}' failed; trying alternate name '{alt_name}'...")
        installed_alt = try_runtime_install(alt_name, max_attempts=1)
        if installed_alt:
            try:
                import google.generativeai as genai
                from google.generativeai.types import GenerationConfig
                from google.generativeai.errors import APIError
                AI_AVAILABLE = True
            except Exception as e:
                print(f"⚠️ Failed to import google.generativeai after installing alternate package name: {e}")

if not AI_AVAILABLE:
    print("❌ AI features are disabled for this run. The bot will still start but AI won't be available.")

# --- Configuration & Setup ---
load_dotenv()

# The Gemini key is read from env var GEMINI_API_KEY
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Initialize Gemini Client only if library + key are present
GEMINI_MODEL = "gemini-2.5-flash"
if AI_AVAILABLE and GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        print(f"✅ Gemini client configured with {GEMINI_MODEL}.")
    except Exception as e:
        print(f"❌ Failed to configure Gemini client: {e}")
else:
    if not AI_AVAILABLE:
        print("❌ GEMINI client library missing: AI features are disabled.")
    elif not GEMINI_API_KEY:
        print("❌ GEMINI_API_KEY not found: AI features are disabled.")

# Configuration for the Discord Bot
intents = discord.Intents.default()
intents.message_content = True  # Required to read message content for chat
intents.members = True  # Required for member checks
bot = commands.Bot(command_prefix="!", intents=intents)

# --- Bot Feature Variables ---
VERIFY_ROLE_NAME = "🧑︱Member"
COMMANDS_DATA_FILE = "commands_data.json"
TICKETS_DATA_FILE = "tickets_data.json"
TICKET_CATEGORY_NAME = "Tickets"

AI_SYSTEM_INSTRUCTION = (
    "You are an engaging, detailed, and creative assistant. "
    "Your response must *always* be presented as a single, comprehensive paragraph or summary. "
    "Crucially, you must heavily incorporate relevant and descriptive emojis throughout the text "
    "to make the message visually appealing and expressive. "
    "Do not include any prefaces, attribution, or headers like 'AI generated', 'Generated by User', or 'Here is your summary'."
)

# --- Gemini API Helper Function ---

async def chat_with_ai(messages: list):
    """
    Sends a list of messages to the Gemini API with the required system instruction.
    Returns an informative string if AI is disabled.
    """
    if not AI_AVAILABLE:
        return "Sorry, AI features are currently disabled on this deployment (missing package). 🤖"

    if not GEMINI_API_KEY:
        return "Sorry, AI features are currently disabled due to a missing GEMINI_API_KEY. 🔒"

    # Configure generation with the system instruction
    config = GenerationConfig(system_instruction=AI_SYSTEM_INSTRUCTION)

    try:
        response = await asyncio.to_thread(
            genai.client.models.generate_content,
            model=GEMINI_MODEL,
            contents=messages,
            config=config,
        )

        if getattr(response, "text", None):
            return response.text
        elif getattr(response, "candidates", None) and response.candidates:
            candidate = response.candidates[0]
            if getattr(candidate, "safety_ratings", None):
                return "🛡️ Your request was blocked due to safety concerns. Please try a different query. 🚫"
            return getattr(candidate, "content", "🤖 The AI returned an unexpected response.")
        else:
            return "🤖 I received an empty or malformed response from the AI. Please try again. 🛠️"

    except APIError as e:
        print(f"Gemini API Error: {e}")
        return f"🚨 I ran into an issue communicating with the AI model: {e} 💔"
    except Exception as e:
        print(f"General AI Error: {e}")
        return f"Oops! An unexpected error occurred while processing your request: {e} 🐛"

# --- Discord Event Handlers and Commands ---
# (rest of your existing commands and events go here, unchanged)
# For brevity this patch retains the rest of your logic but preserves AI checks above.

@bot.event
async def on_ready():
    print(f'✨ Logged in as {bot.user} (ID: {bot.user.id})')
    try:
        synced = await bot.tree.sync()
        print(f"🤖 Synced {len(synced)} command(s) globally.")
    except Exception as e:
        print(f"❌ Failed to sync commands on ready: {e}")

# ... include the rest of your commands and on_message implementation unchanged ...

# --- Bot Runner ---
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")

if not DISCORD_TOKEN:
    print("FATAL ERROR: DISCORD_TOKEN environment variable is not set. The bot cannot start.")
else:
    try:
        bot.run(DISCORD_TOKEN)
    except discord.errors.LoginFailure:
        print("FATAL ERROR: Invalid Discord token provided. Please check your DISCORD_TOKEN.")
    except Exception as e:
        print(f"An unexpected error occurred during bot execution: {e}")
