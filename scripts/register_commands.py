#!/usr/bin/env python3
"""Register Discord slash commands for the bot."""

import os
import sys
import json
import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

APPLICATION_ID = os.getenv('DISCORD_APPLICATION_ID')
GUILD_ID = os.getenv('GUILD_ID')
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')

if not all([APPLICATION_ID, GUILD_ID, DISCORD_BOT_TOKEN]):
    print("ERROR: Missing DISCORD_APPLICATION_ID, GUILD_ID, or DISCORD_BOT_TOKEN in .env")
    sys.exit(1)

API_URL = (
    f"https://discord.com/api/v10/applications/{APPLICATION_ID}/"
    f"guilds/{GUILD_ID}/commands"
)
HEADERS = {
    "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
    "Content-Type": "application/json",
}

COMMANDS = [
    {
        "name": "ask",
        "type": 1,
        "description": "Ask a question about Scuz Patrol lore",
        "options": [
            {
                "name": "question",
                "description": "Your question about the band or lore",
                "type": 3,
                "required": True,
            }
        ],
    },
    {
        "name": "lore",
        "type": 1,
        "description": "Add new Scuz Patrol lore to the canon",
        "options": [
            {
                "name": "content",
                "description": "The lore to add",
                "type": 3,
                "required": True,
            }
        ],
    },
]


def register_commands():
    """Register all slash commands."""
    print(f"Registering commands for guild {GUILD_ID}...\n")

    for cmd in COMMANDS:
        try:
            print(f"Registering /{cmd['name']}...")
            response = requests.post(API_URL, headers=HEADERS, json=cmd)
            response.raise_for_status()
            result = response.json()
            print(f"  ✓ Success: {json.dumps(result, indent=2)}\n")
        except requests.exceptions.HTTPError as e:
            print(f"  ✗ Error: {e.response.status_code} - {e.response.text}\n")
            sys.exit(1)
        except Exception as e:
            print(f"  ✗ Error: {e}\n")
            sys.exit(1)

    print("✓ All commands registered. Try typing / in Discord to see them.")


if __name__ == "__main__":
    register_commands()
