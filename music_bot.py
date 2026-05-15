import discord
from discord.ext import commands
import yt_dlp
import asyncio
import os
import glob
import ctypes
import ctypes.util

# ─────────────────────────────────────────
#  Chargement Opus
# ─────────────────────────────────────────
def load_opus():
    if discord.opus.is_loaded():
        return True

    candidates = []

    # 1. Variable d'environnement
    env = os.getenv("DISCORD_OPUS_PATH")
    if env:
        candidates.append(env)

    # 2. ctypes.util.find_library
    found = ctypes.util.find_library("opus")
    if found:
        candidates.append(found)

    # 3. Nix store (Railway / Render)
    candidates += glob.glob("/nix/store/*/lib/libopus.so*")

    # 4. Paths Linux classiques
    candidates += [
        "/usr/lib/x86_64-linux-gnu/libopus.so.0",
        "/usr/lib/libopus.so.0",
        "/usr/local/lib/libopus.so.0",
        "libopus.so.0",
        "libopus.so",
        "opus",
    ]

    for path in candidates:
        try:
            discord.opus.load_opus(path)
            print(f"✅ Opus chargé : {path}")
            return True
        except Exception:
            continue

    print("❌ Opus introuvable — la lecture vocale ne fonctionnera pas")
    return False

OPUS_OK = load_opus()

# ─────────────────────────────────────────
#  FFmpeg
# ─────────────────────────────────────────
try:
    import imageio_ffmpeg
    FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
    print(f"✅ FFmpeg : {FFMPEG_PATH}")
except Exception:
    FFMPEG_PATH = "ffmpeg"

# ─────────────────────────────────────────
#  Configuration
# ─────────────────────────────────────────
PREFIX = "?"
TOKEN = os.getenv("DISCORD_TOKEN", "METS_TON_TOKEN_ICI")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)

# ─────────────────────────────────────────
#  Options yt-dlp — SoundCloud en priorité
# ─────────────────────────────────────────
YDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "scsearch",   # SoundCloud search
    "source_address": "0.0.0.0",
    "extract_flat": False,
}

FFMPEG_OPTIONS = {
    "before_options": (
        "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
    ),
    "options": "-vn -bufsize 64k",
}

# ─────────────────────────────────────────
#  File d'attente par serveur
# ─────────────────────────────────────────
# Structure : guild_id -> {"queue": [...], "current": None, "volume": 0.5}
servers: dict[int, dict] = {}


def get_server(guild_id: int) -> dict:
    if guild_id not in servers:
        servers[guild_id] = {"queue": [], "current": None, "volume": 0.5}
    return servers[guild_id]


# ─────────────────────────────────────────
#  Recherche SoundCloud
# ─────────────────────────────────────────
async def search_soundcloud(query: str) -> dict | None:
    """Cherche sur SoundCloud et retourne les infos de la piste."""
    loop = asyncio.get_event_loop()

    def _extract():
        with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
            # Si c'est une URL directe, on l'utilise telle quelle
            if query.startswith("http"):
                info = ydl.extract_info(query, download=False)
            else:
                # Sinon, recherche SoundCloud
                info = ydl.extract_info(f"scsearch1:{query}", download=False)
                if "entries" in info and info["entries"]:
                    info = info["entries"][0]
                elif "entries" in info:
                    return None
            return info

    try:
        info = await loop.run_in_executor(None, _extract)
        if not info:
            return None

        return {
            "title": info.get("title", "Titre inconnu"),
            "url": info["url"],
            "duration": info.get("duration", 0),
            "webpage_url": info.get("webpage_url", ""),
            "uploader": info.get("uploader", "Inconnu"),
            "thumbnail": info.get("thumbnail", None),
        }
    except Exception as e:
        print(f"Erreur extraction : {e}")
        return None


def format_duration(seconds: int) -> str:
    if not seconds:
        return "??:??"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


# ─────────────────────────────────────────
#  Lecture
# ─────────────────────────────────────────
def play_next(ctx: commands.Context):
    server = get_server(ctx.guild.id)
    queue = server["queue"]

    if not queue:
        server["current"] = None
        asyncio.run_coroutine_threadsafe(
            ctx.send("📭 File d'attente terminée !"),
            bot.loop,
        )
        return

    track = queue.pop(0)
    server["current"] = track

    source = discord.FFmpegPCMAudio(
        track["url"],
        executable=FFMPEG_PATH,
        **FFMPEG_OPTIONS,
    )
    source = discord.PCMVolumeTransformer(source, volume=server["volume"])

    def after_play(error):
        if error:
            print(f"Erreur lecture : {error}")
        play_next(ctx)

    ctx.voice_client.play(source, after=after_play)

    embed = discord.Embed(
        title="🎵 En cours de lecture",
        description=f"**[{track['title']}]({track['webpage_url']})**",
        color=discord.Color.orange(),
    )
    embed.add_field(name="Artiste", value=track["uploader"], inline=True)
    embed.add_field(name="Durée", value=format_duration(track["duration"]), inline=True)
    if track.get("thumbnail"):
        embed.set_thumbnail(url=track["thumbnail"])
    embed.set_footer(text="🎧 Source : SoundCloud")

    asyncio.run_coroutine_threadsafe(
        ctx.send(embed=embed),
        bot.loop,
    )


# ─────────────────────────────────────────
#  Commandes
# ─────────────────────────────────────────

@bot.command(name="play", aliases=["p"])
async def play(ctx: commands.Context, *, search: str):
    """?play <titre ou URL SoundCloud>"""

    if not OPUS_OK:
        return await ctx.send("❌ Opus n'est pas chargé sur ce serveur. Contacte l'admin.")

    if ctx.author.voice is None:
        return await ctx.send("❌ Tu dois être dans un salon vocal !")

    vc = ctx.voice_client
    if vc is None:
        vc = await ctx.author.voice.channel.connect()
    elif vc.channel != ctx.author.voice.channel:
        await vc.move_to(ctx.author.voice.channel)

    msg = await ctx.send("🔍 Recherche sur SoundCloud...")

    track = await search_soundcloud(search)
    if not track:
        return await msg.edit(content=f"❌ Aucun résultat pour `{search}` sur SoundCloud.")

    server = get_server(ctx.guild.id)

    if vc.is_playing() or vc.is_paused():
        server["queue"].append(track)
        await msg.edit(content=f"✅ Ajouté à la file : **{track['title']}** (position {len(server['queue'])})")
    else:
        server["queue"].append(track)
        play_next(ctx)
        await msg.delete()


@bot.command(name="skip", aliases=["s"])
async def skip(ctx: commands.Context):
    """?skip — passe à la chanson suivante"""
    vc = ctx.voice_client
    if vc and (vc.is_playing() or vc.is_paused()):
        vc.stop()
        await ctx.send("⏭️ Chanson passée !")
    else:
        await ctx.send("❌ Rien en cours de lecture.")


@bot.command(name="pause")
async def pause(ctx: commands.Context):
    """?pause — met en pause"""
    vc = ctx.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await ctx.send("⏸️ Lecture en pause.")
    else:
        await ctx.send("❌ Rien en cours de lecture.")


@bot.command(name="resume", aliases=["r"])
async def resume(ctx: commands.Context):
    """?resume — reprend la lecture"""
    vc = ctx.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await ctx.send("▶️ Lecture reprise.")
    else:
        await ctx.send("❌ La lecture n'est pas en pause.")


@bot.command(name="stop")
async def stop(ctx: commands.Context):
    """?stop — arrête tout et déconnecte"""
    vc = ctx.voice_client
    if vc:
        server = get_server(ctx.guild.id)
        server["queue"].clear()
        server["current"] = None
        vc.stop()
        await vc.disconnect()
        await ctx.send("⏹️ Lecture arrêtée, bot déconnecté.")
    else:
        await ctx.send("❌ Le bot n'est pas connecté.")


@bot.command(name="queue", aliases=["q", "liste"])
async def queue_cmd(ctx: commands.Context):
    """?queue — affiche la file d'attente"""
    server = get_server(ctx.guild.id)
    queue = server["queue"]
    current = server["current"]
    vc = ctx.voice_client

    if not current and not queue:
        return await ctx.send("📭 La file d'attente est vide.")

    lines = []
    if current and vc and vc.is_playing():
        lines.append(f"🎵 **En cours :** {current['title']} `{format_duration(current['duration'])}`")

    for i, track in enumerate(queue, start=1):
        lines.append(f"`{i}.` {track['title']} `{format_duration(track['duration'])}`")

    embed = discord.Embed(
        title="🎶 File d'attente",
        description="\n".join(lines) or "Vide",
        color=discord.Color.orange(),
    )
    embed.set_footer(text=f"{len(queue)} chanson(s) en attente")
    await ctx.send(embed=embed)


@bot.command(name="volume", aliases=["vol"])
async def volume(ctx: commands.Context, vol: int):
    """?volume <0-200> — règle le volume"""
    vc = ctx.voice_client
    if vc is None or not (vc.is_playing() or vc.is_paused()):
        return await ctx.send("❌ Rien en cours de lecture.")
    if not 0 <= vol <= 200:
        return await ctx.send("❌ Le volume doit être entre 0 et 200.")

    server = get_server(ctx.guild.id)
    server["volume"] = vol / 100
    if isinstance(vc.source, discord.PCMVolumeTransformer):
        vc.source.volume = vol / 100
    await ctx.send(f"🔊 Volume réglé à **{vol}%**")


@bot.command(name="nowplaying", aliases=["np"])
async def nowplaying(ctx: commands.Context):
    """?np — affiche la chanson en cours"""
    server = get_server(ctx.guild.id)
    current = server["current"]
    vc = ctx.voice_client

    if not current or not vc or not vc.is_playing():
        return await ctx.send("❌ Rien en cours de lecture.")

    embed = discord.Embed(
        title="🎵 En cours de lecture",
        description=f"**[{current['title']}]({current['webpage_url']})**",
        color=discord.Color.orange(),
    )
    embed.add_field(name="Artiste", value=current["uploader"], inline=True)
    embed.add_field(name="Durée", value=format_duration(current["duration"]), inline=True)
    if current.get("thumbnail"):
        embed.set_thumbnail(url=current["thumbnail"])
    await ctx.send(embed=embed)


@bot.command(name="help")
async def help_cmd(ctx: commands.Context):
    """?help — affiche l'aide"""
    embed = discord.Embed(
        title="🎵 Lafamax Music — Commandes",
        color=discord.Color.orange(),
    )
    cmds = [
        ("?play <titre/URL>", "Joue une chanson depuis SoundCloud"),
        ("?skip / ?s", "Passe à la suivante"),
        ("?pause", "Met en pause"),
        ("?resume / ?r", "Reprend la lecture"),
        ("?stop", "Arrête et déconnecte"),
        ("?queue / ?q", "Affiche la file d'attente"),
        ("?np", "Chanson en cours"),
        ("?volume <0-200>", "Règle le volume"),
    ]
    for name, value in cmds:
        embed.add_field(name=f"`{name}`", value=value, inline=False)
    embed.set_footer(text="🎧 Powered by SoundCloud")
    await ctx.send(embed=embed)


# ─────────────────────────────────────────
#  Événements
# ─────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"✅ Connecté : {bot.user} (ID: {bot.user.id})")
    print(f"   Opus OK : {OPUS_OK}")
    print(f"   FFmpeg  : {FFMPEG_PATH}")
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.listening,
        name="?play <musique>",
    ))


@bot.event
async def on_command_error(ctx: commands.Context, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ Argument manquant. Ex : `?play Blanche Maes`")
    elif isinstance(error, commands.CommandNotFound):
        pass
    else:
        await ctx.send(f"❌ Erreur : {error}")
        raise error


# ─────────────────────────────────────────
#  Lancement
# ─────────────────────────────────────────
if __name__ == "__main__":
    bot.run(TOKEN)
