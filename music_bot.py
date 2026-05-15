import subprocess
import sys

# Force l'installation de PyNaCl si absent
subprocess.check_call([sys.executable, "-m", "pip", "install", "PyNaCl", "--quiet"])

import discord
from discord.ext import commands
import yt_dlp
import asyncio
import os

# ─────────────────────────────────────────
#  Configuration
# ─────────────────────────────────────────
PREFIX = "?"
TOKEN = os.getenv("DISCORD_TOKEN", "METS_TON_TOKEN_ICI")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents)

# ─────────────────────────────────────────
#  Options yt-dlp / FFmpeg
# ─────────────────────────────────────────
YDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "default_search": "ytsearch",   # recherche YouTube si pas d'URL
    "source_address": "0.0.0.0",
}

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

# ─────────────────────────────────────────
#  File d'attente par serveur
# ─────────────────────────────────────────
queues: dict[int, list[dict]] = {}   # guild_id -> [{"title": ..., "url": ...}, ...]


def get_queue(guild_id: int) -> list:
    if guild_id not in queues:
        queues[guild_id] = []
    return queues[guild_id]


# ─────────────────────────────────────────
#  Lecture de la piste suivante
# ─────────────────────────────────────────
def play_next(ctx: commands.Context):
    queue = get_queue(ctx.guild.id)
    if not queue:
        return

    track = queue.pop(0)
    source = discord.FFmpegPCMAudio(track["url"], **FFMPEG_OPTIONS)

    def after_play(error):
        if error:
            print(f"Erreur lecture : {error}")
        play_next(ctx)

    ctx.voice_client.play(source, after=after_play)
    asyncio.run_coroutine_threadsafe(
        ctx.send(f"🎵 **En cours :** {track['title']}"),
        bot.loop,
    )


# ─────────────────────────────────────────
#  Commandes
# ─────────────────────────────────────────

@bot.command(name="play", aliases=["p"])
async def play(ctx: commands.Context, *, search: str):
    """?play <titre ou URL>  — joue ou met en file d'attente une chanson."""

    # Vérifier / rejoindre le salon vocal
    if ctx.author.voice is None:
        return await ctx.send("❌ Tu dois être dans un salon vocal !")

    voice_channel = ctx.author.voice.channel
    vc = ctx.voice_client

    if vc is None:
        vc = await voice_channel.connect()
    elif vc.channel != voice_channel:
        await vc.move_to(voice_channel)

    async with ctx.typing():
        with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
            try:
                info = ydl.extract_info(search, download=False)
                # Si c'est un résultat de recherche, prendre le premier
                if "entries" in info:
                    info = info["entries"][0]

                track = {"title": info["title"], "url": info["url"]}
            except Exception as e:
                return await ctx.send(f"❌ Impossible de trouver `{search}` : {e}")

    queue = get_queue(ctx.guild.id)

    if vc.is_playing() or vc.is_paused():
        queue.append(track)
        await ctx.send(f"✅ Ajouté à la file : **{track['title']}** (position {len(queue)})")
    else:
        queue.append(track)
        play_next(ctx)


@bot.command(name="skip", aliases=["s"])
async def skip(ctx: commands.Context):
    """?skip  — passe à la chanson suivante."""
    vc = ctx.voice_client
    if vc and vc.is_playing():
        vc.stop()
        await ctx.send("⏭️ Chanson passée !")
    else:
        await ctx.send("❌ Rien en cours de lecture.")


@bot.command(name="pause")
async def pause(ctx: commands.Context):
    """?pause  — met en pause."""
    vc = ctx.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await ctx.send("⏸️ Lecture en pause.")
    else:
        await ctx.send("❌ Rien en cours de lecture.")


@bot.command(name="resume", aliases=["r"])
async def resume(ctx: commands.Context):
    """?resume  — reprend la lecture."""
    vc = ctx.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await ctx.send("▶️ Lecture reprise.")
    else:
        await ctx.send("❌ La lecture n'est pas en pause.")


@bot.command(name="stop")
async def stop(ctx: commands.Context):
    """?stop  — arrête et vide la file d'attente."""
    vc = ctx.voice_client
    if vc:
        get_queue(ctx.guild.id).clear()
        vc.stop()
        await vc.disconnect()
        await ctx.send("⏹️ Lecture arrêtée, bot déconnecté.")
    else:
        await ctx.send("❌ Le bot n'est pas connecté.")


@bot.command(name="queue", aliases=["q", "liste"])
async def queue_cmd(ctx: commands.Context):
    """?queue  — affiche la file d'attente."""
    queue = get_queue(ctx.guild.id)
    vc = ctx.voice_client

    if not queue and (vc is None or not vc.is_playing()):
        return await ctx.send("📭 La file d'attente est vide.")

    lines = []
    if vc and vc.is_playing():
        lines.append("🎵 **En cours de lecture**")

    for i, track in enumerate(queue, start=1):
        lines.append(f"`{i}.` {track['title']}")

    embed = discord.Embed(
        title="🎶 File d'attente",
        description="\n".join(lines) if lines else "Vide",
        color=discord.Color.blurple(),
    )
    await ctx.send(embed=embed)


@bot.command(name="volume", aliases=["vol"])
async def volume(ctx: commands.Context, vol: int):
    """?volume <0-200>  — règle le volume."""
    vc = ctx.voice_client
    if vc is None or not vc.is_playing():
        return await ctx.send("❌ Rien en cours de lecture.")
    if not 0 <= vol <= 200:
        return await ctx.send("❌ Le volume doit être entre 0 et 200.")
    vc.source = discord.PCMVolumeTransformer(vc.source, volume=vol / 100)
    await ctx.send(f"🔊 Volume réglé à **{vol}%**")


# ─────────────────────────────────────────
#  Événements
# ─────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"✅ Connecté en tant que {bot.user} (ID: {bot.user.id})")
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.listening,
        name="?play <musique>",
    ))


@bot.event
async def on_command_error(ctx: commands.Context, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Argument manquant. Exemple : `?play Blanche Maes`")
    elif isinstance(error, commands.CommandNotFound):
        pass   # ignorer les commandes inconnues
    else:
        await ctx.send(f"❌ Erreur : {error}")
        raise error


# ─────────────────────────────────────────
#  Lancement
# ─────────────────────────────────────────
if __name__ == "__main__":
    bot.run(TOKEN)
