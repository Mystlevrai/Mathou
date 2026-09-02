"""Mini-bot de test : !ping -> pong. Sert juste a verifier que le token et la
connexion Discord fonctionnent. Le vrai bot est dans bot.py."""
from __future__ import annotations

import os

import discord
from discord.ext import commands

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

TOKEN = os.environ["DISCORD_TOKEN"]

intents = discord.Intents.default()
intents.message_content = True  # necessaire pour lire "!ping" (a activer aussi dans le portail Discord)

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready() -> None:
    print(f"Connecte comme {bot.user} ({bot.user.id})")


@bot.command(name="ping")
async def ping(ctx: commands.Context) -> None:
    await ctx.send("pong")


bot.run(TOKEN)
