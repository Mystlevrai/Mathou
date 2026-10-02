"""verif-bot — verification par QR code.

QR code -> invitation Discord permanente (salon #verification) -> le bot verifie le compte :
  - present dans liste.txt -> message en MP
  - absent                 -> alerte dans le salon logs
Deux declencheurs : l'arrivee sur le serveur (VERIFY_ON_JOIN) et le bouton
"Verifier mon compte" du panneau (pour ceux qui sont deja membres).
Tourne en local (PC) : uniquement une connexion sortante vers Discord, aucun port a ouvrir."""
from __future__ import annotations

import contextlib
import io
import logging
import os
import sys
import time
from pathlib import Path

import discord
import qrcode
from discord import app_commands

BASE_DIR = Path(__file__).resolve().parent

try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env", encoding="utf-8-sig")
except ImportError:
    pass

log = logging.getLogger("verif.bot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def config_error(msg: str) -> None:
    """Erreur de config -> code 2 : start.bat s'arrete au lieu de relancer en boucle."""
    log.error(msg)
    sys.exit(2)


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        config_error(f"{name} doit etre un nombre (ID Discord), recu : {raw!r}")


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    return raw in {"1", "true", "oui", "yes", "on"} if raw else default


DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GUILD_ID = _int_env("GUILD_ID", 0)
LOG_CHANNEL_ID = _int_env("LOG_CHANNEL_ID", 0)
ALERT_ROLE_ID = _int_env("ALERT_ROLE_ID", 0)
VERIFY_ON_JOIN = _bool_env("VERIFY_ON_JOIN", True)
LIST_FILE = BASE_DIR / os.getenv("LIST_FILE", "liste.txt").strip()
# \n litteral -> retour a la ligne meme si la valeur n'est pas entre guillemets doubles dans .env
DM_MESSAGE = (os.getenv("DM_MESSAGE", "").strip().replace("\\n", "\n")
              or "✅ Salut {name}, ton compte est bien vérifié sur **{server}**.")
CHECK_COOLDOWN = _int_env("CHECK_COOLDOWN", 60)

if not DISCORD_TOKEN:
    config_error("DISCORD_TOKEN manquant dans .env")
if not LOG_CHANNEL_ID:
    config_error("LOG_CHANNEL_ID manquant dans .env (salon des alertes)")

REPLIES = {
    "ok": "✅ Compte vérifié — je t'ai envoyé un message privé.",
    "dm_closed": ("✅ Compte vérifié, mais je n'arrive pas à t'écrire en MP. Autorise les messages privés "
                  "(clic sur le nom du serveur > Paramètres de confidentialité) puis reclique sur le bouton."),
    "refused": "❌ Ton compte n'est pas dans la liste. Un responsable a été prévenu.",
}

# derniere verification par compte (anti-spam : un meme compte ne declenche pas 20 alertes)
_last_check: dict[int, float] = {}


def load_list() -> tuple[set[int], set[str]]:
    """Relue a chaque verification : on edite liste.txt sans redemarrer le bot.
    Une entree par ligne (ID Discord ou nom d'utilisateur), tout ce qui suit un # est ignore."""
    ids: set[int] = set()
    names: set[str] = set()
    try:
        text = LIST_FILE.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        log.warning("%s introuvable : personne n'est reconnu", LIST_FILE)
        return ids, names
    for line in text.splitlines():
        tokens = line.split("#", 1)[0].split()
        if not tokens:
            continue
        tok = tokens[0].lstrip("@").lower()
        if tok.isdigit():
            ids.add(int(tok))
        else:
            names.add(tok)
    return ids, names


def is_listed(user: discord.abc.User) -> bool:
    ids, names = load_list()
    return user.id in ids or user.name.lower() in names


def render(template: str, member: discord.Member) -> str:
    return (template.replace("{mention}", member.mention)
            .replace("{name}", member.display_name)
            .replace("{server}", member.guild.name))


async def _log_channel():
    ch = client.get_channel(LOG_CHANNEL_ID)
    if ch is None:
        with contextlib.suppress(Exception):
            ch = await client.fetch_channel(LOG_CHANNEL_ID)
    return ch


async def post_log(member: discord.Member, source: str, *, title: str, colour: discord.Colour,
                   ping: bool = False) -> None:
    ch = await _log_channel()
    if ch is None:
        log.warning("salon logs %s introuvable (LOG_CHANNEL_ID ? le bot y a acces ?)", LOG_CHANNEL_ID)
        return
    emb = discord.Embed(title=title, colour=colour, timestamp=discord.utils.utcnow())
    emb.add_field(name="Compte", value=f"{member.mention} (`{member}`)", inline=False)
    emb.add_field(name="ID", value=f"`{member.id}`", inline=True)
    emb.add_field(name="Compte créé", value=discord.utils.format_dt(member.created_at, "R"), inline=True)
    emb.add_field(name="Via", value=source, inline=True)
    emb.set_thumbnail(url=member.display_avatar.url)
    content = f"<@&{ALERT_ROLE_ID}>" if ping and ALERT_ROLE_ID else None
    try:
        await ch.send(content=content, embed=emb,
                      allowed_mentions=discord.AllowedMentions(everyone=False, users=False, roles=True))
    except discord.HTTPException as exc:
        log.warning("envoi dans le salon logs impossible: %s", exc)


async def verify(member: discord.Member, source: str) -> str:
    """'ok' (MP envoye), 'dm_closed' (dans la liste mais MP fermes) ou 'refused' (alerte postee)."""
    if not is_listed(member):
        log.info("NON reconnu : %s (%s) via %s", member, member.id, source)
        await post_log(member, source, title="🚨 Compte non reconnu", colour=discord.Colour.red(), ping=True)
        return "refused"
    try:
        await member.send(render(DM_MESSAGE, member))
    except discord.HTTPException as exc:  # Forbidden = MP fermes pour ce serveur
        log.info("reconnu mais MP impossible : %s (%s) : %s", member, member.id, exc)
        await post_log(member, source, title="⚠️ Compte reconnu — MP impossible (MP fermés)",
                       colour=discord.Colour.orange())
        return "dm_closed"
    log.info("reconnu, MP envoye : %s (%s) via %s", member, member.id, source)
    return "ok"


class VerifyView(discord.ui.View):
    """Bouton persistant (timeout=None + custom_id fixe) : marche encore apres un redemarrage du bot."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Vérifier mon compte", emoji="✅", style=discord.ButtonStyle.success,
                       custom_id="verif-bot:verify")
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        member = interaction.user
        if not isinstance(member, discord.Member):
            await interaction.response.send_message("À utiliser sur le serveur.", ephemeral=True)
            return
        now = time.monotonic()
        last = _last_check.get(member.id)
        if last is not None and now - last < CHECK_COOLDOWN:
            wait = int(CHECK_COOLDOWN - (now - last)) + 1
            await interaction.response.send_message(f"⏳ Déjà vérifié à l'instant, réessaie dans {wait} s.",
                                                    ephemeral=True)
            return
        _last_check[member.id] = now
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await verify(member, "bouton")
        if result == "dm_closed":
            _last_check.pop(member.id, None)  # qu'il puisse recliquer des qu'il a ouvert ses MP
        await interaction.followup.send(REPLIES[result], ephemeral=True)


intents = discord.Intents.default()
# intent privilegie : a activer sur le Developer Portal (Bot > Server Members Intent) si VERIFY_ON_JOIN
intents.members = VERIFY_ON_JOIN


class Bot(discord.Client):
    def __init__(self) -> None:
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        self.add_view(VerifyView())
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


client = Bot()


@client.event
async def on_member_join(member: discord.Member) -> None:
    if member.bot or (GUILD_ID and member.guild.id != GUILD_ID):
        return
    _last_check[member.id] = time.monotonic()
    if await verify(member, "arrivée sur le serveur") == "dm_closed":
        _last_check.pop(member.id, None)


@client.tree.command(name="panneau", description="Poste le message avec le bouton « Vérifier mon compte »")
@app_commands.describe(salon="Salon où poster le panneau (par défaut : ici)")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def panneau(interaction: discord.Interaction, salon: discord.TextChannel | None = None) -> None:
    target = salon or interaction.channel
    emb = discord.Embed(
        title="🔐 Vérification",
        description="Clique sur le bouton ci-dessous pour vérifier ton compte.",
        colour=discord.Colour.blurple(),
    )
    try:
        await target.send(embed=emb, view=VerifyView())
    except discord.HTTPException as exc:
        await interaction.response.send_message(f"Impossible de poster dans {target.mention} : {exc}",
                                                ephemeral=True)
        return
    await interaction.response.send_message(f"✅ Panneau posté dans {target.mention}.", ephemeral=True)


@client.tree.command(name="qrcode", description="Crée l'invitation permanente et renvoie le QR code à imprimer")
@app_commands.describe(salon="Salon d'arrivée quand on scanne (par défaut : ici). Mets-y le panneau.")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def qr_code(interaction: discord.Interaction, salon: discord.TextChannel | None = None) -> None:
    target = salon or interaction.channel
    if not isinstance(target, discord.abc.GuildChannel):
        await interaction.response.send_message("Lance la commande dans un salon texte (pas un fil).",
                                                ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        # max_age=0/max_uses=0 : n'expire jamais ; unique=False : reutilise la meme invitation
        invite = await target.create_invite(max_age=0, max_uses=0, unique=False,
                                            reason="QR code de verification")
    except discord.HTTPException as exc:
        await interaction.followup.send(
            f"Impossible de créer l'invitation (permission « Créer une invitation » ?) : {exc}", ephemeral=True
        )
        return
    buf = io.BytesIO()
    qrcode.make(invite.url, box_size=12, border=4).save(buf)
    buf.seek(0)
    await interaction.followup.send(
        f"Lien encodé : {invite.url}\nImprime l'image ci-dessous (clic > Ouvrir dans le navigateur > Enregistrer).",
        file=discord.File(buf, filename="qrcode-verification.png"),
        ephemeral=True,
    )


@client.event
async def on_ready() -> None:
    ids, names = load_list()
    log.info("Connecte comme %s (%s)", client.user, client.user.id if client.user else "?")
    log.info("%s : %d ID(s), %d pseudo(s) ; verification a l'arrivee : %s",
             LIST_FILE.name, len(ids), len(names), "oui" if VERIFY_ON_JOIN else "non")


def main() -> None:
    try:
        client.run(DISCORD_TOKEN, log_handler=None)  # log_handler=None : logs via basicConfig, sans doublon
    except discord.LoginFailure:
        config_error("DISCORD_TOKEN invalide : recopie le jeton (Developer Portal > Bot > Reset Token).")
    except discord.PrivilegedIntentsRequired:
        config_error("Active 'SERVER MEMBERS INTENT' (Developer Portal > Bot > Privileged Gateway Intents) "
                     "ou mets VERIFY_ON_JOIN=false dans .env.")


if __name__ == "__main__":
    main()
