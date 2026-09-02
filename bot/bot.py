"""mathou bot v2 — /dl /chercher /catalogue /cancel + suivi + salons logs & bibliotheque."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
from urllib.parse import urlparse

import aiohttp
import discord
from discord import app_commands

try:
    from dotenv import load_dotenv

    load_dotenv(encoding="utf-8-sig")
except ImportError:
    pass

log = logging.getLogger("mathou.bot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else default


DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
VM_API_BASE = os.environ["VM_API_BASE"].rstrip("/")
API_TOKEN = os.environ["API_TOKEN"]
GUILD_ID = _int_env("GUILD_ID", 0)
LOG_CHANNEL_ID = _int_env("LOG_CHANNEL_ID", 0)
LIBRARY_CHANNEL_ID = _int_env("LIBRARY_CHANNEL_ID", 0)
CATALOG_URL = os.getenv("CATALOG_URL", "").strip()
ALLOWED_USER_IDS = {int(x) for x in os.getenv("ALLOWED_USER_IDS", "").replace(" ", "").split(",") if x}
ALLOWED_ROLE_ID = _int_env("ALLOWED_ROLE_ID", 0)
POLL_SECONDS = _int_env("POLL_SECONDS", 3)
JOB_MAX_WAIT = _int_env("JOB_MAX_WAIT", 14400)
DL_COOLDOWN = _int_env("DL_COOLDOWN", 10)

STATUS_LABELS = {
    "queued": "\U0001f552 En file d'attente",
    "running": "⚙️ Telechargement (cdlr)",
    "zipping": "\U0001f4e6 Compression",
    "uploading": "☁️ Envoi sur B2",
    "done": "✅ Termine",
    "error": "❌ Echec",
}


def human_size(nbytes) -> str:
    if not nbytes:
        return "?"
    x = float(nbytes)
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if x < 1024 or unit == "To":
            return f"{x:.1f} {unit}"
        x /= 1024
    return f"{x:.1f} To"


def dur(secs) -> str:
    if not secs:
        return "?"
    total = int(secs)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def clean_url(raw: str) -> str | None:
    raw = raw.strip().strip("<>").rstrip(".,)]}")
    p = urlparse(raw)
    if p.scheme in {"http", "https"} and p.netloc and len(raw) <= 2000:
        return raw
    return None


def is_allowed(user) -> bool:
    if not ALLOWED_USER_IDS and not ALLOWED_ROLE_ID:
        return True
    if user.id in ALLOWED_USER_IDS:
        return True
    if ALLOWED_ROLE_ID and isinstance(user, discord.Member):
        return any(r.id == ALLOWED_ROLE_ID for r in user.roles)
    return False


class VMBusy(RuntimeError):
    pass


class VMClient:
    def __init__(self, base: str, token: str) -> None:
        self.base = base
        self.headers = {"Authorization": f"Bearer {token}"}
        self._session: aiohttp.ClientSession | None = None

    async def _s(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers=self.headers, timeout=aiohttp.ClientTimeout(total=30)
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def submit(self, url: str, season: int | None) -> str:
        payload: dict = {"url": url}
        if season is not None:
            payload["season"] = season
        s = await self._s()
        async with s.post(f"{self.base}/jobs", json=payload) as r:
            data = await r.json()
            if r.status == 429:
                raise VMBusy(data.get("detail") or "file pleine")
            if r.status != 200:
                raise RuntimeError(data.get("detail") or f"HTTP {r.status}")
            return data["job_id"]

    async def poll(self, job_id: str) -> dict:
        s = await self._s()
        async with s.get(f"{self.base}/jobs/{job_id}") as r:
            data = await r.json()
            if r.status != 200:
                raise RuntimeError(data.get("detail") or f"HTTP {r.status}")
            return data

    async def cancel(self, job_id: str) -> str:
        s = await self._s()
        async with s.post(f"{self.base}/jobs/{job_id}/cancel") as r:
            data = await r.json()
            if r.status != 200:
                raise RuntimeError(data.get("detail") or f"HTTP {r.status}")
            return data.get("result", "?")

    async def search(self, q: str) -> list[dict]:
        s = await self._s()
        async with s.get(f"{self.base}/search", params={"q": q}) as r:
            data = await r.json()
            if r.status != 200:
                raise RuntimeError(data.get("detail") or f"HTTP {r.status}")
            return data.get("results", [])


vm = VMClient(VM_API_BASE, API_TOKEN)

_bg: set[asyncio.Task] = set()


def spawn(coro) -> None:
    t = asyncio.create_task(coro)
    _bg.add(t)
    t.add_done_callback(_bg.discard)


def season_label(season) -> str:
    return f"Saison {int(season):02d}" if season is not None else "Saison ?"


def progress_bar(frac: float, width: int = 18) -> str:
    frac = max(0.0, min(1.0, frac))
    filled = round(frac * width)
    return "█" * filled + "░" * (width - filled)


def progress_field(state: dict) -> tuple[str, str] | None:
    pb, pt = state.get("progress_bytes"), state.get("progress_total")
    if pb is None:
        return None
    if pt:
        frac = pb / pt if pt else 0.0
        return "Progression", f"`{progress_bar(frac)}` {frac * 100:.0f}%  ({human_size(pb)} / {human_size(pt)})"
    return "Telecharge", human_size(pb)


def build_embed(url: str, season, state: dict) -> discord.Embed:
    status = state.get("status", "queued")
    colour = {"done": discord.Colour.green(), "error": discord.Colour.red()}.get(
        status, discord.Colour.blurple()
    )
    emb = discord.Embed(title=STATUS_LABELS.get(status, status), colour=colour)
    emb.add_field(name="Lien", value=url[:1024], inline=False)
    if season is not None:
        emb.add_field(name="Saison", value=str(season), inline=True)
    if state.get("size_bytes"):
        emb.add_field(name="Poids", value=human_size(state["size_bytes"]), inline=True)
    if status in ("running", "uploading"):
        pf = progress_field(state)
        if pf:
            emb.add_field(name=pf[0], value=pf[1], inline=False)
    if state.get("job_id"):
        emb.set_footer(text=f"job {state['job_id']}")
    if status == "done" and state.get("download_url"):
        emb.add_field(name="Telechargement (.zip)", value=state["download_url"][:1024], inline=False)
        if CATALOG_URL:
            emb.add_field(name="Catalogue", value=CATALOG_URL, inline=False)
    if status == "error":
        emb.add_field(name="Erreur", value=(state.get("error") or "raison inconnue")[:1024], inline=False)
        if state.get("log_tail"):
            emb.add_field(name="Log", value=f"```\n{state['log_tail'][-900:]}\n```", inline=False)
    return emb


async def _channel(cid: int):
    if not cid:
        return None
    ch = client.get_channel(cid)
    if ch is None:
        with contextlib.suppress(Exception):
            ch = await client.fetch_channel(cid)
    return ch


async def post_log(requester, url: str, season, state: dict) -> None:
    ch = await _channel(LOG_CHANNEL_ID)
    if ch is None:
        return
    ok = state.get("status") == "done"
    emb = discord.Embed(
        title="✅ Termine" if ok else "❌ Echec",
        colour=discord.Colour.green() if ok else discord.Colour.red(),
        timestamp=discord.utils.utcnow(),
    )
    emb.add_field(name="Demande par", value=f"{requester.mention} (`{requester}`)", inline=False)
    emb.add_field(name="Lien", value=url[:1024], inline=False)
    if season is not None:
        emb.add_field(name="Saison", value=str(season), inline=True)
    if state.get("series_slug"):
        emb.add_field(name="Serie", value=state["series_slug"], inline=True)
    if state.get("size_bytes"):
        emb.add_field(name="Poids", value=human_size(state["size_bytes"]), inline=True)
    for name, key in (("Telechargement", "dl_seconds"), ("Compression", "zip_seconds"),
                      ("Upload B2", "up_seconds")):
        if state.get(key):
            emb.add_field(name=name, value=dur(state[key]), inline=True)
    if ok and state.get("download_url"):
        emb.add_field(name=".zip", value=state["download_url"][:1024], inline=False)
    if not ok and state.get("error"):
        emb.add_field(name="Erreur", value=str(state["error"])[:1024], inline=False)
    emb.set_footer(text=f"job {state.get('job_id', '?')}")
    with contextlib.suppress(discord.HTTPException):
        await ch.send(embed=emb)


async def post_new(state: dict, season) -> None:
    ch = await _channel(LIBRARY_CHANNEL_ID)
    if ch is None or state.get("status") != "done" or not state.get("download_url"):
        return
    serie = state.get("series_slug") or "?"
    size = human_size(state.get("size_bytes"))
    line = f"\U0001f195 **{serie}** — {season_label(season)} — {size}\n{state['download_url']}"
    if CATALOG_URL:
        line += f"\nCatalogue : {CATALOG_URL}"
    with contextlib.suppress(discord.HTTPException):
        await ch.send(line)


def _render_key(state: dict) -> tuple:
    """Change quand le statut change OU quand la progression avance d'un cran (~5%)."""
    pb, pt = state.get("progress_bytes"), state.get("progress_total")
    step = int(pb / pt * 20) if (pt and pb is not None) else (pb // (300 * 1024 * 1024) if pb else 0)
    return (state.get("status"), step)


async def track(message: discord.Message, url: str, season, job_id: str, requester) -> None:
    last_key = None
    waited = 0
    while waited < JOB_MAX_WAIT:
        try:
            state = await vm.poll(job_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("poll %s: %s", job_id, exc)
            await asyncio.sleep(POLL_SECONDS)
            waited += POLL_SECONDS
            continue
        key = _render_key(state)
        if key != last_key:
            last_key = key
            with contextlib.suppress(discord.HTTPException):
                await message.edit(embed=build_embed(url, season, state))
        if state.get("status") in {"done", "error"}:
            await post_log(requester, url, season, state)
            await post_new(state, season)
            return
        await asyncio.sleep(POLL_SECONDS)
        waited += POLL_SECONDS
    timeout_state = {"status": "error", "job_id": job_id,
                     "error": f"Toujours pas fini apres {JOB_MAX_WAIT // 60} min, j'arrete de suivre."}
    with contextlib.suppress(discord.HTTPException):
        await message.edit(embed=build_embed(url, season, timeout_state))
    await post_log(requester, url, season, timeout_state)


intents = discord.Intents.default()


class Bot(discord.Client):
    def __init__(self) -> None:
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        try:
            if GUILD_ID:
                g = discord.Object(id=GUILD_ID)
                self.tree.copy_global_to(guild=g)
                await self.tree.sync(guild=g)
                log.info("commandes sync sur la guilde %s", GUILD_ID)
            else:
                await self.tree.sync()
                log.info("commandes sync globalement")
        except Exception as exc:  # noqa: BLE001
            log.warning("sync echouee, bot demarre quand meme: %s", exc)
            if GUILD_ID:
                with contextlib.suppress(Exception):
                    await self.tree.sync()
                    log.info("fallback: sync globale")

    async def close(self) -> None:
        await vm.close()
        await super().close()


client = Bot()


@client.tree.command(name="dl", description="Telecharge une saison et la met en ligne")
@app_commands.describe(lien="Lien de la saison/serie", nombre="Numero de la saison")
@app_commands.checks.cooldown(1, float(DL_COOLDOWN), key=lambda i: 0)  # global : 1 /dl toutes les N s, tout le monde confondu
async def dl(
    interaction: discord.Interaction,
    lien: str,
    nombre: app_commands.Range[int, 0, 100] | None = None,
) -> None:
    if not is_allowed(interaction.user):
        await interaction.response.send_message("Non autorise.", ephemeral=True)
        return
    url = clean_url(lien)
    if not url:
        await interaction.response.send_message("Lien invalide (http/https).", ephemeral=True)
        return
    await interaction.response.defer(thinking=True)
    try:
        job_id = await vm.submit(url, nombre)
    except VMBusy:
        await interaction.edit_original_response(
            content="⏳ Un autre telechargement est en cours. Reessaie dans quelques minutes."
        )
        return
    except Exception as exc:  # noqa: BLE001
        await interaction.edit_original_response(content=f"Impossible de joindre la VM : {exc}")
        return
    state = {"status": "queued", "job_id": job_id}
    if interaction.channel is not None:
        msg = await interaction.channel.send(embed=build_embed(url, nombre, state))
        with contextlib.suppress(discord.HTTPException):
            await interaction.delete_original_response()
    else:
        msg = await interaction.followup.send(embed=build_embed(url, nombre, state), wait=True)
    spawn(track(msg, url, nombre, job_id, interaction.user))


@dl.error
async def dl_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    if isinstance(error, app_commands.CommandOnCooldown):
        msg = f"⏳ Doucement — attends {error.retry_after:.0f}s avant un autre /dl."
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
        return
    raise error


@client.tree.command(name="chercher", description="Cherche une serie dans le catalogue")
@app_commands.describe(nom="Nom (ou partie) de la serie")
async def chercher(interaction: discord.Interaction, nom: str) -> None:
    await interaction.response.defer(thinking=True, ephemeral=True)
    try:
        results = await vm.search(nom)
    except Exception as exc:  # noqa: BLE001
        await interaction.followup.send(f"Erreur : {exc}", ephemeral=True)
        return
    if not results:
        await interaction.followup.send("Aucun resultat.", ephemeral=True)
        return
    lines = [f"- **{r['title']}** — {r['seasons']} saison(s)" for r in results]
    tail = f"\n\nCatalogue complet : {CATALOG_URL}" if CATALOG_URL else ""
    await interaction.followup.send("\n".join(lines) + tail, ephemeral=True)


@client.tree.command(name="catalogue", description="Lien du catalogue")
async def catalogue(interaction: discord.Interaction) -> None:
    if CATALOG_URL:
        await interaction.response.send_message(CATALOG_URL)
    else:
        await interaction.response.send_message("CATALOG_URL non configure.", ephemeral=True)


@client.tree.command(name="cancel", description="Annule un job (id visible en pied d'encadre)")
@app_commands.describe(job="L'identifiant du job")
async def cancel(interaction: discord.Interaction, job: str) -> None:
    if not is_allowed(interaction.user):
        await interaction.response.send_message("Non autorise.", ephemeral=True)
        return
    try:
        res = await vm.cancel(job.strip())
    except Exception as exc:  # noqa: BLE001
        await interaction.response.send_message(f"Erreur : {exc}", ephemeral=True)
        return
    msg = {
        "annule": "✅ Job annule (il etait en file d'attente).",
        "en cours": "⚠️ Job deja demarre — a arreter manuellement sur la VM (voir README).",
        "inconnu": "Job introuvable.",
    }.get(res, f"Etat : {res}")
    await interaction.response.send_message(msg, ephemeral=True)


@client.event
async def on_ready() -> None:
    log.info("Connecte comme %s (%s)", client.user, client.user.id if client.user else "?")


def main() -> None:
    client.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
