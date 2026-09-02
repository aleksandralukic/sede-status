# ¿Dónde voy? — working title (repo: sede-status)

A free tool that answers one question: **"I have this problem. Which public
website do I actually go to?"**

Built in phases, starting from a link-health monitor and ending at a
plain-language router. **Decision 2026-09-02: the monitor ships as a public
product** — one status page per monitored sede ("¿está caída la sede de
extranjería?"), because that is what people actually search for, and it gives
the later router an audience instead of needing one from zero.

---

## The problem

Spanish public administration is not short of information. It is short of
*routing*. People arrive with a situation — a noisy neighbour, a child
support question, an expiring residence card — and have to already know the
name of the procedure and which of four administrative levels owns it before
they can search for anything.

## What already exists (checked 2026-09-02)

**administracion.gob.es (Punto de Acceso General).** The official single entry
point to state, autonomic, local and EU administrations, with procedures
grouped by subject matter. Backed by the 060 phone line. The directory
problem is officially solved on paper.

**SIA (Sistema de Información Administrativa).** The national catalogue of
administrative procedures, existing because Ley 39/2015 art. 21.4 obliges
administrations to publish and maintain the list of procedures under their
competence. Every procedure carries a SIA code, maintained co-responsibly by
AGE, comunidades, local entities and universities. **DIR3** is the companion
inventory of organisational units and offices. Together these are the
procedure → competent body mapping.

**Commercial players.** Entre Trámites (paid advisory + app), Tramit
(municipal SaaS). Both sell transactions, not orientation.

**downdetector.es** covers a handful of big domains via user reports. It
cannot tell "the homepage is up but the cita previa flow is broken" — which
is exactly what a deep-link + soft-404 checker detects.

### The gap

SIA is indexed by *procedure name and competent body*. Citizens arrive with a
*problem*. Nobody has built the translation layer between the two, and that
translation is exactly what an LLM is good at.

### The unfair advantage

The incumbent's own links rot. A router that can show **"verified working 2
hours ago"** has something administracion.gob.es structurally cannot offer —
and it falls out of Phase 1 for free.

---

## Design principle

> Can this tool be wrong without hurting anyone?

- **Route, never advise.** Show the official link and which body owns it, with
  one line of *why*. Never write "you should do X."
- **Show confidence, offer alternatives.** Two or three candidates, never one
  confident answer.
- **Competence is regional.** Neighbour noise is a municipal ordinance and
  differs by city. Region selection is not a v2 feature.

### What we show vs. what we never show

Show: observed facts with timestamps ("broken when checked at 03:12",
"redirects to homepage since Tuesday"), owning body, region, SIA code,
canonical PAG fallback link, history.

Never show: "down right now" beyond our actual check cadence; "down" when we
were merely WAF-blocked (publicly that is "cannot verify"); a single failure
(3 consecutive before surfacing); advice or deadlines; a mirror of the SIA
catalogue; anything about cita previa *availability* (we check the page, not
the appointments).

## Non-goals

- No accounts, no user data, no analytics beyond aggregate counts.
- No legal or procedural advice, ever.
- No "we'll email the org when their link breaks" until Phase 4 at the
  earliest.
- No attempt to automate or scalp cita previa appointments.

---

## Phases

### Phase 0 — SIA spike ✅ DONE 2026-09-02

**Outcome:** No public national SIA export exists (datos.gob.es API returns
zero datasets; PAe downloads are bot-blocked — F5 challenge + CAPTCHA — and
likely SARA-gated). DIR3 *is* open data: 17 XLSX files via datos.gob.es
dataset `e05251701-directorio-comun-de-unidades-organicas-y-oficinas-dir3`,
though the files only download in a real browser (manual, occasional
refresh). Regional fallbacks are thin: only **Madrid's** procedure inventory
CSV carries verified SIA codes
(`datos.madrid.es/dataset/202377-0-inventario-procedimientos`); Andalucía's
daily CSV/JSON API carries DIR3 codes but not SIA; Zaragoza, Málaga, Galicia,
Catalunya confirmed without; Castilla y León has a `numerosia` field that is
null in all records.

**The finding that matters:** PAG trámite detail pages are stable,
server-rendered, and keyed directly by SIA code —
`administracion.gob.es/pagFront/buscadoractuaciones/detalleActuacion.htm?codSia=N`.
So Phase 2 proceeds by hand-tagging seeds with SIA codes and *verifying* each
against its PAG page, respecting robots.txt (crawl-delay 60, 1 req/min,
visit window 01:00–06:45 GMT). Bulk catalogue scraping is out; per-seed
verification is in. Bonus: every SIA-tagged seed gets the PAG page as a
canonical fallback link when the ministry's own URL rots.

### Phase 1 — Public status pages (in progress)

`check.py` + `seeds.json` written and debugged. The product is one page per
monitored sede, URL and title matching real searches ("sede extranjería no
funciona"), showing current status, history, and last-verified timestamp.

Three classifiers, unchanged:

| Failure | Why status codes miss it | Handling |
|---|---|---|
| Soft 404 | 200 with a "no encontrada" body, or a blanket redirect to the homepage | content markers in 5 languages + baseline body-length diff + root-collapse detection |
| WAF block | 403 means "blocked me", not "dead" | block markers, classified as `BLOCKED`, shown publicly as "cannot verify", never alerted |
| Flake | slow site reads as an outage | 3 consecutive failures before surfacing; ERROR (dead DNS, refused conn) counts |

**Cadence is tiered, not daily:** the notoriously flaky high-criticality
sedes get checked every 30–60 min (they are *not* on administracion.gob.es,
whose robots.txt forbids that); everything else daily. administracion.gob.es
seeds run inside the 01:00–06:45 GMT window only.

**Exit criteria:** 7 consecutive daily runs with under 3 false positives, and
a static site of per-sede pages reading `status.json`.

### Phase 2 — Tagging (one week) — UNBLOCKED by Phase 0

Annotate the monitored URLs with SIA code, DIR3 organism, administrative
level and region. SIA codes hand-tagged, then verified against the PAG
detail page on a polite nightly job. DIR3 org codes from the XLSX dump.

**Exit criteria:** every seed has a level and region; at least 60% carry a
SIA or DIR3 identifier.

### Phase 3 — Plain-language front door

Free-text box. An LLM maps the described situation onto the tag vocabulary and
returns 2–3 candidate official links, each with the owning body, why it owns
it, and a freshness stamp from Phase 1. Lands on / links to the Phase 1
status pages, which by then have their own search traffic.

**Exit criteria:** 30 hand-written real-world situations, correct
administration identified in 25 of them, evaluated by someone who is not the
author.

---

## Stack

Python + `requests` + SQLite + GitHub Actions cron (tiered: hourly for the
flaky set, daily for the rest, night-window for administracion.gob.es).
Static frontend reading `status.json`.

## Files

- `seeds.json` — 58 sources: 33 AGE, 12 CCAA, 3 local (Zaragoza), 10 NGO,
  tagged against an 18-term situation vocabulary. **Unverified** until the
  first smoke-test run; entries flagged `VERIFY` in `notes` are the least
  certain.
- `check.py` — the checker. Fixed 2026-09-02: sqlite row-factory crash,
  charset sniffing (mojibake blinded the soft-404 markers), bare "not found"
  marker removed, ERROR counts toward alerting, MOVED compares
  session-param-normalised URLs, `--baseline` no longer flags.
- `requirements.txt`

## Open questions

- [x] Phase 0 outcome — see above
- [ ] `robots.txt` compliance: check the flaky-tier hosts before choosing
      their cadence (administracion.gob.es already known: 1 req/min,
      01:00–06:45 GMT only)
- [ ] Name and domain (working title "¿Dónde voy?", repo `sede-status`)
- [ ] Licence — AGPL keeps a public-interest tool public
- [ ] Does the freshness signal need a "last known good URL" archive, or is
      "this is broken" enough? (Phase 0 partial answer: the PAG codSia page
      is a free canonical fallback for anything SIA-tagged)
- [ ] `admin-electronica` seed will sit permanently BLOCKED (hard F5/CAPTCHA)
      — it is the WAF test case; mark it expected-blocked so it never reads
      as noise
