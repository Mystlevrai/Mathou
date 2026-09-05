"""Panel admin du catalogue : editer/supprimer series & saisons, forcer un rebuild.

Auth par JETON UNIQUEMENT (ADMIN_TOKEN), sans restriction d'IP - contrairement au
reste de l'API (/jobs...) qui est reserve a l'IP du bot. C'est voulu : l'admin doit
pouvoir s'y connecter de n'importe ou (telephone, autre reseau...). D'ou l'importance
d'un ADMIN_TOKEN long et unique, jamais reutilise ailleurs.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

import db
import library
import storage
from config import Config

PAGE = r"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>mathou - admin</title>
<style>
:root{color-scheme:dark;--bg:#0f1115;--card:#171a21;--line:#262b36;--fg:#e7e9ee;--mut:#9aa3b2;--acc:#5b8def;--bad:#e5566d;--ok:#4caf7d}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,Segoe UI,Roboto,sans-serif}
header{padding:16px 24px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:16px;position:sticky;top:0;background:var(--bg);z-index:5}
header b{font-weight:700}
main{padding:24px;max-width:1100px;margin:0 auto}
button{cursor:pointer;background:var(--acc);color:#fff;border:0;border-radius:8px;padding:8px 14px;font-size:13px;font-weight:600}
button.bad{background:var(--bad)}
button.ghost{background:transparent;border:1px solid var(--line);color:var(--fg)}
button:disabled{opacity:.5;cursor:wait}
input[type=text],textarea,select{width:100%;background:#11141a;border:1px solid var(--line);color:var(--fg);border-radius:8px;padding:8px 10px;font:13px/1.4 inherit}
textarea{min-height:70px;resize:vertical}
label{font-size:12px;color:var(--mut);display:block;margin:10px 0 4px}
#login{max-width:360px;margin:80px auto;text-align:center}
#login input{margin:12px 0}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:16px;margin-top:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden}
.poster{aspect-ratio:2/3;background:#20242e center/cover no-repeat;display:flex;align-items:center;justify-content:center;color:var(--mut);font-size:12px;padding:8px;text-align:center}
.meta{padding:10px 12px}
.meta .t{font-weight:600;font-size:14px}
.meta .s{color:var(--mut);font-size:12px;margin-top:2px}
.meta button{width:100%;margin-top:8px}
.panel{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px;margin-top:18px}
.panel h2{margin:0 0 4px;font-size:16px}
.panel .slug{color:var(--mut);font-size:12px;margin-bottom:12px}
.row{display:flex;gap:10px;margin-top:14px;flex-wrap:wrap}
.season{display:flex;align-items:center;justify-content:space-between;gap:10px;background:#11141a;border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin-top:8px}
.season .info{font-size:13px}
.season .mut{color:var(--mut);font-size:12px}
.season .left{flex:1;min-width:0}
.season .left input{margin-bottom:4px}
.season .actions{display:flex;gap:8px;flex:none}
.msg{font-size:13px;margin-top:10px}
.msg.ok{color:var(--ok)}
.msg.bad{color:var(--bad)}
.hidden{display:none}
.top{margin-left:auto;display:flex;gap:10px}
</style></head><body>

<div id="login">
  <h1>mathou admin</h1>
  <p style="color:var(--mut)">Jeton admin</p>
  <input id="tok" type="text" placeholder="ADMIN_TOKEN" autocomplete="off">
  <button onclick="doLogin()">Se connecter</button>
  <p id="loginErr" class="msg bad"></p>
</div>

<div id="app" class="hidden">
  <header>
    <b>mathou admin</b>
    <div class="top">
      <button class="ghost" onclick="rebuild()">Rebuild catalogue</button>
      <button class="ghost" onclick="logout()">Deconnexion</button>
    </div>
  </header>
  <main>
    <p id="topMsg" class="msg"></p>
    <div id="grid" class="grid"></div>
    <div id="panel"></div>
  </main>
</div>

<script>
let TOKEN = localStorage.getItem("mathou_admin_token") || "";
let LIB = [];

function headers() { return {"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"}; }

async function api(path, opts) {
  opts = opts || {};
  opts.headers = headers();
  const r = await fetch("/admin/api" + path, opts);
  if (r.status === 401 || r.status === 403) { logout(); throw new Error("jeton invalide"); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || ("HTTP " + r.status));
  return data;
}

function logout() {
  localStorage.removeItem("mathou_admin_token");
  TOKEN = "";
  document.getElementById("app").classList.add("hidden");
  document.getElementById("login").classList.remove("hidden");
}

async function doLogin() {
  TOKEN = document.getElementById("tok").value.trim();
  try {
    await load();
    localStorage.setItem("mathou_admin_token", TOKEN);
    document.getElementById("login").classList.add("hidden");
    document.getElementById("app").classList.remove("hidden");
  } catch (e) {
    document.getElementById("loginErr").textContent = "Echec : " + e.message;
  }
}

function human(n) {
  if (!n) return "?";
  const u = ["o","Ko","Mo","Go","To"]; let x = n, i = 0;
  while (x >= 1024 && i < u.length - 1) { x /= 1024; i++; }
  return x.toFixed(1) + " " + u[i];
}

async function load() {
  const data = await api("/library");
  LIB = data.series;
  renderGrid();
}

function renderGrid() {
  const grid = document.getElementById("grid");
  grid.innerHTML = "";
  for (const s of LIB) {
    const el = document.createElement("div");
    el.className = "card";
    el.innerHTML =
      (s.poster_url
        ? `<div class="poster" style="background-image:url('${esc(s.poster_url)}')"></div>`
        : `<div class="poster">${esc(s.title)}</div>`) +
      `<div class="meta"><div class="t">${esc(s.title)}</div>` +
      `<div class="s">${s.seasons.length} saison(s)</div>` +
      `<button onclick="openPanel('${esc(s.slug)}')">Gerer</button></div>`;
    grid.appendChild(el);
  }
}

function esc(s) { const d = document.createElement("div"); d.textContent = s || ""; return d.innerHTML; }

function openPanel(slug) {
  const s = LIB.find(x => x.slug === slug);
  if (!s) return;
  const panel = document.getElementById("panel");
  const seasons = s.seasons.map(se => {
    const dflt = "Saison " + String(se.season).padStart(2, "0");
    return `
    <div class="season">
      <div class="left">
        <input type="text" id="lbl_${se.season}" value="${esc(se.label || dflt)}" placeholder="${esc(dflt)}">
        <div class="mut">${human(se.size_bytes)}${se.episodes ? " &middot; " + se.episodes + " ep." : ""}</div>
      </div>
      <div class="actions">
        <button class="ghost" onclick="saveSeasonLabel('${esc(slug)}', ${se.season})">Renommer</button>
        <button class="bad" onclick="deleteSeason('${esc(slug)}', ${se.season})">Supprimer</button>
      </div>
    </div>`;
  }).join("") || '<p class="mut">Aucune saison.</p>';
  panel.innerHTML = `
    <div class="panel">
      <h2>${esc(s.title)}</h2>
      <div class="slug">slug: ${esc(s.slug)}</div>
      <label>Titre</label>
      <input type="text" id="f_title" value="${esc(s.title)}">
      <label>Affiche (URL)</label>
      <input type="text" id="f_poster" value="${esc(s.poster_url || "")}">
      <label>Resume</label>
      <textarea id="f_overview">${esc(s.overview || "")}</textarea>
      <div class="row">
        <button onclick="saveSeries('${esc(slug)}')">Enregistrer</button>
        <button class="ghost" onclick="refreshTmdb('${esc(slug)}')">Rafraichir TMDB</button>
        <button class="bad" onclick="deleteSeries('${esc(slug)}')">Supprimer la serie</button>
      </div>
      <div class="row">
        <select id="mergeTarget">
          <option value="">Fusionner dans...</option>
          ${LIB.filter(o => o.slug !== slug).map(o => `<option value="${esc(o.slug)}">${esc(o.title)}</option>`).join("")}
        </select>
        <button class="ghost" onclick="mergeSeries('${esc(slug)}')">Fusionner</button>
      </div>
      <p id="panelMsg" class="msg"></p>
      <h3 style="margin-top:20px;font-size:14px">Saisons</h3>
      ${seasons}
    </div>`;
}

function setMsg(id, text, ok) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  el.className = "msg " + (ok ? "ok" : "bad");
}

async function saveSeries(slug) {
  try {
    await api(`/series/${encodeURIComponent(slug)}`, { method: "PATCH", body: JSON.stringify({
      title: document.getElementById("f_title").value.trim() || null,
      poster_url: document.getElementById("f_poster").value.trim() || null,
      overview: document.getElementById("f_overview").value.trim() || null,
    })});
    await load();
    openPanel(slug);
    setMsg("panelMsg", "Enregistre.", true);
  } catch (e) { setMsg("panelMsg", e.message, false); }
}

async function refreshTmdb(slug) {
  try {
    await api(`/series/${encodeURIComponent(slug)}/tmdb-refresh`, { method: "POST" });
    await load();
    openPanel(slug);
    setMsg("panelMsg", "TMDB rafraichi.", true);
  } catch (e) { setMsg("panelMsg", e.message, false); }
}

async function mergeSeries(slug) {
  const target = document.getElementById("mergeTarget").value;
  if (!target) { setMsg("panelMsg", "Choisis une serie cible.", false); return; }
  if (!confirm("Fusionner cette serie dans la serie choisie ?\nSes saisons seront deplacees (renumerotees si collision) puis cette serie sera retiree du catalogue. Les fichiers B2 ne sont pas touches.")) return;
  try {
    await api(`/series/${encodeURIComponent(slug)}/merge`, { method: "POST", body: JSON.stringify({ into: target }) });
    document.getElementById("panel").innerHTML = "";
    await load();
    setMsg("topMsg", "Fusion effectuee.", true);
  } catch (e) { setMsg("panelMsg", e.message, false); }
}

async function deleteSeries(slug) {
  const purge = confirm("Supprimer aussi les fichiers .zip sur B2 (definitif) ?\nOK = oui, Annuler = juste retirer du catalogue.");
  if (!confirm("Confirme la suppression de la serie '" + slug + "' ?")) return;
  try {
    await api(`/series/${encodeURIComponent(slug)}?purge_b2=${purge}`, { method: "DELETE" });
    document.getElementById("panel").innerHTML = "";
    await load();
    setMsg("topMsg", "Serie supprimee.", true);
  } catch (e) { setMsg("topMsg", e.message, false); }
}

async function saveSeasonLabel(slug, season) {
  const val = document.getElementById(`lbl_${season}`).value.trim();
  try {
    await api(`/seasons/${encodeURIComponent(slug)}/${season}`, { method: "PATCH", body: JSON.stringify({ label: val }) });
    await load();
    openPanel(slug);
    setMsg("panelMsg", "Saison renommee.", true);
  } catch (e) { setMsg("panelMsg", e.message, false); }
}

async function deleteSeason(slug, season) {
  const purge = confirm("Supprimer aussi le .zip sur B2 (definitif) ?\nOK = oui, Annuler = juste retirer du catalogue.");
  if (!confirm("Confirme la suppression de la saison " + season + " ?")) return;
  try {
    await api(`/seasons/${encodeURIComponent(slug)}/${season}?purge_b2=${purge}`, { method: "DELETE" });
    await load();
    openPanel(slug);
    setMsg("panelMsg", "Saison supprimee.", true);
  } catch (e) { setMsg("panelMsg", e.message, false); }
}

async function rebuild() {
  setMsg("topMsg", "Rebuild en cours...", true);
  try {
    await api("/rebuild", { method: "POST" });
    setMsg("topMsg", "Catalogue republie.", true);
  } catch (e) { setMsg("topMsg", e.message, false); }
}

if (TOKEN) {
  load().then(() => {
    document.getElementById("login").classList.add("hidden");
    document.getElementById("app").classList.remove("hidden");
  }).catch(() => {});
}
</script>
</body></html>"""


class SeriesPatch(BaseModel):
    title: str | None = None
    poster_url: str | None = None
    overview: str | None = None


class SeasonPatch(BaseModel):
    label: str | None = None


class MergeBody(BaseModel):
    into: str


def build_router(cfg: Config) -> APIRouter:
    router = APIRouter()

    def require_admin(authorization: str = Header(default=""),
                      x_admin_token: str = Header(default="")) -> None:
        if not cfg.admin_token:
            raise HTTPException(403, "ADMIN_TOKEN non configure dans worker/.env")
        token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        token = token or x_admin_token.strip()
        if token != cfg.admin_token:
            raise HTTPException(401, "Jeton admin invalide")

    admin_dep = [Depends(require_admin)]

    @router.get("/admin", response_class=HTMLResponse)
    async def admin_page() -> str:
        return PAGE

    @router.get("/admin/api/library", dependencies=admin_dep)
    async def api_library() -> dict:
        return {"series": db.library()}

    @router.patch("/admin/api/series/{slug}", dependencies=admin_dep)
    async def api_series_patch(slug: str, body: SeriesPatch):
        if not db.series_get(slug):
            raise HTTPException(404, "Serie inconnue")
        changed = db.series_update(slug, title=body.title, poster_url=body.poster_url,
                                   overview=body.overview)
        if changed:
            storage.publish_catalog(cfg)
        return {"ok": True}

    @router.post("/admin/api/series/{slug}/tmdb-refresh", dependencies=admin_dep)
    async def api_series_tmdb_refresh(slug: str):
        s = db.series_get(slug)
        if not s:
            raise HTTPException(404, "Serie inconnue")
        db.tmdb_delete(s["title"])
        info = library.tmdb_lookup(s["title"], cfg.tmdb_api_key)
        db.series_update(slug, poster_url=info.get("poster_url"), overview=info.get("overview"))
        storage.publish_catalog(cfg)
        return {"ok": True, "tmdb": info}

    @router.post("/admin/api/series/{slug}/merge", dependencies=admin_dep)
    async def api_series_merge(slug: str, body: MergeBody):
        if slug == body.into:
            raise HTTPException(400, "Source et cible identiques")
        if not db.series_get(slug):
            raise HTTPException(404, "Serie source inconnue")
        if not db.series_get(body.into):
            raise HTTPException(404, "Serie cible inconnue")
        db.seasons_merge(slug, body.into)
        storage.publish_catalog(cfg)
        return {"ok": True}

    @router.delete("/admin/api/series/{slug}", dependencies=admin_dep)
    async def api_series_delete(slug: str, purge_b2: bool = False):
        if not db.series_get(slug):
            raise HTTPException(404, "Serie inconnue")
        if purge_b2:
            r = storage.purge_series(cfg, slug)
            if r.returncode != 0:
                raise HTTPException(500, f"Suppression B2 echouee : {r.stderr[-500:]}")
        db.series_delete(slug)
        storage.publish_catalog(cfg)
        return {"ok": True}

    @router.patch("/admin/api/seasons/{slug}/{season}", dependencies=admin_dep)
    async def api_season_patch(slug: str, season: int, body: SeasonPatch):
        if not db.season_get(slug, season):
            raise HTTPException(404, "Saison inconnue")
        changed = db.season_update(slug, season, label=body.label)
        if changed:
            storage.publish_catalog(cfg)
        return {"ok": True}

    @router.delete("/admin/api/seasons/{slug}/{season}", dependencies=admin_dep)
    async def api_season_delete(slug: str, season: int, purge_b2: bool = False):
        se = db.season_get(slug, season)
        if not se:
            raise HTTPException(404, "Saison inconnue")
        if purge_b2 and se.get("zip_name"):
            r = storage.delete_season_zip(cfg, slug, se["zip_name"])
            if r.returncode != 0:
                raise HTTPException(500, f"Suppression B2 echouee : {r.stderr[-500:]}")
        db.season_delete(slug, season)
        storage.publish_catalog(cfg)
        return {"ok": True}

    @router.post("/admin/api/rebuild", dependencies=admin_dep)
    async def api_rebuild():
        r = storage.publish_catalog(cfg)
        if r.returncode != 0:
            raise HTTPException(500, f"Sync catalogue echouee : {r.stderr[-500:]}")
        return {"ok": True}

    return router
