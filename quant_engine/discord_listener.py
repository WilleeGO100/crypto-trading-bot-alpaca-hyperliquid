import os
import discord
from dotenv import load_dotenv
from discord_alert_hotkey_sender import parse_tcl_alert, post_signal

# Load token from the .env file
load_dotenv()
TOKEN = os.getenv('DISCORD_BOT_TOKEN')

# Enable required intents to read messages
intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)


@client.event
async def on_ready():
    print(f"\n--- [ONLINE] 24/7 DISCORD LISTENER ONLINE AS {client.user} ---")
    print("Listening for incoming signals...")


@client.event
async def on_message(message):
    # Ignore the bot's own messages
    if message.author == client.user:
        return

    text = message.content.strip()
    if not text:
        return

    try:
        # Pass message to your parser
        alert = parse_tcl_alert(text)
        print(f"\n[ALERT] SIGNAL DETECTED: {alert.symbol} {alert.side}")

        # Fire to FastAPI
        resp = post_signal(alert)
        print(f"[OK] POST RESULT: {resp['status_code']} {resp['text']}")

    except ValueError:
        pass  # Ignore normal chat messages
    except Exception as e:
        print(f"[ERROR] Error processing signal: {e}")


if __name__ == "__main__":
    if not TOKEN:
        print("[ERROR] ERROR: DISCORD_BOT_TOKEN not found in .env file.")
    else:
        client.run(TOKEN)