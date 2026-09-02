#!/usr/bin/env python3
"""
build_site.py - generate the static site into ./site/

One page per monitored service, titled the way people actually search
("¿Funciona X hoy?"), with the current status baked into the HTML so it
does not depend on JavaScript, plus sitemap.xml and robots.txt.

Env:
    BASE_URL   canonical origin, e.g. https://estado-sedes.es
               Empty (default) -> relative links and <meta noindex> on every
               page, so a github.io preview never gets indexed by accident.
    SITE_NAME  brand shown in titles (default: the dashboard's name).
"""

import html
import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

BASE = os.environ.get("BASE_URL", "").rstrip("/")
SITE_NAME = os.environ.get("SITE_NAME", "¿Está caída la sede?")
OUT = "site"
MADRID = ZoneInfo("Europe/Madrid")
HISTORY_ROWS = 14

STATUS_ES = {
    "OK": ("Accesible", "ok"),
    "DEAD": ("Caída", "bad"),
    "SOFT_404": ("Contenido desaparecido", "bad"),
    "MOVED": ("Ha cambiado de sitio", "warn"),
    "ERROR": ("Error de conexión", "err"),
    "BLOCKED": ("No verificable", "neutral"),
}

CSS = """
:root{color-scheme:light;--page:#f2f5f7;--surface:#fcfdfe;--ink:#111820;--ink-2:#4b5763;
--muted:#84909b;--hairline:#dfe6ec;--accent:#14508c;--good:#0ca30c;--warn:#fab219;
--serious:#ec835a;--critical:#d03b3b;--good-bg:rgba(12,163,12,.10);--warn-bg:rgba(250,178,25,.14);
--serious-bg:rgba(236,131,90,.13);--critical-bg:rgba(208,59,59,.10);--neutral-bg:rgba(132,144,155,.13)}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--page:#0d1116;
--surface:#151b22;--ink:#edf1f5;--ink-2:#a9b4bf;--muted:#78838e;--hairline:#27303a;--accent:#7fb1e8;
--good-bg:rgba(12,163,12,.16);--warn-bg:rgba(250,178,25,.14);--serious-bg:rgba(236,131,90,.15);
--critical-bg:rgba(208,59,59,.18);--neutral-bg:rgba(132,144,155,.16)}}
:root[data-theme="dark"]{color-scheme:dark;--page:#0d1116;--surface:#151b22;--ink:#edf1f5;
--ink-2:#a9b4bf;--muted:#78838e;--hairline:#27303a;--accent:#7fb1e8;--good-bg:rgba(12,163,12,.16);
--warn-bg:rgba(250,178,25,.14);--serious-bg:rgba(236,131,90,.15);--critical-bg:rgba(208,59,59,.18);
--neutral-bg:rgba(132,144,155,.16)}
*{box-sizing:border-box}body{margin:0;background:var(--page);color:var(--ink);
font:15px/1.55 "Archivo",system-ui,-apple-system,"Segoe UI",sans-serif}
.cinta{height:8px;background:linear-gradient(to bottom,#aa151b 0 2px,#f1bf00 2px 6px,#aa151b 6px 8px)}
.wrap{max-width:760px;margin:0 auto;padding:32px 20px 56px}
a{color:var(--accent)}.mono{font-family:"IBM Plex Mono",ui-monospace,monospace}
.crumb{font-size:13px;margin:0 0 18px}.crumb a{text-decoration:none}
h1{font-family:"Archivo Black","Archivo",sans-serif;font-size:clamp(24px,4.5vw,34px);
line-height:1.1;margin:0 0 18px;text-wrap:balance}
.card{background:var(--surface);border:1px solid var(--hairline);border-radius:10px;
padding:18px 22px;margin-bottom:18px}
.pill{display:inline-flex;align-items:center;gap:8px;padding:5px 14px;border-radius:99px;
font-size:15px;font-weight:700}
.dot{width:10px;height:10px;border-radius:50%;display:inline-block}
.pill.ok{background:var(--good-bg)}.dot.ok{background:var(--good)}
.pill.bad{background:var(--critical-bg)}.dot.bad{background:var(--critical)}
.pill.warn{background:var(--warn-bg)}.dot.warn{background:var(--warn)}
.pill.err{background:var(--serious-bg)}.dot.err{background:var(--serious)}
.pill.neutral{background:var(--neutral-bg)}.dot.neutral{background:var(--muted)}
.stamp-line{font-size:13px;color:var(--ink-2);margin:10px 0 0}
.detail{font-size:13.5px;color:var(--ink-2);margin:8px 0 0}
.lede{color:var(--ink-2);max-width:62ch}
h2{font-size:16px;margin:0 0 10px}
.btn{display:inline-block;background:var(--accent);color:var(--surface);text-decoration:none;
padding:9px 18px;border-radius:6px;font-weight:600;margin-top:6px}
ul{margin:8px 0;padding-left:20px}li{margin-bottom:6px;color:var(--ink-2)}
table{border-collapse:collapse;width:100%;font-size:13px}
th{text-align:left;color:var(--muted);font-weight:600;padding:5px 10px 5px 0;
border-bottom:1px solid var(--hairline)}
td{padding:6px 10px 6px 0;border-bottom:1px solid var(--hairline);color:var(--ink-2);
font-variant-numeric:tabular-nums}
.st{font-weight:600}.st.ok{color:var(--good)}.st.bad{color:var(--critical)}
.st.warn{color:#b98200}.st.err{color:var(--serious)}.st.neutral{color:var(--muted)}
footer{font-size:12.5px;color:var(--muted);margin-top:26px;max-width:70ch}
"""

FONTS = ('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
         'family=Archivo:wght@400;600;700&family=Archivo+Black&family=IBM+Plex+Mono&display=swap">')


def e(s):
    return html.escape(str(s), quote=True)


def madrid(iso):
    d = datetime.fromisoformat(iso).astimezone(MADRID)
    return d.strftime("%-d/%-m/%Y a las %H:%M")


def ago(iso):
    mins = int((datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds() // 60)
    if mins < 60:
        return f"hace {max(mins, 0)} minutos"
    if mins < 2880:
        return f"hace {mins // 60} horas"
    return f"hace {mins // 1440} días"


def answer_sentence(r):
    """The direct answer, first thing on the page. Facts only."""
    label = STATUS_ES[r["status"]][0]
    when = ago(r["checked_at"])
    if r["status"] == "OK":
        return (f"Sí: la página oficial respondió con normalidad cuando la "
                f"comprobamos {when} ({r['elapsed_ms']} ms).")
    if r["status"] in ("DEAD", "SOFT_404"):
        return (f"Parece que no: nuestra última comprobación, {when}, "
                f"la encontró con problemas ({label.lower()}).")
    if r["status"] == "MOVED":
        return f"La página ha cambiado de dirección; lo detectamos {when}."
    if r["status"] == "ERROR":
        return (f"No pudimos conectar {when}. Puede ser un problema del sitio "
                f"o de nuestra red; solo lo damos por caído tras tres fallos seguidos.")
    return (f"No podemos comprobarla de forma automática: el sitio bloquea a los "
            f"robots de monitorización. A ti probablemente te funcione ({when}).")


def alternatives(r):
    items = []
    if r["level"] == "AGE":
        items.append("Llama al <strong>060</strong>, el teléfono general de la "
                     "administración del Estado.")
        items.append('Busca el trámite en el <a href="https://administracion.gob.es/" '
                     'rel="noopener">Punto de Acceso General</a>, que a veces ofrece '
                     "una vía alternativa.")
    elif r["level"] == "LOCAL":
        items.append("Llama al <strong>010</strong>, el teléfono del Ayuntamiento "
                     "de Zaragoza.")
    items.append("Las sedes suelen recuperarse en unas horas; volvemos a comprobar "
                 "esta página automáticamente.")
    if r["status"] == "MOVED" and r.get("final_url") and r["final_url"] != r["url"]:
        items.insert(0, f'Ahora redirige a <a href="{e(r["final_url"])}" rel="noopener">'
                        f"{e(r['final_url'])}</a>.")
    return items


def history_rows(con, sid):
    rows = con.execute(
        "SELECT checked_at, status, http_code, elapsed_ms FROM checks "
        "WHERE id=? ORDER BY checked_at DESC LIMIT ?", (sid, HISTORY_ROWS)).fetchall()
    out = []
    for when, status, code, ms in rows:
        label, cls = STATUS_ES.get(status, (status, "neutral"))
        out.append(f"<tr><td>{e(madrid(when))}</td>"
                   f'<td class="st {cls}">{e(label)}</td>'
                   f"<td>{code or '—'}</td><td>{ms} ms</td></tr>")
    return "".join(out)


def page(r, con):
    label, cls = STATUS_ES[r["status"]]
    canon = f"{BASE}/sede/{r['id']}/" if BASE else ""
    robots = "" if BASE else '<meta name="robots" content="noindex">\n'
    canonical = f'<link rel="canonical" href="{e(canon)}">\n' if BASE else ""
    # 'ask' phrases the question the way people search it
    # ("la cita previa de extranjería"), falling back to the organism name.
    ask = r.get("ask") or f"la página de {r['org']}"
    title = f"¿Funciona {r.get('ask') or r['org']} hoy? Estado comprobado — {SITE_NAME}"
    meta_desc = (f"{r.get('desc', '')} Comprobamos el enlace oficial con peticiones "
                 f"reales. Última comprobación: {madrid(r['checked_at'])}.")
    jsonld = json.dumps({
        "@context": "https://schema.org", "@type": "WebPage",
        "name": f"Estado de {r['org']}", "inLanguage": "es",
        "dateModified": r["checked_at"],
        **({"url": canon} if canon else {}),
    }, ensure_ascii=False)
    alts = "".join(f"<li>{a}</li>" for a in alternatives(r))
    hist = history_rows(con, r["id"])
    detail = (f'<p class="detail">{e(r["detail"])}</p>' if r.get("detail") else "")
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<meta name="description" content="{e(meta_desc)}">
{robots}{canonical}<meta property="og:title" content="{e(title)}">
<meta property="og:description" content="{e(meta_desc)}">
<meta property="og:type" content="website">
{FONTS}
<style>{CSS}</style>
<script type="application/ld+json">{jsonld}</script>
</head>
<body>
<div class="cinta" aria-hidden="true"></div>
<div class="wrap">
<p class="crumb"><a href="../../">← Todas las sedes</a></p>
<h1>¿Está caída {e(ask)}?</h1>
<div class="card">
  <span class="pill {cls}"><span class="dot {cls}"></span>{e(label)}</span>
  <p class="stamp-line">Última comprobación: <span class="mono">{e(madrid(r['checked_at']))}</span>
  (hora peninsular, {e(ago(r['checked_at']))}) · respondió en {r['elapsed_ms']} ms</p>
  <p class="lede">{e(answer_sentence(r))}</p>
  {detail}
</div>
<div class="card">
  <h2>¿Qué es esta página?</h2>
  <p class="lede">{e(r.get('desc', ''))}</p>
  <a class="btn" href="{e(r['url'])}" rel="noopener">Ir a la página oficial</a>
</div>
<div class="card">
  <h2>Si no te funciona</h2>
  <ul>{alts}</ul>
</div>
<div class="card">
  <h2>Últimas comprobaciones</h2>
  <table>
  <tr><th>Cuándo</th><th>Estado</th><th>HTTP</th><th>Respuesta</th></tr>
  {hist}
  </table>
</div>
<footer>Monitor independiente, no afiliado a la administración. Comprobamos el enlace
oficial con peticiones reales y respetuosas. «No verificable» significa que el sitio
bloquea a nuestro robot, no que esté caído. Nunca damos consejos sobre trámites: solo
te decimos si el enlace oficial responde y adónde ir.</footer>
</div>
</body>
</html>
"""


def main():
    seeds = json.load(open("seeds.json", encoding="utf-8"))["sources"]
    status = json.load(open("status.json", encoding="utf-8"))
    by_id = {r["id"]: r for r in status["results"]}
    con = sqlite3.connect("checks.db")

    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT)
    shutil.copy("status.json", os.path.join(OUT, "status.json"))

    # Dashboard: turn on links to the per-service pages when served as a site.
    dash = open("index.html", encoding="utf-8").read()
    dash = dash.replace("var SEDE_PAGES = false", "var SEDE_PAGES = true")
    open(os.path.join(OUT, "index.html"), "w", encoding="utf-8").write(dash)

    urls = []
    for seed in seeds:
        r = by_id.get(seed["id"])
        if not r:
            continue
        d = os.path.join(OUT, "sede", seed["id"])
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "index.html"), "w", encoding="utf-8").write(page(r, con))
        urls.append((f"/sede/{seed['id']}/", r["checked_at"],
                     "hourly" if seed.get("watch") else "daily"))

    if BASE:
        sm = ['<?xml version="1.0" encoding="UTF-8"?>',
              '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
              f"<url><loc>{BASE}/</loc><lastmod>{status['generated_at']}</lastmod>"
              "<changefreq>hourly</changefreq><priority>1.0</priority></url>"]
        for path, lastmod, freq in urls:
            sm.append(f"<url><loc>{BASE}{path}</loc><lastmod>{lastmod}</lastmod>"
                      f"<changefreq>{freq}</changefreq><priority>0.8</priority></url>")
        sm.append("</urlset>")
        open(os.path.join(OUT, "sitemap.xml"), "w", encoding="utf-8").write("\n".join(sm))
        open(os.path.join(OUT, "robots.txt"), "w", encoding="utf-8").write(
            f"User-agent: *\nAllow: /\n\nSitemap: {BASE}/sitemap.xml\n")
    else:
        # Preview build: keep crawlers out entirely.
        open(os.path.join(OUT, "robots.txt"), "w", encoding="utf-8").write(
            "User-agent: *\nDisallow: /\n")

    print(f"site/: {len(urls)} service pages"
          + (f", sitemap for {BASE}" if BASE else " (preview: noindex)"))


if __name__ == "__main__":
    main()
