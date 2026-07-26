# Lacanau fire monitor

Watches the Lacanau municipal fire bulletin
([incendie de Saumos](https://www.lacanau.fr/actualite/incendie-en-cours-saumos/))
and sends an **instant push to your phone** whenever the page changes, plus a small
**status dashboard**. Runs free and 24/7 on GitHub Actions — no server, works while
you travel and your PC is off.

> ⚠️ **This is a supplement, not a replacement** for official emergency channels.
> Enable **FR-Alert** on your phone and follow
> [@prefet33 / Préfecture de la Gironde](https://twitter.com/prefet33) for evacuation orders.

## How it works

1. A GitHub Actions job runs every ~15 min.
2. `monitor.py` fetches the page, extracts the article title (`.article-title`) + body
   (`.article-content`), normalizes and `sha256`-hashes it.
3. It compares the hash to `state.json`. On a change it:
   - sends an **urgent** [ntfy.sh](https://ntfy.sh) push (with a text excerpt + tap-to-open link),
   - appends to `history.json`,
   - regenerates `docs/data.json` for the dashboard,
   - commits the updated state back to the repo.
4. `docs/index.html` (GitHub Pages) shows current status + update history.

Extra safety nets: a one-time "monitoring active" ping on first run, a daily
"still watching" heartbeat (so silence is never ambiguous), and a "⚠️ can't reach
the page" alert after 3 consecutive failed fetches.

## Setup (one-time, ~10 min)

1. **Install ntfy** ([iOS](https://apps.apple.com/app/ntfy/id1625396347) /
   [Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy)).
   Subscribe to a **secret** topic, e.g. `lacanau-fire-8f3k9qz2` (pick your own random suffix —
   the topic name is the only thing keeping others out).
2. **Push this repo to GitHub** as a **public** repo (needed for free GitHub Pages;
   nothing here is sensitive — the ntfy topic lives in Actions secrets, not in the code).
3. **Add the secret:** repo → Settings → Secrets and variables → Actions → New repository secret:
   - Name: `NTFY_URL`
   - Value: `https://ntfy.sh/lacanau-fire-8f3k9qz2` (your topic)
4. **Enable the dashboard:** repo → Settings → Pages → Source: `Deploy from a branch`,
   Branch: `main` / `/docs`. Note the published URL (e.g. `https://<you>.github.io/<repo>/`).
5. **Seed it:** repo → Actions → "Lacanau fire monitor" → **Run workflow**.
   You should get a "✅ monitoring active" push within a minute, and a first commit appears.

## Local test (optional, before pushing)

```bash
pip install -r requirements.txt

# Verify your phone push works end-to-end:
NTFY_URL="https://ntfy.sh/lacanau-fire-8f3k9qz2" python monitor.py --test

# Dry run (no NTFY_URL = prints notifications instead of sending):
python monitor.py
```

On Windows PowerShell:

```powershell
$env:NTFY_URL = "https://ntfy.sh/lacanau-fire-8f3k9qz2"
python monitor.py --test
```

## Verifying change detection

Blank the hash and run the workflow again — you should get an urgent 🔥 push:

```bash
# edit state.json → set "last_hash" to ""  (or delete the file), commit, then Run workflow
```

## Tuning

- **Check more often:** edit the cron in `.github/workflows/monitor.yml`
  (`*/10 * * * *`). GitHub cron is best-effort and may lag 5–15 min; ~15 min is the realistic floor.
- **False positives:** if you get pings without real content changes, tighten the selector in
  `extract()` in `monitor.py` (currently `.article-content`).

## Files

| File | Purpose |
|------|---------|
| `monitor.py` | fetch → detect → notify → write state + dashboard data |
| `.github/workflows/monitor.yml` | scheduled runner (+ manual dispatch) |
| `state.json` | last-seen hash / text / timestamps (auto-managed) |
| `history.json` | event log (auto-managed) |
| `docs/index.html` | dashboard (GitHub Pages) |
| `docs/data.json` | dashboard data (auto-generated) |
