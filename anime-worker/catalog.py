"""Genere le site statique du catalogue dans un dossier local.
Pas de dependance : HTML construit a la main, un seul style.css.
Structure produite :
    <out>/index.html                 grille des series
    <out>/<slug>/index.html          saisons d'une serie (liens .zip)
    <out>/assets/style.css
Liens relatifs -> marche tel quel une fois pousse sur b2:<bucket>/catalog/.
"""
from __future__ import annotations

import html
import shutil
from pathlib import Path

import db

STYLE = """
:root { color-scheme: dark; --bg:#0f1115; --card:#171a21; --line:#262b36; --fg:#e7e9ee; --mut:#9aa3b2; --acc:#5b8def; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.5 system-ui,Segoe UI,Roboto,sans-serif; }
a { color:inherit; text-decoration:none; }
header { padding:18px 24px; border-bottom:1px solid var(--line); position:sticky; top:0; background:var(--bg); }
header .home { font-weight:700; letter-spacing:.5px; }
header .crumb { color:var(--mut); }
main { padding:24px; max-width:1200px; margin:0 auto; }
h1 { font-size:20px; margin:0 0 20px; }
.grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(160px,1fr)); gap:18px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:12px; overflow:hidden; transition:border-color .15s; }
.card:hover { border-color:var(--acc); }
.poster { aspect-ratio:2/3; background:#20242e center/cover no-repeat; display:flex; align-items:center; justify-content:center; color:var(--mut); font-size:13px; padding:8px; text-align:center; }
.card .meta { padding:10px 12px; }
.card .t { font-weight:600; font-size:14px; line-height:1.3; }
.card .s { color:var(--mut); font-size:12px; margin-top:3px; }
.seasons { display:flex; flex-direction:column; gap:10px; margin-top:16px; }
.season { display:flex; align-items:center; justify-content:space-between; gap:16px; background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 16px; }
.season .dl { background:var(--acc); color:#fff; padding:8px 14px; border-radius:8px; font-weight:600; font-size:13px; white-space:nowrap; }
.season .info { color:var(--mut); font-size:13px; }
.search { width:100%; max-width:420px; display:block; background:var(--card); border:1px solid var(--line); color:var(--fg); border-radius:10px; padding:10px 14px; font:14px inherit; margin-bottom:18px; }
.hero { display:flex; gap:20px; margin-bottom:8px; }
.hero .poster { width:150px; flex:none; border-radius:10px; }
.hero p { color:var(--mut); margin:8px 0 0; }
.empty { color:var(--mut); }
""".strip()


def _esc(s: str | None) -> str:
    return html.escape(s or "")


def _human(nbytes: int | None) -> str:
    x = float(nbytes or 0)
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if x < 1024 or unit == "To":
            return f"{x:.1f} {unit}"
        x /= 1024
    return f"{x:.1f} To"


_SITE_NAME = "aburame"


def _page(title: str, body: str, depth: int) -> str:
    up = "../" * depth
    return (
        '<!doctype html><html lang="fr"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{_esc(title)}</title>"
        f'<link rel="stylesheet" href="{up}assets/style.css"></head><body>'
        f'<header><a class="home" href="{up}index.html">{_esc(_SITE_NAME)}</a>'
        + (f' <span class="crumb">/ {_esc(title)}</span>' if depth else "")
        + f"</header><main>{body}</main></body></html>"
    )


def _poster_div(url: str | None, fallback: str) -> str:
    if url:
        return f'<div class="poster" style="background-image:url(&quot;{_esc(url)}&quot;)"></div>'
    return f'<div class="poster">{_esc(fallback)}</div>'


def _index_html(series: list[dict]) -> str:
    if not series:
        return _page("mathou", '<h1>Catalogue</h1><p class="empty">Rien pour l\'instant.</p>', 0)
    cards = []
    for s in series:
        n = len(s["seasons"])
        cards.append(
            f'<a class="card" data-title="{_esc(s["title"].lower())}" href="{_esc(s["slug"])}/index.html">'
            + _poster_div(s.get("poster_url"), s["title"])
            + f'<div class="meta"><div class="t">{_esc(s["title"])}</div>'
            + f'<div class="s">{n} saison{"s" if n > 1 else ""}</div></div></a>'
        )
    search = (
        '<input class="search" id="q" type="search" placeholder="Rechercher une serie...">'
        '<script>'
        'document.getElementById("q").addEventListener("input",function(e){'
        'var q=e.target.value.trim().toLowerCase();'
        'document.querySelectorAll("#grid .card").forEach(function(el){'
        'el.style.display = el.dataset.title.indexOf(q) !== -1 ? "" : "none";});});'
        "</script>"
    )
    body = f'<h1>Catalogue ({len(series)})</h1>{search}<div class="grid" id="grid">{"".join(cards)}</div>'
    return _page(_SITE_NAME, body, 0)


def _series_html(s: dict) -> str:
    hero = (
        '<div class="hero">'
        + _poster_div(s.get("poster_url"), s["title"])
        + f'<div><h1>{_esc(s["title"])}</h1>'
        + (f"<p>{_esc(s['overview'])}</p>" if s.get("overview") else "")
        + "</div></div>"
    )
    rows = []
    for se in sorted(s["seasons"], key=lambda x: x["season"] or 0):
        num = se["season"]
        label = se.get("label") or (f"Saison {num:02d}" if num is not None else "Saison ?")
        eps = f' &middot; {se["episodes"]} ep.' if se.get("episodes") else ""
        rows.append(
            f'<div class="season"><div><b>{_esc(label)}</b>'
            f'<div class="info">{_human(se.get("size_bytes"))}{eps}</div></div>'
            f'<a class="dl" href="{_esc(se["download_url"])}">Telecharger le .zip</a></div>'
        )
    body = hero + f'<div class="seasons">{"".join(rows) or "<p class=empty>Aucune saison.</p>"}</div>'
    return _page(s["title"], body, 1)


def build(out_dir: Path, site_name: str = "aburame") -> None:
    global _SITE_NAME
    _SITE_NAME = site_name
    series = db.library()
    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "assets").mkdir(parents=True, exist_ok=True)
    (out_dir / "assets" / "style.css").write_text(STYLE, encoding="utf-8")
    (out_dir / "index.html").write_text(_index_html(series), encoding="utf-8")
    for s in series:
        d = out_dir / s["slug"]
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.html").write_text(_series_html(s), encoding="utf-8")
