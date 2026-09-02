#!/usr/bin/env python3
"""
check.py - v1 status checker for Spanish public-service and NGO pages.

The point of this file is NOT "does it return 200". Spanish public sites lie:
they serve 200 with a "no encontrada" body, redirect everything to the
homepage, or block you at the WAF. The three classifiers below are the
actual product.

Usage:
    python check.py                 # check everything
    python check.py --only aragon   # substring filter on id
    python check.py --baseline      # (re)record baselines, don't flag
"""

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

DB = "checks.db"
SEEDS = "seeds.json"
OUT = "status.json"

# Politeness: a real UA, and a gap between requests to the same host.
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 "
    "(+link-health-monitor; contacto: aleksandravlukic@gmail.com)"
)
HEADERS = {"User-Agent": UA, "Accept-Language": "es-ES,es;q=0.9,en;q=0.8"}
TIMEOUT = 25
PER_HOST_DELAY = 2.0
FAILURES_BEFORE_ALERT = 3  # don't cry wolf on one flaky night

# --- classifier 1: soft 404 -------------------------------------------------
# 200 OK with a body that says otherwise. Castellano, catalán, gallego,
# euskera and English, because these sites are multilingual.
SOFT_404_MARKERS = [
    "página no encontrada", "pagina no encontrada", "no se ha encontrado",
    "no existe la página", "error 404", "contenido no disponible",
    "la página que busca", "pàgina no trobada", "no s'ha trobat",
    "páxina non atopada", "orria ez da aurkitu",
    "page not found", "the requested url was not found",
]

# --- classifier 2: WAF / bot block -----------------------------------------
# 403 here means "you got blocked", not "the page is dead". Never alert an
# org about these; they are a monitoring problem, not their problem.
BLOCK_MARKERS = [
    "the requested url was rejected", "support id",
    "acceso denegado", "access denied", "incapsula", "cloudflare",
    "checking your browser", "captcha", "solicitud bloqueada",
]

STATUSES = ("OK", "SOFT_404", "DEAD", "BLOCKED", "MOVED", "ERROR")


def schema(con):
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS baseline (
            id TEXT PRIMARY KEY,
            title TEXT, body_hash TEXT, body_len INTEGER,
            final_url TEXT, recorded_at TEXT
        );
        CREATE TABLE IF NOT EXISTS checks (
            id TEXT, checked_at TEXT, status TEXT, http_code INTEGER,
            final_url TEXT, elapsed_ms INTEGER, detail TEXT
        );
        CREATE INDEX IF NOT EXISTS checks_id_time ON checks(id, checked_at);
        """
    )
    con.commit()


def visible_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", html)
    return re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", " ", html)).strip().lower()


def page_title(html: str) -> str:
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
    return re.sub(r"\s+", " ", m.group(1)).strip()[:200] if m else ""


def is_root(url: str) -> bool:
    return urlparse(url).path.strip("/") == ""


def norm_url(url: str) -> str:
    # Session ids and tracking params churn on every visit; compare without them.
    p = urlparse(url)
    return p._replace(params="", query="", fragment="").geturl().rstrip("/")


def classify(seed, resp, base):
    """Return (status, detail). Order matters: block > dead > soft > moved."""
    body = resp.text or ""
    text = visible_text(body)[:6000]

    if resp.status_code in (401, 403, 406, 429) or any(m in text for m in BLOCK_MARKERS):
        return "BLOCKED", f"http {resp.status_code}, likely WAF"

    if resp.status_code >= 400:
        return "DEAD", f"http {resp.status_code}"

    # Redirected from a deep link to the site root = silent content removal.
    if not is_root(seed["url"]) and is_root(resp.url):
        return "SOFT_404", f"deep link collapsed to root: {resp.url}"

    if any(m in text[:2500] for m in SOFT_404_MARKERS):
        return "SOFT_404", "404 wording in a 200 response"

    if base:
        # Body shrank to a fraction of what it was: usually an error stub.
        if base["body_len"] > 2000 and len(body) < base["body_len"] * 0.25:
            return "SOFT_404", f"body {len(body)}b vs baseline {base['body_len']}b"
        if norm_url(base["final_url"]) != norm_url(resp.url):
            return "MOVED", f"now redirects to {resp.url}"

    return "OK", ""


def consecutive_failures(con, sid):
    rows = con.execute(
        "SELECT status FROM checks WHERE id=? ORDER BY checked_at DESC LIMIT ?",
        (sid, FAILURES_BEFORE_ALERT),
    ).fetchall()
    # ERROR counts too: dead DNS or refused connections are the strongest
    # death signal there is. BLOCKED and MOVED never count.
    return sum(1 for (s,) in rows if s in ("DEAD", "SOFT_404", "ERROR"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--baseline", action="store_true")
    args = ap.parse_args()

    seeds = json.load(open(SEEDS, encoding="utf-8"))["sources"]
    if args.only:
        seeds = [s for s in seeds if args.only in s["id"]]

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    schema(con)
    now = datetime.now(timezone.utc).isoformat()
    last_hit, results = {}, []

    for seed in seeds:
        host = urlparse(seed["url"]).netloc
        if host in last_hit:
            gap = PER_HOST_DELAY - (time.time() - last_hit[host])
            if gap > 0:
                time.sleep(gap)

        row = con.execute("SELECT * FROM baseline WHERE id=?", (seed["id"],)).fetchone()
        base = dict(row) if row else None

        started = time.time()
        try:
            resp = requests.get(seed["url"], headers=HEADERS, timeout=TIMEOUT,
                                allow_redirects=True)
            # No charset in the header means requests decodes as latin-1; a
            # UTF-8 page then turns to mojibake and the accented soft-404
            # markers can never match. Sniff instead.
            if "charset" not in resp.headers.get("Content-Type", "").lower():
                resp.encoding = resp.apparent_encoding or "utf-8"
            status, detail = classify(seed, resp, base)
            code, final = resp.status_code, resp.url
            body = resp.text or ""
        except requests.RequestException as e:
            status, detail = "ERROR", type(e).__name__
            code, final, body = 0, seed["url"], ""

        last_hit[host] = time.time()
        elapsed = int((time.time() - started) * 1000)

        con.execute(
            "INSERT INTO checks VALUES (?,?,?,?,?,?,?)",
            (seed["id"], now, status, code, final, elapsed, detail),
        )

        # --baseline may also re-baseline a MOVED site (accept its new home).
        ok_to_record = status == "OK" or (args.baseline and status == "MOVED")
        if ok_to_record and (args.baseline or not base):
            con.execute(
                "INSERT OR REPLACE INTO baseline VALUES (?,?,?,?,?,?)",
                (seed["id"], page_title(body),
                 hashlib.sha256(body.encode("utf-8", "ignore")).hexdigest(),
                 len(body), final, now),
            )

        fails = consecutive_failures(con, seed["id"])
        alert = not args.baseline and fails >= FAILURES_BEFORE_ALERT
        results.append({**seed, "status": status, "http": code, "detail": detail,
                        "final_url": final, "elapsed_ms": elapsed,
                        "checked_at": now, "alert": alert})

        flag = "!" if alert else " "
        print(f"{flag} {status:<9} {seed['id']:<24} {code:<4} {elapsed:>6}ms  {detail}")

    con.commit()
    json.dump({"generated_at": now, "results": results},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    counts = {s: sum(1 for r in results if r["status"] == s) for s in STATUSES}
    print("\n" + "  ".join(f"{k}={v}" for k, v in counts.items() if v))
    print(f"-> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
