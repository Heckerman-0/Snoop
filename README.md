# 🔍 Snoop

**The only snoop you'll ever invite in.**

[![Python](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey.svg)](#supported-platforms)
[![No Telemetry](https://img.shields.io/badge/telemetry-none-brightgreen.svg)](#privacy-guarantees)
[![No Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen.svg)](#requirements)

Snoop is a single-file privacy auditor for macOS, Linux, and Windows. It scans your system, files, and browsers for privacy issues, then tells you exactly how to fix what it finds.

Run it from the CLI for a quick one-shot report, or launch the built-in **web dashboard** to see your score over time, browse threats, chart severity trends, and schedule automatic audits.

It runs locally. It never phones home. You can read the whole thing in one sitting.

```
  ╔══════════════════════════════════════════════════════════╗
  ║  Snoop v2.0.0                                            ║
  ║  The only snoop you'll ever invite in.                   ║
  ║  Local-only · Nothing leaves this machine.               ║
  ╚══════════════════════════════════════════════════════════╝

  ════════════════════════════════════════════════════════════
    SYSTEM SCAN
  ════════════════════════════════════════════════════════════
    [i] OS: Darwin 23.4.0 (arm64)
    [+] Firewall is enabled (globalstate=1)
    [!] Disk encryption is OFF (FileVault is Off)
    [!] 4 listening port(s) found:
        127.0.0.1:5000
        0.0.0.0:6379 [HIGH RISK] Redis
        0.0.0.0:27017 [HIGH RISK] MongoDB
        0.0.0.0:8080

  ════════════════════════════════════════════════════════════
    SCORE
  ════════════════════════════════════════════════════════════
    Privacy score: 55/100  (grade F)

  ════════════════════════════════════════════════════════════
    ADVICE
  ════════════════════════════════════════════════════════════
    Priority findings:
      [HIGH] Disk is not encrypted. Anyone with physical access can read your data.
      [HIGH] 2 risky port(s) listening — 6379, 27017

    Recommended actions:
       1. macOS: System Settings → Privacy & Security → FileVault → Turn On.
       2. Bind services like Redis/MongoDB/MySQL to 127.0.0.1, or firewall the ports.
       3. Rotate any exposed credentials immediately...
```

---

## Table of contents

- [What's new in v2.0](#whats-new-in-v20)
- [What it checks](#what-it-checks)
- [Quick start](#quick-start)
- [Command reference](#command-reference)
- [The dashboard](#the-dashboard)
- [Scheduling audits](#scheduling-audits)
- [History and reports](#history-and-reports)
- [Supported platforms](#supported-platforms)
- [Privacy guarantees](#privacy-guarantees)
- [Understanding the score](#understanding-the-score)
- [Where your data lives](#where-your-data-lives)
- [JSON output format](#json-output-format)
- [Safety notes](#safety-notes)
- [Limitations](#limitations)
- [Troubleshooting](#troubleshooting)
- [FAQ](#faq)
- [Contributing](#contributing)
- [Roadmap](#roadmap)
- [License](#license)
- [Disclaimer](#disclaimer)

---

## What's new in v2.0

Version 2.0 turns Snoop from a one-shot CLI into a full privacy-monitoring tool. Everything is still a single Python file.

| Feature | What it does | How to use |
|---|---|---|
| 🌐 **Web dashboard** | Live score gauge, threat list, and charts in your browser | `python snoop.py --dashboard` |
| 📊 **Charts** | Score gauge, severity bars, category breakdown, score-over-time line chart | Automatic — visible in the dashboard |
| 🗓️ **Scheduler** | Automatic recurring audits — pick interval, scan type, path | Toggle in the dashboard, or `--schedule-every` for headless mode |
| 🕓 **History** | Every scan is saved locally so you can see progress over time | Automatic — view with `--history` |
| ⚡ **Scan now** | Trigger a fresh scan from the dashboard UI | "Scan Now" button |
| 🔌 **HTTP API** | Every dashboard action is a REST endpoint — script it, wire it into CI | See [Dashboard API](#dashboard-api) |
| 🏷️ **Threats** | Flattened, sorted list of every finding across all categories | Visible in the dashboard table |

All v1 CLI flags still work exactly as before. Nothing broke.

---

## What it checks

Snoop looks at three areas: your system, your files, and your browsers.

### 🖥️ System

| Check | Why it matters |
|---|---|
| Firewall state | A disabled firewall exposes every listening service to your network. |
| Disk encryption (FileVault / BitLocker / LUKS) | Without it, anyone with physical access can read everything on the drive. |
| Listening ports | Every open port is attack surface. |
| Risky ports | Redis, MongoDB, RDP, VNC, SMB, Telnet and friends ship with weak or no auth and are actively scanned for. |
| Running processes | A snapshot of what's live; highlights memory-heavy processes. |
| Auto-login | Boots straight into your session with no password. |
| Guest account | Temporary access for anyone who touches the machine. |

### 📁 Files

| Check | Why it matters |
|---|---|
| Sensitive file names | `.env`, `id_rsa`, `.aws/credentials`, `.bash_history`, `*.pem`, `*.key` — common exfiltration targets. |
| World-readable files | Any local user or process can read them. |
| World-writable files | Any local user or process can **modify** them — an arbitrary code execution vector. |
| Secret patterns | AWS keys, GitHub tokens, Google API keys, Slack tokens, Stripe live keys, PEM blocks sitting in plaintext. |
| PII patterns | Emails, phones, SSNs, credit cards, IPs — regulated data that needs encryption and access control. |

### 🌐 Browsers

| Check | Why it matters |
|---|---|
| Chrome, Brave, Edge, Chromium, Firefox profiles | Each stores cookies, history, saved passwords. |
| Chromium telemetry & default permissions | Camera, mic, geolocation can be granted without a prompt. |
| Firefox telemetry & DNS-over-HTTPS | Telemetry uploads; DoH off means your resolver sees every domain. |

### 🎯 Advice engine

- **Prioritized** — critical issues first, cosmetic last.
- **OS-specific** — macOS gets macOS instructions, Windows gets Windows.
- **Actionable** — exact menu paths, exact shell commands.

### 📊 Privacy score

0–100, with a letter grade A–F. Track it over time in the dashboard and watch it climb as you fix things.

---

## Quick start

### 1. Get the file

```bash
curl -O https://raw.githubusercontent.com/Heckerman-0/snoop/main/snoop.py
```

Or clone the repo:

```bash
git clone https://github.com/Heckerman-0/snoop.git
cd snoop
```

### 2. (Optional, recommended) Install `psutil`

`psutil` enables richer process inspection and faster port lookup. Without it, Snoop falls back to `netstat` / `ss` / `lsof` — slower but functional.

```bash
pip install psutil
```

### 3. Run your first scan

```bash
python snoop.py
```

That's it. Full scan of your home directory, all checks, report and advice printed to your terminal. The result is also saved to history so the dashboard can chart it later.

### 4. Open the dashboard

```bash
python snoop.py --dashboard
```

A browser tab opens at `http://127.0.0.1:8765/`. You'll see your score, threats, and history. Come back anytime with the same command.

---

## Command reference

Every command in one place, with a plain-English explanation of what it does.

### One-shot scans

```bash
python snoop.py
```
**Default. Runs all three checks (system + files + browser) against your home directory, prints the report, saves the result to history.**

```bash
python snoop.py --scan system
```
**Runs only the system checks.** Fast — takes a second or two. Doesn't walk your file system. Good for a quick "am I exposed?" check.

```bash
python snoop.py --scan files
```
**Runs only the file scan.** Walks your home directory looking for sensitive files, exposed permissions, leaked secrets, and PII.

```bash
python snoop.py --scan browser
```
**Runs only the browser checks.** Detects installed browsers and reads their privacy-relevant config files.

```bash
python snoop.py --scan files --path ~/Documents
```
**Run the file scan against a specific directory instead of `~`.** Useful when you only care about one project, or when the full home scan is too slow.

```bash
python snoop.py --scan files --path ~/code --max-files 5000
```
**Cap the file scan at 5000 files.** Prevents Snoop from crawling huge trees (e.g. `node_modules`, vendored deps). Combine with `--path` to point it somewhere small.

### Output control

```bash
python snoop.py --output snoop-report.json
```
**Save the full report to a JSON file.** Same data the dashboard uses. Good for CI, backups, or piping into other tools.

```bash
python snoop.py --quiet
```
**Only print the score and advice.** No per-check progress lines. Good for scripts and cron jobs.

```bash
python snoop.py --no-color
```
**Disable ANSI colors.** Use when piping to a file, logging, or on terminals that render ANSI badly.

```bash
python snoop.py --no-history
```
**Don't save this scan to `~/.snoop/history/`.** Use when you're testing and don't want to pollute your score history with throwaway runs.

```bash
python snoop.py --max-files 100000
```
**Raise the file cap.** Default is 50,000. Bigger numbers mean slower scans and more thorough coverage.

### History

```bash
python snoop.py --history
```
**Show a table of the last 20 scans.** Columns: ID, when, score, grade, threat count. Color-coded by score. Doesn't run a new scan — just reads what's stored in `~/.snoop/history/`.

Example output:
```
ID                   When                 Score    Grade  Threats
──────────────────────────────────────────────────────────────────
20260910-143211      2026-09-10 14:32     55       F      14
20260909-090000      2026-09-09 09:00     62       D      11
20260908-093245      2026-09-08 09:32     71       C      8
```

### The dashboard

```bash
python snoop.py --dashboard
```
**Launch the local web dashboard.** Opens `http://127.0.0.1:8765/` in your default browser. Runs until you press Ctrl+C. Includes the scheduler, so any schedule you've saved will keep running while this is open.

```bash
python snoop.py --dashboard --port 9000
```
**Use a custom port.** Useful if 8765 is already taken, or if you're tunneling through SSH.

```bash
python snoop.py --dashboard --port 0
```
**Auto-select a free port.** Snoop picks the first open port between 8765 and 8775.

```bash
python snoop.py --dashboard --no-browser
```
**Launch the dashboard without opening a browser.** Useful on a headless server or when you want to open the URL yourself.

### Scheduling

Two ways to run recurring audits: through the dashboard (visual, config-driven) or headless from the command line.

```bash
python snoop.py --schedule-every 6h
```
**Headless scheduler.** Runs a full scan every 6 hours, forever. No dashboard, no browser. Prints a one-line summary per run. Press Ctrl+C to stop.

```bash
python snoop.py --schedule-every 30m
```
**Every 30 minutes.** Interval suffixes: `m` (minutes), `h` (hours), `d` (days). `--schedule-every 2d` = every 2 days.

```bash
python snoop.py --schedule-every 24h --scan system
```
**Combine with `--scan` to schedule a specific scan type.** Useful if you want the cheap system check running hourly but the slow file scan only weekly.

```bash
python snoop.py --schedule-every 24h --scan files --path ~/code --max-files 10000
```
**Headless scheduler scoped to a specific path.** Files only, capped, every day.

### Version and help

```bash
python snoop.py --version
```
**Print the version and exit.**

```bash
python snoop.py --help
```
**Show all flags with descriptions.** Same content as the Command reference section above, formatted for the terminal.

---

## The dashboard

Launch it with:

```bash
python snoop.py --dashboard
```

It opens `http://127.0.0.1:8765/` — a dark-themed single-page app with everything in one view.

### What you'll see

| Panel | What it shows |
|---|---|
| **Privacy Score** | A semicircular gauge, color-coded by grade (green ≥80, yellow ≥60, red <60). Big number, letter grade beneath. |
| **Severity Breakdown** | Horizontal bars for Critical / High / Medium / Low. Instant "how bad is it right now" read. |
| **Categories** | Distribution of threats across system / network / files / browser, as percentages. |
| **Score History** | Line chart of every scan you've run. Hover for a shape of your progress over weeks or months. |
| **Recent Threats** | Every finding from the latest scan, in a sortable-feeling table sorted critical → low. Severity chip, category, title, and the raw detail (file path, port, config key). |
| **Schedule** | Interval, scan type, and path — saved to `~/.snoop/config.json`. |
| **Scan Now** | Kicks off an immediate scan in the background. The dashboard auto-refreshes every 5 seconds so the result appears without you reloading. |

### Dashboard API

Every action in the UI is a plain REST endpoint. Anything you can click, you can script.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | The dashboard HTML |
| `GET` | `/api/status` | Current state: is a scan running, when was the last one, current schedule |
| `GET` | `/api/history?limit=60` | List of scan summaries (id, timestamp, score, grade, threat counts) |
| `GET` | `/api/latest` | Full report of the newest scan |
| `GET` | `/api/report/<id>` | Full report for a specific scan ID |
| `GET` | `/api/schedule` | Current schedule config |
| `POST` | `/api/scan` | Trigger a scan now. Optional JSON body: `{"scan_type": "system", "path": "~/code", "max_files": 5000}` |
| `POST` | `/api/schedule` | Update the schedule. Body: `{"enabled": true, "interval_hours": 24, "scan_type": "all", "path": "~"}` |
| `DELETE` | `/api/history` | Wipe all scan history |

Example: check status from a shell.

```bash
curl -s http://127.0.0.1:8765/api/status | python -m json.tool
```

Example: trigger a scan from a script.

```bash
curl -X POST http://127.0.0.1:8765/api/scan \
  -H "Content-Type: application/json" \
  -d '{"scan_type": "system"}'
```

Example: turn on the scheduler from a script.

```bash
curl -X POST http://127.0.0.1:8765/api/schedule \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "interval_hours": 6, "scan_type": "all", "path": "~"}'
```

### Security of the dashboard

- **Bound to `127.0.0.1` only.** Nothing on your network can reach it — not your phone, not your laptop, not a roommate's machine. Only processes on this machine can connect.
- **No authentication.** That's fine for a loopback-only service; anyone with shell access on your machine can already read your files.
- **No CSRF tokens.** Same reasoning — a malicious page in your browser *could* theoretically trigger a scan via `fetch()` to `127.0.0.1:8765`, but all it can do is start a scan. It can't read your files or exfiltrate anything, because there's no endpoint that returns file contents.
- **If you want remote access**, put it behind an SSH tunnel or a reverse proxy with auth:
  ```bash
  ssh -L 8765:127.0.0.1:8765 you@your-server
  # then open http://127.0.0.1:8765/ locally
  ```

---

## Scheduling audits

### Visual scheduling (dashboard)

1. Launch the dashboard: `python snoop.py --dashboard`
2. Scroll to the **Schedule** card.
3. Set:
   - **Enabled** — toggle on to activate
   - **Every (hours)** — 1, 6, 24, 168 (a week), anything
   - **Scan** — all / system / files / browser
   - **Path** — only applies to file scans
4. Click **Save**.

The scheduler runs in a background thread inside the dashboard process. As long as the dashboard is running, scans happen automatically. When you close the dashboard, scheduling pauses.

You'll see a "Next run" timestamp in the Schedule card. It updates after each run.

### Headless scheduling (no UI)

If you want scheduling without keeping a browser tab open, use `--schedule-every`:

```bash
python snoop.py --schedule-every 6h
```

This runs in the foreground, scanning every 6 hours, printing a one-liner per scan, and saving each to history. It's perfect for:

- Running on a headless server under `tmux` or `screen`
- Running as a `systemd` service or macOS `launchd` job
- A `Docker` entrypoint

Example systemd unit (`~/.config/systemd/user/snoop.service`):

```ini
[Unit]
Description=Snoop privacy auditor
After=network.target

[Service]
ExecStart=/usr/bin/python3 /opt/snoop/snoop.py --schedule-every 24h --quiet
Restart=on-failure
RestartSec=60

[Install]
WantedBy=default.target
```

Then:

```bash
systemctl --user daemon-reload
systemctl --user enable --now snoop
```

### Classic cron

If you'd rather use `cron` to trigger one scan per run (letting cron handle the timing instead of Snoop's internal scheduler):

```cron
# Every day at 9am, save to history and to a dated file
0 9 * * * /usr/bin/python3 /opt/snoop/snoop.py --quiet --output /var/log/snoop-$(date +\%F).json
```

Both approaches are supported. The `--schedule-every` mode is simpler if you just want "run every N hours." Cron is better if you need calendar-based timing (e.g. "every Monday at 9am").

---

## History and reports

Every scan — whether from the CLI, the dashboard's "Scan Now" button, or the scheduler — is saved to `~/.snoop/history/` as a timestamped JSON file. That's what powers the score-over-time chart in the dashboard.

### Where to find it

```bash
ls ~/.snoop/history/
# 20260908-093245.json
# 20260909-090000.json
# 20260910-143211.json
```

### What's in a report

Each file is the complete report — same structure as `--output`. See [JSON output format](#json-output-format) for the schema.

### Managing history

```bash
python snoop.py --history
```
Shows the last 20 scans in a color-coded table.

To delete individual reports:
```bash
rm ~/.snoop/history/20260908-093245.json
```

To wipe everything at once, use the "Clear History" button in the dashboard, or:
```bash
curl -X DELETE http://127.0.0.1:8765/api/history
```

### Getting a report out

```bash
# Latest report as JSON, via the API
curl -s http://127.0.0.1:8765/api/latest | python -m json.tool

# A specific historical report
curl -s http://127.0.0.1:8765/api/report/20260910-143211 | python -m json.tool

# Just the score
curl -s http://127.0.0.1:8765/api/latest | python -c "import json,sys; print(json.load(sys.stdin)['score'])"
```

---

## Supported platforms

| Platform | Support |
|---|---|
| **macOS** 11 (Big Sur) and newer | Full — FileVault, Gatekeeper, SIP, `system_profiler`, guest account |
| **Linux** (Debian, Ubuntu, Fedora, Arch) | Full — LUKS, ufw/firewalld/iptables, GDM/LightDM auto-login |
| **Windows** 10 / 11 | Full — BitLocker, Defender Firewall, `netstat`, registry |

Snoop detects the platform and runs the appropriate checks. Anything that doesn't apply is silently skipped.

The dashboard works identically on all three platforms.

---

## Requirements

- **Python 3.8+**
- **`psutil`** (optional, recommended)
- A modern browser for the dashboard (any browser from the last 5 years)

No other dependencies. The dashboard is served by Python's standard `http.server`, and the charts are hand-rolled SVG — no Chart.js, no React, no CDN fetches.

---

## Privacy guarantees

Snoop was built to audit privacy, so it holds itself to the same standard:

- ✅ **No network calls.** Zero. It never connects to anything.
- ✅ **No telemetry.** Nothing is collected or sent anywhere.
- ✅ **No file uploads.** Your data stays on your machine.
- ✅ **No writes outside `~/.snoop/` and the `--output` file you specify.**
- ✅ **Read-only on scanned files.** It never modifies, deletes, or moves anything.
- ✅ **Dashboard binds loopback only.** Reachable from `127.0.0.1`, nothing else.

You can verify this by reading the source — it's a single file, no imports beyond the Python standard library (plus optional `psutil`).

---

## Understanding the score

The score starts at 100 and subtracts for each finding:

| Finding | Penalty |
|---|---|
| Disk not encrypted | −20 |
| Risky port exposed | −5 each (max −20) |
| Firewall disabled | −15 |
| Auto-login enabled | −10 |
| Files containing secrets | −3 each (max −15) |
| Files containing PII | −2 each (max −10) |
| Sensitive files found | −1 each (max −10) |
| World-writable files | −2 each (max −5) |

| Grade | Score |
|---|---|
| **A** | 90–100 |
| **B** | 80–89 |
| **C** | 70–79 |
| **D** | 60–69 |
| **F** | 0–59 |

The score is a rough prioritization aid, not a certified measurement. A score of 100 doesn't mean you're unhackable — it means the checks in Snoop found nothing to complain about.

Because every scan is saved to history, the dashboard's line chart lets you track the number over time. If you fix a finding and rerun, you'll see the score jump.

---

## Where your data lives

Everything Snoop stores lives under `~/.snoop/`.

```
~/.snoop/
├── config.json              # schedule settings
└── history/
    ├── 20260908-093245.json
    ├── 20260909-090000.json
    └── 20260910-143211.json
```

- **`config.json`** — one JSON object holding your schedule (enabled flag, interval, scan type, path, last/next run timestamps). Edited by the dashboard's Schedule card. Safe to hand-edit; Snoop merges unknown fields instead of overwriting them.
- **`history/*.json`** — one file per scan. Filename is `YYYYMMDD-HHMMSS.json`. Contains the full report (same structure as `--output`).

To reset everything:

```bash
rm -rf ~/.snoop
```

Snoop will recreate the directory and default config on the next run.

---

## JSON output format

`--output` and the history files both use the same schema. Useful for dashboards, CI gates, or tracking over time.

```json
{
  "tool": "snoop",
  "version": "2.0.0",
  "id": "20260910-143211",
  "timestamp": "2026-09-10T14:32:11",
  "platform": "macOS-14.5-arm64",
  "scan_type": "all",
  "score": 55,
  "grade": "F",
  "severity_counts": {
    "critical": 1,
    "high": 6,
    "medium": 4,
    "low": 3
  },
  "system": {
    "os_info": { "os": "Darwin", "release": "23.4.0", "machine": "arm64" },
    "checks": {
      "firewall": { "enabled": true, "details": "globalstate=1" },
      "disk_encryption": { "encrypted": false, "details": "FileVault is Off" },
      "auto_login": { "enabled": false, "details": "" }
    },
    "open_ports": [
      { "address": "0.0.0.0", "port": 6379, "pid": 512 }
    ],
    "processes": { "count": 312, "top": [/* … */] }
  },
  "files": {
    "path": "/Users/alice",
    "files_scanned": 18234,
    "sensitive_files": [
      { "path": "/Users/alice/.ssh/id_rsa", "type": "SSH private key" }
    ],
    "with_secrets": [
      { "path": "/Users/alice/project/.env",
        "types": { "AWS key": 1, "Stripe key": 1 } }
    ],
    "with_pii": [
      { "path": "/Users/alice/notes/customers.csv",
        "types": { "email": 42, "phone": 12 } }
    ],
    "world_readable": ["/Users/alice/public/notes.txt"],
    "world_writable": []
  },
  "browser": {
    "browsers": [
      { "name": "Chrome",
        "path": "/Users/alice/Library/Application Support/Google/Chrome" },
      { "name": "Firefox",
        "path": "/Users/alice/Library/Application Support/Firefox/Profiles" }
    ],
    "issues": [
      { "browser": "Chrome", "profile": "Default",
        "title": "usage statistics enabled" }
    ]
  },
  "threats": [
    { "category": "system", "severity": "critical",
      "title": "Disk encryption off", "detail": "FileVault is Off" },
    { "category": "network", "severity": "high",
      "title": "Risky port 6379 exposed",
      "detail": "0.0.0.0:6379 — Redis" }
  ],
  "advice": {
    "severity": [
      ["HIGH", "Disk is not encrypted. Anyone with physical access can read your data."]
    ],
    "advice": [
      "macOS: System Settings → Privacy & Security → FileVault → Turn On.",
      "Bind services like Redis/MongoDB/MySQL to 127.0.0.1, or firewall the ports."
    ]
  }
}
```

New in v2.0: the `id`, `severity_counts`, and `threats` fields. `threats` is the flattened list the dashboard sorts and displays; `severity_counts` is the aggregate used for the bar chart.

---

## Safety notes

- **Read the source before running it.** It's one file — you can audit it in 15 minutes.
- **Run as your normal user**, not `root` / `sudo`. Elevated privileges let it see more, but aren't required, and running any scanner as root is bad hygiene.
- **The credit-card regex is intentionally broad.** It flags any 13–16 digit number. Treat `credit_card` findings as "review these files," not "you have a card number leak."
- **`~` contains a lot of files.** The default cap is 50,000 files. Use `--max-files` or `--path` to narrow it.
- **Skipped directories.** Snoop skips `.git`, `node_modules`, `__pycache__`, `venv`, `.venv`, `Library`, `AppData`, `$RECYCLE.BIN`, `.snoop`, and other noise. Edit `SKIP_DIRS` if you want them scanned.
- **The dashboard is loopback-only** — no network exposure, no auth needed. See [Security of the dashboard](#security-of-the-dashboard).
- **Only run this on machines you own** or have explicit permission to audit.

---

## Limitations

Being honest about what Snoop doesn't do:

- **It's not a vulnerability scanner.** No CVEs, no exploitability, no missing-patch detection.
- **It doesn't parse application databases.** Browser SQLite files (history, cookies) aren't read — that's a different tool's job.
- **It doesn't monitor continuously.** Between scheduled scans, Snoop isn't watching. It's a snapshot, not a daemon.
- **It's not a malware scanner.** It flags suspicious configuration, not malicious binaries.
- **Secrets detection is regex-based.** Catches common formats (AWS, GitHub, Google, Slack, Stripe, PEM) but not every provider. False positives and false negatives both happen.
- **PII detection is heuristic.** Same caveat: regex, not ML.
- **The dashboard stores reports unencrypted** in `~/.snoop/history/`. If your home directory isn't encrypted (see the disk-encryption check!), those reports — which contain file paths and threat details — are readable by anyone with physical access.

---

## Troubleshooting

**`psutil` import warnings**
Install it: `pip install psutil`. Without it, Snoop falls back to shell commands and runs a bit slower.

**Permission denied errors on files**
Expected. Snoop skips files it can't read and moves on. Run as your normal user — you don't need root.

**Scan takes forever**
Cap it: `--max-files 5000`, or point it at a smaller path: `--path ~/Documents`.

**Firewall check reports "unknown"**
On some Linux distros, no firewall manager is installed at all. That's itself a finding — install `ufw` or `firewalld`.

**Colors look weird on Windows**
Windows Terminal and Windows 10+ cmd.exe support ANSI. If it looks broken, use `--no-color`.

**File scan reports `credit_card` matches that aren't cards**
Yes, the regex is broad (13–16 consecutive digits). It's a "look at this file" signal, not proof.

**Dashboard says "disconnected"**
The dashboard server isn't running, or your browser lost connection. Check that `python snoop.py --dashboard` is still active in a terminal.

**Dashboard port already in use**
Use `--port 9000` (or any other port), or `--port 0` to auto-pick one.

**Scheduler isn't running scans**
Two possibilities:
1. **Dashboard mode**: the schedule only runs while the dashboard is open. If you closed the terminal, it stopped.
2. **Check the config**: open the dashboard, confirm the Enabled checkbox is on and the Next Run timestamp is in the future.

If you want scheduling to survive reboots and terminal closes, use `--schedule-every` under systemd/launchd, or a cron job.

**Scans pile up too fast**
Lower the interval (e.g. `24h` instead of `1h`) or set the scan type to `system` — it's cheap and won't walk your file system.

**History directory is huge**
Each report is a few KB to a few MB depending on how many threats you have. If it's getting big:
```bash
python snoop.py --history   # see what's there
# then delete old ones:
find ~/.snoop/history -name "*.json" -mtime +30 -delete
```

---

## FAQ

**Is this just `lynis` / `spectre` / `Wazuh` with a different name?**
No. Those are full audit frameworks. Snoop is a **single-file, zero-dependency, opinionated** scanner designed to be read in one sitting and run in one command. Different scope.

**Why "Snoop"?**
Because the tool snoops on your machine so nobody else has to. The irony is the brand.

**Does the dashboard send anything anywhere?**
No. It serves a local HTML page from `127.0.0.1` and never makes an outbound connection. Open dev tools → Network tab and watch — you'll see nothing leave.

**Can I access the dashboard from another device?**
Not out of the box — it binds `127.0.0.1` on purpose. To reach it remotely, tunnel over SSH:
```bash
ssh -L 8765:127.0.0.1:8765 you@your-server
```
Then open `http://127.0.0.1:8765/` locally. Don't expose the port directly — there's no auth.

**Why doesn't it check for X?**
Open an issue. If it's a real, checkable privacy risk and the check fits the single-file constraint, it's probably a good addition.

**Can I run it on my work laptop?**
Only if your employer permits it. Corporate machines often have endpoint agents that flag unexpected scanners. Check your policy first.

**Is the score meaningful?**
Loosely. It's a rough prioritization aid. A machine with 100/100 and one unpatched RCE is worse than a machine with 60/100 and everything patched. Treat the score as a nudge, not a verdict.

**Why Python?**
Because it runs everywhere without a build step, and everyone can read it. A Rust or Go version would be faster, but the constraint here is auditability, not speed.

**Can I use this commercially?**
Yes, MIT license. See [License](#license).

---

## Contributing

Contributions are welcome. The bar is:

1. Keep it **single-file**. The whole point is that it's easy to audit and run.
2. **No new required dependencies.** `psutil` is optional; anything else that's non-standard needs to be too. That includes front-end — the dashboard should keep using vanilla JS and hand-rolled SVG.
3. **Add a check for a real risk**, not a theoretical one.
4. **Test on at least two platforms** if the check is platform-specific.
5. **Update this README** if you add flags or change output.

### Adding a new check

Each check is a function that returns a dict. To add one:

1. Write the function — e.g. `check_something()` — following the existing patterns.
2. Call it from `scan_system()`, `scan_directory()`, or `scan_browser()`.
3. Add the finding to the appropriate `findings[...]` key.
4. Wire it into `build_advice()` so it produces a recommendation.
5. Add an entry to `extract_threats()` so it shows up in the dashboard.
6. Update the JSON schema section of this README.

### Reporting a bug

Open an issue with:

- Your OS and Python version
- The exact command you ran
- The full output (redact anything sensitive)
- What you expected to happen

---

## Roadmap

Roughly in priority order:

- [ ] Windows auto-login and guest account checks (currently macOS/Linux only)
- [ ] `known_hosts` entropy and duplicate-key detection
- [ ] Detection of stale browser profiles (unused for 6+ months)
- [ ] `.gitignore` awareness — don't flag secrets in files that are gitignored
- [ ] Optional `--fix` mode that applies safe, reversible fixes with confirmation
- [ ] Export history as CSV or HTML
- [ ] Configurable scoring weights
- [ ] Per-check enable/disable in the dashboard
- [ ] Homebrew / apt / scoop packages

PRs welcome on any of these.

---

## License

MIT License. See [LICENSE](LICENSE) for the full text.

Copyright (c) 2026 Heckerman-0

---

## Disclaimer

Snoop is provided for **educational and defensive purposes only**. It's intended to help individuals audit and improve the privacy and security posture of systems they own or have explicit permission to assess.

**Do not run this on systems you do not own or have authorization to test.** Unauthorized scanning may violate computer misuse laws in your jurisdiction.

The authors take no responsibility for:

- Misuse of this tool
- Any damage, data loss, or legal consequences arising from its use
- The accuracy or completeness of its findings

The privacy score and advice are heuristic and should not be treated as a certified security assessment. For a professional audit, hire a qualified security practitioner.

**You are responsible for your own use of this software.**
