import discord
from discord.ext import commands
import os
import datetime
import re
import asyncio
import json
from collections import deque
from dotenv import load_dotenv
from openai import AsyncOpenAI
from google import genai
from google.genai import types
import io

load_dotenv()

BOT_PREFIX = "!"
VERIFY_ROLE_NAME = "🧑︱Member"
COMMANDS_DATA_FILE = "commands_data.json"

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
MAX_DM_PER_WARN = 5
DM_DELAY = 0.5
MAX_EMBED_LENGTH = 4096

user_messages = {}
active_dm_tasks = {}
user_warnings = {}
user_dm_limits = {}
prompt_messages = {}

BAD_WORDS_PATTERN = re.compile(
    r'(' + '|'.join(re.escape(word) for word in BAD_WORDS) + r')',
    re.IGNORECASE
)

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix=BOT_PREFIX, intents=intents)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

def load_data():
    global prompt_messages
    try:
        with open(COMMANDS_DATA_FILE, 'r') as f:
            data = json.load(f)
            prompt_messages = {int(k): {int(mk): mv for mk, mv in v.items()} for k, v in data.items()}
    except FileNotFoundError:
        print("No command data file found. Starting fresh.")
    except Exception as e:
        print(f"Error loading command data: {e}")

def save_data():
    try:
        data_to_save = {str(k): {str(mk): mv for mk, mv in v.items()} for k, v in prompt_messages.items()}
        with open(COMMANDS_DATA_FILE, 'w') as f:
            json.dump(data_to_save, f, indent=4)
    except Exception as e:
        print(f"Error saving command data: {e}")

@bot.event
async def on_ready():
    bot.start_time = discord.utils.utcnow()
    print(f'Bot is ready. Logged in as: {bot.user}')
    print(f'Bot ID: {bot.user.id}')
    print(f'Connected to {len(bot.guilds)} guild(s)')
    if not openai_client:
        print("⚠️  Warning: OPENAI_API_KEY not found. AI commands will not work.")
    load_data()
    print("Command data loaded.")

@bot.event
async def on_command_error(ctx, error):
    if hasattr(ctx.command, 'on_error'):
        return

    if isinstance(error, commands.CommandNotFound):
        if ctx.message.content.startswith(BOT_PREFIX):
            embed = discord.Embed(
                title="❌ Unknown Command",
                description=f"The command `{ctx.invoked_with}` is not a valid command. Please check your spelling or use `{BOT_PREFIX}help` for a list of available commands.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed, delete_after=10)
        return

    elif isinstance(error, commands.MissingPermissions):
        embed = discord.Embed(
            title="❌ Permission Denied",
            description=f"You need the following permission(s) to use this command: **{', '.join(error.missing_permissions)}**.",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
    elif isinstance(error, commands.BadArgument) or isinstance(error, commands.MissingRequiredArgument):
        embed = discord.Embed(
            title="❌ Invalid Usage",
            description=f"You used the command incorrectly. Please check the required arguments.",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed, delete_after=10)
    else:
        print(f"Unhandled error in command {ctx.command}: {error}")
        error_embed = discord.Embed(
            title="💥 An Unexpected Error Occurred",
            description=f"Error: `{error}`",
            color=discord.Color.red()
        )
        await ctx.send(embed=error_embed, delete_after=15)

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if FEATURE_STATUS.get('cursing') and message.guild:
        if BAD_WORDS_PATTERN.search(message.content):
            try:
                await message.delete()
                embed = discord.Embed(
                    title="⚠️ Inappropriate Language Detected",
                    description=f"{message.author.mention}, please keep the chat clean and respectful.",
                    color=discord.Color.orange()
                )
                bot_name = bot.user.name if bot.user else "Bot"
                embed.set_footer(text=f"Message deleted by {bot_name}")
                await message.channel.send(embed=embed, delete_after=5)

                timeout_until = discord.utils.utcnow() + CURSING_TIMEOUT_DURATION
                await message.author.timeout(timeout_until, reason="Using inappropriate language")

                dm_embed = discord.Embed(
                    title="🚫 Timeout Notice",
                    description=f"You have been timed out in **{message.guild.name}** for using inappropriate language.",
                    color=discord.Color.red()
                )
                dm_embed.add_field(
                    name="Duration",
                    value=f"{CURSING_TIMEOUT_DURATION.seconds // 60} minutes",
                    inline=False
                )
                try:
                    await message.author.send(embed=dm_embed)
                except:
                    pass
            except Exception as e:
                print(f"Error handling bad words: {e}")

    if FEATURE_STATUS.get('spamming') and message.guild:
        user_id = message.author.id
        current_time = datetime.datetime.now()

        if user_id not in user_messages:
            user_messages[user_id] = deque(maxlen=SPAM_THRESHOLD)

        user_messages[user_id].append(current_time)

        if len(user_messages[user_id]) == SPAM_THRESHOLD:
            time_diff = (current_time - user_messages[user_id][0]).total_seconds()

            if time_diff <= SPAM_COOLDOWN:
                try:
                    timeout_until = discord.utils.utcnow() + SPAM_TIMEOUT_DURATION
                    await message.author.timeout(timeout_until, reason="Spamming messages")

                    embed = discord.Embed(
                        title="🚫 Anti-Spam Protection",
                        description=f"{message.author.mention} has been timed out for spamming.",
                        color=discord.Color.red()
                    )
                    embed.add_field(
                        name="Duration",
                        value=f"{SPAM_TIMEOUT_DURATION.seconds // 60} minutes",
                        inline=False
                    )
                    await message.channel.send(embed=embed, delete_after=10)

                    user_messages[user_id].clear()
                except Exception as e:
                    print(f"Error handling spam: {e}")

    await bot.process_commands(message)

@bot.group(invoke_without_command=True, aliases=['ai'])
async def aicommand(ctx):
    if ctx.invoked_subcommand is None:
        usage_embed = discord.Embed(
            title="🤖 AI Commands Group",
            description="Use one of the following subcommands for AI interactions:",
            color=discord.Color.blue()
        )
        usage_embed.add_field(name="`!ask <question>`", value="Get a simple, factual answer.", inline=False)
        usage_embed.add_field(name="`!generate <prompt>`", value="Generate creative text (Admin only).", inline=False)
        usage_embed.add_field(name="`!prompt <prompt>`", value="Get a structured AI response (Admin only).", inline=False)
        usage_embed.add_field(name="`!aiedit <message_id> <new_prompt>`", value="Edit a previous AI response (Admin only).", inline=False)
        await ctx.send(embed=usage_embed)

def _check_ai_config(ctx):
    if not openai_client and not gemini_client:
        error_embed = discord.Embed(
            title="❌ Configuration Error",
            description="Neither OpenAI nor Gemini API key is configured. Please contact the bot administrator.",
            color=discord.Color.red()
        )
        asyncio.create_task(ctx.send(embed=error_embed))
        return False
    return True

async def _send_and_store_ai_response(ctx, prompt, ai_type, max_tokens, temperature):
    if not _check_ai_config(ctx):
        return

    async with ctx.typing():
        answer = None
        ai_provider = None

        if openai_client:
            try:
                response = await openai_client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "user", "content": prompt}
                    ],
                    max_tokens=max_tokens,
                    temperature=temperature
                )
                answer = response.choices[0].message.content or "No response generated"
                ai_provider = "OpenAI"
            except Exception as e:
                print(f"OpenAI API Error: {e}")
                if gemini_client:
                    print("Falling back to Gemini...")
                else:
                    error_embed = discord.Embed(
                        title="❌ Error",
                        description=f"Failed to generate response with OpenAI: {str(e)}",
                        color=discord.Color.red()
                    )
                    await ctx.send(embed=error_embed)
                    return

        if not answer and gemini_client:
            try:
                response = gemini_client.models.generate_content(
                    model="gemini-2.0-flash-exp",
                    contents=[prompt]
                )
                answer = response.text or "No response generated"
                ai_provider = "Gemini"
            except Exception as e:
                error_embed = discord.Embed(
                    title="❌ Error",
                    description=f"Failed to generate response with Gemini: {str(e)}",
                    color=discord.Color.red()
                )
                await ctx.send(embed=error_embed)
                print(f"Gemini API Error: {e}")
                return

        if not answer:
            return

        was_truncated = False
        if len(answer) > MAX_EMBED_LENGTH - 100:
            answer = answer[:MAX_EMBED_LENGTH - 103] + "..."
            was_truncated = True

        if ai_type == 'ask':
            color = discord.Color.blue()
        elif ai_type == 'generate':
            color = discord.Color.purple()
        elif ai_type == 'prompt':
            color = discord.Color.green()
        else:
            color = discord.Color.default()

        embed = discord.Embed(
            description=answer,
            color=color
        )
        embed.set_footer(text=f"Prompted by {ctx.author.display_name} • {ai_provider}")

        if was_truncated:
            embed.add_field(
                name="⚠️ Note",
                value="Response was truncated due to length limit",
                inline=False
            )

        response_msg = await ctx.send(embed=embed)

        channel_id = ctx.channel.id
        message_id = response_msg.id

        if channel_id not in prompt_messages:
            prompt_messages[channel_id] = {}

        prompt_messages[channel_id][message_id] = {
            'type': ai_type,
            'user_id': ctx.author.id,
            'prompt': prompt
        }
        save_data()

        return response_msg

@aicommand.command(name='ask')
async def ask_command(ctx, *, question: str = ""):
    if not question:
        usage_embed = discord.Embed(
            title="❓ Ask Command Usage",
            description="Please provide a question after the command.",
            color=discord.Color.blue()
        )
        usage_embed.add_field(
            name="Format",
            value="`!ask <your question>`",
            inline=False
        )
        usage_embed.add_field(
            name="Example",
            value="`!ask What is the capital of France?`",
            inline=False
        )
        await ctx.send(embed=usage_embed)
        return

    await _send_and_store_ai_response(ctx, question, 'ask', 500, 0.7)

@aicommand.command(name='generate')
@commands.has_permissions(administrator=True)
async def generate_command(ctx, *, prompt: str = ""):
    if not prompt:
        usage_embed = discord.Embed(
            title="✨ Generate Command Usage",
            description="Please provide a creative prompt after the command.",
            color=discord.Color.purple()
        )
        usage_embed.add_field(
            name="Format",
            value="`!generate <your creative prompt>`",
            inline=False
        )
        usage_embed.add_field(
            name="Example",
            value="`!generate Write a short poem about coding`",
            inline=False
        )
        await ctx.send(embed=usage_embed)
        return

    await _send_and_store_ai_response(ctx, prompt, 'generate', 800, 0.9)

@aicommand.command(name='prompt')
@commands.has_permissions(administrator=True)
async def prompt_command(ctx, *, user_prompt: str = ""):
    if not user_prompt:
        usage_embed = discord.Embed(
            title="💭 Prompt Command Usage",
            description="Please provide a prompt after the command.",
            color=discord.Color.green()
        )
        usage_embed.add_field(
            name="Format",
            value="`!prompt <your prompt>`",
            inline=False
        )
        usage_embed.add_field(
            name="Example",
            value="`!prompt Explain quantum computing in simple terms`",
            inline=False
        )
        await ctx.send(embed=usage_embed)
        return

    response_msg = await _send_and_store_ai_response(ctx, user_prompt, 'prompt', 600, 0.8)

    if response_msg:
        try:
            await ctx.message.delete()
        except:
            pass

@bot.command(name='imagegenerate', aliases=['genimage', 'imagegen'])
@commands.has_permissions(administrator=True)
async def imagegenerate_command(ctx, *, prompt: str = ""):
    if not gemini_client:
        error_embed = discord.Embed(
            title="❌ Configuration Error",
            description="Gemini API key is not configured. Please contact the bot administrator.",
            color=discord.Color.red()
        )
        await ctx.send(embed=error_embed)
        return

    if not prompt:
        usage_embed = discord.Embed(
            title="🎨 Image Generate Command Usage",
            description="Generate AI images using Google Gemini",
            color=discord.Color.purple()
        )
        usage_embed.add_field(
            name="Format",
            value="`!imagegenerate <description>`",
            inline=False
        )
        usage_embed.add_field(
            name="Example",
            value="`!imagegenerate A futuristic cityscape at sunset with flying cars`",
            inline=False
        )
        usage_embed.set_footer(text="Admin only • Powered by Google Gemini")
        await ctx.send(embed=usage_embed)
        return

    async with ctx.typing():
        try:
            response = gemini_client.models.generate_images(
                model='imagen-3.0-generate-001',
                prompt=prompt,
                config={
                    'number_of_images': 1,
                    'aspect_ratio': '1:1',
                    'safety_filter_level': 'block_some',
                    'person_generation': 'allow_all'
                }
            )

            if response.generated_images:
                generated_image = response.generated_images[0].image

                img_byte_arr = io.BytesIO()
                generated_image.save(img_byte_arr, format='PNG')
                img_byte_arr.seek(0)

                file = discord.File(img_byte_arr, filename="generated_image.png")

                embed = discord.Embed(
                    description=f"**Prompt:** {prompt}",
                    color=discord.Color.purple()
                )
                embed.set_image(url="attachment://generated_image.png")
                embed.set_footer(text=f"Generated by {ctx.author.display_name} • Google Imagen")

                await ctx.send(embed=embed, file=file)

        except Exception as e:
            error_embed = discord.Embed(
                title="❌ Image Generation Failed",
                description=f"Failed to generate image: {str(e)}",
                color=discord.Color.red()
            )
            await ctx.send(embed=error_embed)
            print(f"Gemini Error: {e}")

async def _edit_ai_response(ctx, message_id: int, new_prompt: str, ai_type: str, max_tokens: int, temperature: float):
    if not _check_ai_config(ctx):
        return

    try:
        original_msg = await ctx.channel.fetch_message(message_id)
    except discord.NotFound:
        embed = discord.Embed(title="❌ Message Not Found", description="The provided message ID is invalid or the message was deleted.", color=discord.Color.red())
        await ctx.send(embed=embed, delete_after=10)
        return
    except Exception as e:
        embed = discord.Embed(title="❌ Error Fetching Message", description=f"An error occurred: {e}", color=discord.Color.red())
        await ctx.send(embed=embed, delete_after=10)
        return

    channel_id = ctx.channel.id
    if channel_id not in prompt_messages or message_id not in prompt_messages[channel_id]:
        embed = discord.Embed(title="❌ Not an AI Response", description="This message was not generated by a recognizable AI command or the data was lost.", color=discord.Color.red())
        await ctx.send(embed=embed, delete_after=10)
        return

    message_data = prompt_messages[channel_id][message_id]

    if ai_type not in ['aiedit'] and message_data['type'] != ai_type:
        embed = discord.Embed(title="❌ Invalid Edit Command", description=f"This message was generated with `!{message_data['type']}`. Use `!{message_data['type']}edit` or `!aiedit` to modify it.", color=discord.Color.red())
        await ctx.send(embed=embed, delete_after=10)
        return

    if not (ctx.author.id == message_data['user_id'] or ctx.author.guild_permissions.administrator):
        embed = discord.Embed(title="❌ Permission Denied", description="You can only edit your own AI responses, or you must have Administrator permissions.", color=discord.Color.red())
        await ctx.send(embed=embed, delete_after=10)
        return

    loading_embed = discord.Embed(
        title="🔄 Editing AI Response...",
        description=f"Processing new prompt: *{new_prompt[:100]}...*",
        color=discord.Color.orange()
    )
    await original_msg.edit(embed=loading_embed)

    new_answer = None
    ai_provider = None

    if openai_client:
        try:
            response = await openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "user", "content": new_prompt}
                ],
                max_tokens=max_tokens,
                temperature=temperature
            )
            new_answer = response.choices[0].message.content or "No response generated"
            ai_provider = "OpenAI"
        except Exception as e:
            print(f"OpenAI API Error in edit: {e}")
            if not gemini_client:
                error_embed = discord.Embed(
                    title="❌ Error During Edit",
                    description=f"Failed to regenerate response: {str(e)}",
                    color=discord.Color.red()
                )
                await original_msg.edit(embed=error_embed)
                return

    if not new_answer and gemini_client:
        try:
            response = gemini_client.models.generate_content(
                model="gemini-2.0-flash-exp",
                contents=[new_prompt]
            )
            new_answer = response.text or "No response generated"
            ai_provider = "Gemini"
        except Exception as e:
            error_embed = discord.Embed(
                title="❌ Error During Edit",
                description=f"Failed to regenerate response: {str(e)}",
                color=discord.Color.red()
            )
            await original_msg.edit(embed=error_embed)
            print(f"Gemini API Error in edit: {e}")
            return

    if not new_answer:
        return

    was_truncated = False
    if len(new_answer) > MAX_EMBED_LENGTH - 100:
        new_answer = new_answer[:MAX_EMBED_LENGTH - 103] + "..."
        was_truncated = True

    original_type = message_data['type']
    if original_type == 'ask':
        color = discord.Color.blue()
    elif original_type == 'generate':
        color = discord.Color.purple()
    elif original_type == 'prompt':
        color = discord.Color.green()
    else:
        color = discord.Color.default()

    final_embed = discord.Embed(
        description=new_answer,
        color=color
    )
    final_embed.set_footer(text=f"Edited by {ctx.author.display_name} • {ai_provider}")

    if was_truncated:
        final_embed.add_field(
            name="⚠️ Note",
            value="Response was truncated due to length limit",
            inline=False
        )

    await original_msg.edit(embed=final_embed)

    prompt_messages[channel_id][message_id]['prompt'] = new_prompt
    save_data()

    await ctx.send(f"✅ AI response (ID: `{message_id}`) successfully edited.", delete_after=5)
    try:
        await ctx.message.delete()
    except:
        pass

@aicommand.command(name='aiedit')
@commands.has_permissions(administrator=True)
async def aiedit_command(ctx, message_id: int = None, *, new_prompt: str = None):
    if not message_id or not new_prompt:
        usage_embed = discord.Embed(
            title="📝 AIEdit Command Usage",
            description="Allows an admin to regenerate *any* AI response with a new prompt.",
            color=discord.Color.red()
        )
        usage_embed.add_field(name="Format", value="`!aiedit <message_id> <new_prompt>`", inline=False)
        usage_embed.add_field(name="Example", value="`!aiedit 123456789012345678 Write a new response about bots`", inline=False)
        await ctx.send(embed=usage_embed)
        return

    await _edit_ai_response(ctx, message_id, new_prompt, 'aiedit', 800, 0.9)

@aicommand.command(name='promptedit')
@commands.has_permissions(administrator=True)
async def promptedit_command(ctx, message_id: int = None, *, new_prompt: str = None):
    if not message_id or not new_prompt:
        usage_embed = discord.Embed(
            title="📝 Prompt Edit Command Usage",
            description="Re-generates a `!prompt` response with a new prompt.",
            color=discord.Color.green()
        )
        usage_embed.add_field(name="Format", value="`!promptedit <message_id> <new_prompt>`", inline=False)
        usage_embed.add_field(name="Example", value="`!promptedit 123456789012345678 New prompt text`", inline=False)
        await ctx.send(embed=usage_embed)
        return

    await _edit_ai_response(ctx, message_id, new_prompt, 'prompt', 600, 0.8)

@aicommand.command(name='generateedit')
@commands.has_permissions(administrator=True)
async def generateedit_command(ctx, message_id: int = None, *, new_prompt: str = None):
    if not message_id or not new_prompt:
        usage_embed = discord.Embed(
            title="📝 Generate Edit Command Usage",
            description="Re-generates a `!generate` response with a new prompt.",
            color=discord.Color.purple()
        )
        usage_embed.add_field(name="Format", value="`!generateedit <message_id> <new_prompt>`", inline=False)
        usage_embed.add_field(name="Example", value="`!generateedit 123456789012345678 New creative prompt`", inline=False)
        await ctx.send(embed=usage_embed)
        return

    await _edit_ai_response(ctx, message_id, new_prompt, 'generate', 800, 0.9)

@bot.command(name='ask')
async def ask_standalone(ctx, *, question: str = ""):
    if not question:
        usage_embed = discord.Embed(
            title="❓ Ask Command Usage",
            description="Please provide a question after the command.",
            color=discord.Color.blue()
        )
        usage_embed.add_field(
            name="Format",
            value="`!ask <your question>`",
            inline=False
        )
        usage_embed.add_field(
            name="Example",
            value="`!ask What is the capital of France?`",
            inline=False
        )
        await ctx.send(embed=usage_embed)
        return
    await _send_and_store_ai_response(ctx, question, 'ask', 500, 0.7)

@bot.command(name='prompt')
@commands.has_permissions(administrator=True)
async def prompt_standalone(ctx, *, user_prompt: str = ""):
    if not user_prompt:
        usage_embed = discord.Embed(
            title="💭 Prompt Command Usage",
            description="Please provide a prompt after the command.",
            color=discord.Color.green()
        )
        usage_embed.add_field(
            name="Format",
            value="`!prompt <your prompt>`",
            inline=False
        )
        usage_embed.add_field(
            name="Example",
            value="`!prompt Explain quantum computing in simple terms`",
            inline=False
        )
        await ctx.send(embed=usage_embed)
        return
    response_msg = await _send_and_store_ai_response(ctx, user_prompt, 'prompt', 600, 0.8)
    if response_msg:
        try:
            await ctx.message.delete()
        except:
            pass

@bot.command(name='generate')
@commands.has_permissions(administrator=True)
async def generate_standalone(ctx, *, prompt: str = ""):
    if not prompt:
        usage_embed = discord.Embed(
            title="✨ Generate Command Usage",
            description="Please provide a creative prompt after the command.",
            color=discord.Color.purple()
        )
        usage_embed.add_field(
            name="Format",
            value="`!generate <your creative prompt>`",
            inline=False
        )
        usage_embed.add_field(
            name="Example",
            value="`!generate Write a short poem about coding`",
            inline=False
        )
        await ctx.send(embed=usage_embed)
        return
    await _send_and_store_ai_response(ctx, prompt, 'generate', 800, 0.9)
    try:
        await ctx.message.delete()
    except:
        pass

@bot.command(name='info', aliases=['stats'])
async def info_command(ctx):
    if not hasattr(bot, 'start_time'):
        await ctx.send("⏳ Bot is still starting up, please try again in a moment.")
        return

    uptime = discord.utils.utcnow() - bot.start_time
    hours, remainder = divmod(int(uptime.total_seconds()), 3600)
    minutes, seconds = divmod(remainder, 60)

    embed = discord.Embed(
        title="🤖 Discord Bot Information",
        description="A feature-rich moderation and AI assistant bot for Discord servers",
        color=discord.Color.blue()
    )

    embed.add_field(
        name="📊 Bot Stats",
        value=f"**Name:** {bot.user.name}\n**ID:** {bot.user.id}\n**Prefix:** `{BOT_PREFIX}`\n**Servers:** {len(bot.guilds)}\n**Uptime:** {hours}h {minutes}m {seconds}s",
        inline=False
    )

    embed.add_field(
        name="🛡️ Moderation",
        value="• Kick, Ban, Timeout, Warn members\n• Untimeout & Unban commands\n• View and clear warnings",
        inline=True
    )

    embed.add_field(
        name="🤖 AI Features",
        value="• Ask AI questions\n• Generate creative content\n• Generate AI images\n• Edit AI responses",
        inline=True
    )

    embed.add_field(
        name="🔒 Auto-Moderation",
        value="• Anti-cursing protection\n• Anti-spam detection\n• Automatic timeouts",
        inline=True
    )

    embed.add_field(
        name="👥 Utilities",
        value="• Member verification\n• Direct messaging\n• Feature management\n• Server announcements",
        inline=True
    )

    embed.add_field(
        name="📝 Useful Commands",
        value=f"`{BOT_PREFIX}help` - View all commands\n`{BOT_PREFIX}feature` - View features\n`{BOT_PREFIX}verify` - Get verified",
        inline=False
    )

    embed.set_footer(text=f"Discord.py v{discord.__version__} • Running 24/7")

    await ctx.send(embed=embed)

@bot.command(name='feature')
async def feature_command(ctx, action: str = None, feature_name: str = None):
    if action is None:
        embed = discord.Embed(
            title="⚙️ Bot Features Status",
            description="Current status of all bot features",
            color=discord.Color.green()
        )

        for feature, enabled in FEATURE_STATUS.items():
            status = "✅ Enabled" if enabled else "❌ Disabled"
            embed.add_field(name=f"**{feature.title()}**", value=status, inline=True)

        embed.add_field(
            name="📝 How to Use",
            value=f"`{BOT_PREFIX}feature enable <feature_name>` - Enable a feature\n`{BOT_PREFIX}feature disable <feature_name>` - Disable a feature",
            inline=False
        )

        embed.set_footer(text="Admin only for enable/disable actions")
        await ctx.send(embed=embed)
        return

    if action.lower() not in ['enable', 'disable']:
        await ctx.send(f"❌ Invalid action. Use `enable` or `disable`.")
        return

    if not feature_name:
        await ctx.send(f"❌ Please specify a feature name. Available: {', '.join(FEATURE_STATUS.keys())}")
        return

    if not ctx.author.guild_permissions.administrator:
        await ctx.send("❌ You need Administrator permissions to manage features.")
        return

    feature_name = feature_name.lower()
    if feature_name not in FEATURE_STATUS:
        await ctx.send(f"❌ Unknown feature. Available: {', '.join(FEATURE_STATUS.keys())}")
        return

    if action.lower() == 'enable':
        FEATURE_STATUS[feature_name] = True
        embed = discord.Embed(
            title="✅ Feature Enabled",
            description=f"The **{feature_name}** feature has been enabled.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)
    else:
        FEATURE_STATUS[feature_name] = False
        embed = discord.Embed(
            title="❌ Feature Disabled",
            description=f"The **{feature_name}** feature has been disabled.",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)

@bot.command(name='kick')
@commands.has_permissions(kick_members=True)
async def kick_command(ctx, member: discord.Member = None, *, reason: str = "No reason provided"):
    if not FEATURE_STATUS.get('kick'):
        await ctx.send("❌ This command is currently disabled.")
        return

    if not member:
        embed = discord.Embed(
            title="❌ Usage",
            description="`!kick @member [reason]`",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return

    if member.top_role >= ctx.author.top_role:
        await ctx.send("❌ You cannot kick someone with a higher or equal role.")
        return

    try:
        await member.kick(reason=reason)
        embed = discord.Embed(
            title="👢 Member Kicked",
            description=f"{member.mention} has been kicked from the server.",
            color=discord.Color.orange()
        )
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.set_footer(text=f"Kicked by {ctx.author.display_name}")
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"❌ Failed to kick member: {e}")

@bot.command(name='ban')
@commands.has_permissions(ban_members=True)
async def ban_command(ctx, member: discord.Member = None, *, reason: str = "No reason provided"):
    if not FEATURE_STATUS.get('ban'):
        await ctx.send("❌ This command is currently disabled.")
        return

    if not member:
        embed = discord.Embed(
            title="❌ Usage",
            description="`!ban @member [reason]`",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return

    if member.top_role >= ctx.author.top_role:
        await ctx.send("❌ You cannot ban someone with a higher or equal role.")
        return

    try:
        await member.ban(reason=reason)
        embed = discord.Embed(
            title="🔨 Member Banned",
            description=f"{member.mention} has been banned from the server.",
            color=discord.Color.red()
        )
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.set_footer(text=f"Banned by {ctx.author.display_name}")
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"❌ Failed to ban member: {e}")

@bot.command(name='timeout')
@commands.has_permissions(moderate_members=True)
async def timeout_command(ctx, member: discord.Member = None, duration: int = 10, *, reason: str = "No reason provided"):
    if not FEATURE_STATUS.get('timeout'):
        await ctx.send("❌ This command is currently disabled.")
        return

    if not member:
        embed = discord.Embed(
            title="❌ Usage",
            description="`!timeout @member [duration_in_minutes] [reason]`",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return

    if member.top_role >= ctx.author.top_role:
        await ctx.send("❌ You cannot timeout someone with a higher or equal role.")
        return

    try:
        timeout_until = discord.utils.utcnow() + datetime.timedelta(minutes=duration)
        await member.timeout(timeout_until, reason=reason)

        embed = discord.Embed(
            title="🔇 Member Timed Out",
            description=f"{member.mention} has been timed out for {duration} minutes.",
            color=discord.Color.orange()
        )
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.set_footer(text=f"Timed out by {ctx.author.display_name}")
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"❌ Failed to timeout member: {e}")

@bot.command(name='untimeout')
@commands.has_permissions(moderate_members=True)
async def untimeout_command(ctx, member: discord.Member = None):
    if not member:
        embed = discord.Embed(
            title="❌ Usage",
            description="`!untimeout @member`",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return

    try:
        await member.timeout(None, reason=f"Timeout removed by {ctx.author.display_name}")

        embed = discord.Embed(
            title="✅ Timeout Removed",
            description=f"Timeout has been removed from {member.mention}.",
            color=discord.Color.green()
        )
        embed.set_footer(text=f"Removed by {ctx.author.display_name}")
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"❌ Failed to remove timeout: {e}")

@bot.command(name='unban')
@commands.has_permissions(ban_members=True)
async def unban_command(ctx, user_id: str = None, *, reason: str = "No reason provided"):
    if not user_id:
        embed = discord.Embed(
            title="❌ Usage",
            description="`!unban <user_id> [reason]`",
            color=discord.Color.red()
        )
        embed.add_field(
            name="How to get User ID",
            value="Enable Developer Mode in Discord settings, right-click the user, and select 'Copy ID'",
            inline=False
        )
        await ctx.send(embed=embed)
        return

    try:
        user_id = int(user_id)
        user = await bot.fetch_user(user_id)
        await ctx.guild.unban(user, reason=reason)

        embed = discord.Embed(
            title="✅ User Unbanned",
            description=f"{user.mention} ({user.name}) has been unbanned from the server.",
            color=discord.Color.green()
        )
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.set_footer(text=f"Unbanned by {ctx.author.display_name}")
        await ctx.send(embed=embed)
    except ValueError:
        await ctx.send("❌ Invalid user ID. Please provide a valid numeric user ID.")
    except discord.NotFound:
        await ctx.send("❌ User not found or not banned.")
    except Exception as e:
        await ctx.send(f"❌ Failed to unban user: {e}")

@bot.command(name='warn')
@commands.has_permissions(kick_members=True)
async def warn_command(ctx, member: discord.Member = None, *, reason: str = "No reason provided"):
    if not FEATURE_STATUS.get('warn'):
        await ctx.send("❌ This command is currently disabled.")
        return

    if not member:
        embed = discord.Embed(
            title="❌ Usage",
            description="`!warn @member [reason]`",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return

    user_id = member.id
    if user_id not in user_warnings:
        user_warnings[user_id] = []

    warning_data = {
        'reason': reason,
        'warned_by': ctx.author.id,
        'timestamp': datetime.datetime.now().isoformat()
    }
    user_warnings[user_id].append(warning_data)

    total_warnings = len(user_warnings[user_id])

    embed = discord.Embed(
        title="⚠️ Member Warned",
        description=f"{member.mention} has been warned.",
        color=discord.Color.yellow()
    )
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.add_field(name="Total Warnings", value=str(total_warnings), inline=False)
    embed.set_footer(text=f"Warned by {ctx.author.display_name}")
    await ctx.send(embed=embed)

    try:
        dm_embed = discord.Embed(
            title="⚠️ Warning",
            description=f"You have been warned in **{ctx.guild.name}**.",
            color=discord.Color.yellow()
        )
        dm_embed.add_field(name="Reason", value=reason, inline=False)
        dm_embed.add_field(name="Total Warnings", value=str(total_warnings), inline=False)
        await member.send(embed=dm_embed)
    except:
        pass

@bot.command(name='warnings')
@commands.has_permissions(kick_members=True)
async def warnings_command(ctx, member: discord.Member = None):
    if not member:
        embed = discord.Embed(
            title="❌ Usage",
            description="`!warnings @member`",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return

    user_id = member.id
    if user_id not in user_warnings or not user_warnings[user_id]:
        await ctx.send(f"{member.mention} has no warnings.")
        return

    embed = discord.Embed(
        title=f"⚠️ Warnings for {member.display_name}",
        color=discord.Color.yellow()
    )

    for i, warning in enumerate(user_warnings[user_id], 1):
        warned_by = ctx.guild.get_member(warning['warned_by'])
        warned_by_name = warned_by.display_name if warned_by else "Unknown"
        timestamp = warning['timestamp'][:19]

        embed.add_field(
            name=f"Warning #{i}",
            value=f"**Reason:** {warning['reason']}\n**By:** {warned_by_name}\n**Date:** {timestamp}",
            inline=False
        )

    await ctx.send(embed=embed)

@bot.command(name='clearwarnings')
@commands.has_permissions(administrator=True)
async def clearwarnings_command(ctx, member: discord.Member = None):
    if not member:
        embed = discord.Embed(
            title="❌ Usage",
            description="`!clearwarnings @member`",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return

    user_id = member.id
    if user_id in user_warnings:
        user_warnings[user_id] = []
        await ctx.send(f"✅ All warnings cleared for {member.mention}.")
    else:
        await ctx.send(f"{member.mention} has no warnings to clear.")

@bot.command(name='verify')
async def verify_command(ctx):
    if not ctx.guild:
        await ctx.send("❌ This command can only be used in a server.")
        return

    role = discord.utils.get(ctx.guild.roles, name=VERIFY_ROLE_NAME)

    if not role:
        await ctx.send(f"❌ The verification role `{VERIFY_ROLE_NAME}` does not exist. Please contact an admin.")
        return

    if role in ctx.author.roles:
        await ctx.send(f"✅ You are already verified!")
        return

    try:
        await ctx.author.add_roles(role)
        embed = discord.Embed(
            title="✅ Verification Successful",
            description=f"{ctx.author.mention}, you have been verified and granted the {role.mention} role!",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"❌ Failed to verify: {e}")

@bot.command(name='mverify')
@commands.has_permissions(kick_members=True)
async def mverify_command(ctx, member: discord.Member = None):
    if not ctx.guild:
        await ctx.send("❌ This command can only be used in a server.")
        return

    if not member:
        embed = discord.Embed(
            title="❌ Usage",
            description="`!mverify @member`",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return

    role = discord.utils.get(ctx.guild.roles, name=VERIFY_ROLE_NAME)

    if not role:
        await ctx.send(f"❌ The verification role `{VERIFY_ROLE_NAME}` does not exist. Please contact an admin.")
        return

    if role in member.roles:
        await ctx.send(f"✅ {member.mention} is already verified!")
        return

    try:
        await member.add_roles(role)
        embed = discord.Embed(
            title="✅ Member Verified",
            description=f"{member.mention} has been verified and granted the {role.mention} role!",
            color=discord.Color.green()
        )
        embed.set_footer(text=f"Verified by {ctx.author.display_name}")
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"❌ Failed to verify member: {e}")

@bot.command(name='dm')
@commands.has_permissions(administrator=True)
async def dm_command(ctx, member: discord.Member = None, *, message: str = None):
    if not FEATURE_STATUS.get('dm'):
        await ctx.send("❌ This command is currently disabled.")
        return

    if not member or not message:
        embed = discord.Embed(
            title="❌ Usage",
            description="`!dm @member <message>`",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return

    try:
        dm_embed = discord.Embed(
            title=f"📧 Message from {ctx.guild.name}",
            description=message,
            color=discord.Color.blue()
        )
        dm_embed.set_footer(text=f"Sent by {ctx.author.display_name}")

        await member.send(embed=dm_embed)
        await ctx.send(f"✅ Message sent to {member.mention}.")
    except discord.Forbidden:
        await ctx.send(f"❌ Cannot send DM to {member.mention}. They may have DMs disabled.")
    except Exception as e:
        await ctx.send(f"❌ Failed to send DM: {e}")

@bot.command(name='dmeveryone')
@commands.has_permissions(administrator=True)
async def dmeveryone_command(ctx, *, message: str = None):
    if not FEATURE_STATUS.get('dm'):
        await ctx.send("❌ This command is currently disabled.")
        return

    if not message:
        usage_embed = discord.Embed(
            title="📧 DM Everyone Command",
            description="Sends a direct message to all non-bot members in the server.",
            color=discord.Color.blue()
        )
        usage_embed.add_field(
            name="Usage",
            value="`!dmeveryone <message>`",
            inline=False
        )
        usage_embed.add_field(
            name="Example",
            value="`!dmeveryone Important server announcement!`",
            inline=False
        )
        await ctx.send(embed=usage_embed, delete_after=10)
        return

    non_bot_members = [m for m in ctx.guild.members if not m.bot]

    confirm_embed = discord.Embed(
        title="⚠️ Confirm Mass DM",
        description=f"Are you sure you want to send a DM to all {len(non_bot_members)} non-bot members?\n\nReact with ✅ to confirm or ❌ to cancel.",
        color=discord.Color.orange()
    )
    confirm_msg = await ctx.send(embed=confirm_embed)

    await confirm_msg.add_reaction("✅")
    await confirm_msg.add_reaction("❌")

    def check(reaction, user):
        return user == ctx.author and str(reaction.emoji) in ["✅", "❌"] and reaction.message.id == confirm_msg.id

    try:
        reaction, user = await bot.wait_for('reaction_add', timeout=30.0, check=check)

        if str(reaction.emoji) == "❌":
            cancel_embed = discord.Embed(
                title="❌ Cancelled",
                description="Mass DM has been cancelled.",
                color=discord.Color.red()
            )
            await confirm_msg.edit(embed=cancel_embed)
            await confirm_msg.clear_reactions()
            return

        await confirm_msg.delete()

        progress_embed = discord.Embed(
            title="📤 Sending Mass DM...",
            description="Please wait while messages are being sent. This may take a while.",
            color=discord.Color.blue()
        )
        status_msg = await ctx.send(embed=progress_embed)

        dm_embed = discord.Embed(
            title=f"📢 Announcement from {ctx.guild.name}",
            description=message,
            color=discord.Color.blue()
        )
        dm_embed.set_footer(text=f"Sent by {ctx.author.display_name}")

        success_count = 0
        fail_count = 0

        for member in non_bot_members:
            if member.bot:
                continue

            try:
                await member.send(embed=dm_embed)
                success_count += 1
                await asyncio.sleep(1)
            except discord.Forbidden:
                fail_count += 1
            except Exception as e:
                fail_count += 1
                print(f"Failed to DM {member.display_name}: {e}")

            if (success_count + fail_count) % 10 == 0 or (success_count + fail_count) == len(non_bot_members):
                current_embed = discord.Embed(
                    title="📤 Sending Mass DM...",
                    description=f"Progress: {success_count + fail_count}/{len(non_bot_members)} members processed.",
                    color=discord.Color.blue()
                )
                current_embed.add_field(name="✅ Successful", value=str(success_count), inline=True)
                current_embed.add_field(name="❌ Failed", value=str(fail_count), inline=True)
                await status_msg.edit(embed=current_embed)

        result_embed = discord.Embed(
            title="✅ Mass DM Complete",
            description=f"DM sent to members.",
            color=discord.Color.green()
        )
        result_embed.add_field(
            name="✅ Successful",
            value=str(success_count),
            inline=True
        )
        result_embed.add_field(
            name="❌ Failed",
            value=str(fail_count),
            inline=True
        )

        await status_msg.edit(embed=result_embed)

        try:
            await ctx.message.delete()
        except:
            pass

    except asyncio.TimeoutError:
        timeout_embed = discord.Embed(
            title="⏱️ Timeout",
            description="Confirmation timed out. Mass DM cancelled.",
            color=discord.Color.red()
        )
        await confirm_msg.edit(embed=timeout_embed)
        await confirm_msg.clear_reactions()
    except Exception as e:
        error_embed = discord.Embed(
            title="❌ Error",
            description=f"Failed to send mass DM: {str(e)}",
            color=discord.Color.red()
        )
        await ctx.send(embed=error_embed)
        print(f"Error in !dmeveryone: {e}")

if __name__ == "__main__":
    DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
    if not DISCORD_TOKEN:
        print("❌ Error: DISCORD_TOKEN not found in environment variables.")
        exit(1)

    bot.run(DISCORD_TOKEN)
