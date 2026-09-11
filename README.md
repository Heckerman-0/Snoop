# 🔍 Snoop

**The only snoop you'll ever invite in.**

[![Python](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey.svg)](#supported-platforms)
[![No Telemetry](https://img.shields.io/badge/telemetry-none-brightgreen.svg)](#privacy-guarantees)

Snoop is a single-file Python tool that audits your computer for privacy issues, analyses files for sensitive data, and tells you exactly how to fix what it finds.

It runs locally. It never phones home. You can read the whole thing in one sitting.

```
  ╔══════════════════════════════════════════════════════════╗
  ║  Snoop v1.0.0                                            ║
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

- [What it checks](#what-it-checks)
- [Quick start](#quick-start)
- [Usage](#usage)
- [Supported platforms](#supported-platforms)
- [Privacy guarantees](#privacy-guarantees)
- [Understanding the score](#understanding-the-score)
- [JSON output](#json-output)
- [Safety notes](#safety-notes)
- [Limitations](#limitations)
- [Troubleshooting](#troubleshooting)
- [FAQ](#faq)
- [Contributing](#contributing)
- [Roadmap](#roadmap)
- [License](#license)
- [Disclaimer](#disclaimer)

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

0–100, with a letter grade A–F. Track it over time and watch it climb as you fix things.

---

## Quick start

### 1. Get the file

```bash
curl -O https://raw.githubusercontent.com/YOURUSERNAME/snoop/main/snoop.py
```

Or clone the repo:

```bash
git clone https://github.com/YOURUSERNAME/snoop.git
cd snoop
```

### 2. (Optional, recommended) Install `psutil`

`psutil` enables richer process inspection and faster port lookup. Without it, Snoop falls back to `netstat` / `ss` / `lsof` — slower but functional.

```bash
pip install psutil
```

### 3. Run it

```bash
python snoop.py
```

That's it. Full scan of your home directory, all checks, report and advice printed to your terminal.

---

## Usage

```
usage: snoop [-h] [--scan {all,system,files,browser}] [--path PATH]
             [--output FILE] [--quiet] [--no-color] [--max-files N]
             [--version]

Snoop — audit your machine for privacy issues and get fix advice.

options:
  -h, --help            show this help message and exit
  --scan {all,system,files,browser}
                        Which scan to run (default: all)
  --path PATH           Directory to scan for files (default: home)
  --output FILE         Write full JSON report to FILE
  --quiet, -q           Suppress progress output (only advice + score)
  --no-color            Disable ANSI colors
  --max-files N         Cap on files scanned (default 50000)
  --version             show program's version number and exit
```

### Common recipes

**Full scan, save a JSON report**
```bash
python snoop.py --output snoop-report.json
```

**System only — fast, no file walking**
```bash
python snoop.py --scan system
```

**Scan a specific folder, cap at 2000 files**
```bash
python snoop.py --scan files --path ~/Documents --max-files 2000
```

**Quiet mode — only score and advice**
```bash
python snoop.py --quiet
```

**No colors — for logs or CI**
```bash
python snoop.py --no-color --quiet
```

**Weekly cron job that saves a dated report**
```cron
0 9 * * 1 /usr/bin/python3 /opt/snoop/snoop.py \
  --quiet --output /var/log/snoop-$(date +\%F).json
```

---

## Supported platforms

| Platform | Support |
|---|---|
| **macOS** 11 (Big Sur) and newer | Full — FileVault, Gatekeeper, SIP, `system_profiler`, guest account |
| **Linux** (Debian, Ubuntu, Fedora, Arch) | Full — LUKS, ufw/firewalld/iptables, GDM/LightDM auto-login |
| **Windows** 10 / 11 | Full — BitLocker, Defender Firewall, `netstat`, registry |

Snoop detects the platform and runs the appropriate checks. Anything that doesn't apply is silently skipped.

---

## Requirements

- **Python 3.8+**
- **`psutil`** (optional, recommended)
- No other dependencies

---

## Privacy guarantees

Snoop was built to audit privacy, so it holds itself to the same standard:

- ✅ **No network calls.** Zero. It never connects to anything.
- ✅ **No telemetry.** Nothing is collected or sent anywhere.
- ✅ **No file uploads.** Your data stays on your machine.
- ✅ **No writes outside the `--output` file you specify.**
- ✅ **Read-only on scanned files.** It never modifies, deletes, or moves anything.

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

---

## JSON output

`--output snoop-report.json` writes a machine-readable report. Useful for dashboards, CI gates, or tracking over time.

```json
{
  "tool": "snoop",
  "version": "1.0.0",
  "timestamp": "2026-09-10T14:32:11",
  "platform": "macOS-14.5-arm64",
  "score": 55,
  "grade": "F",
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

---

## Safety notes

- **Read the source before running it.** It's one file — you can audit it in 15 minutes.
- **Run as your normal user**, not `root` / `sudo`. Elevated privileges let it see more, but aren't required, and running any scanner as root is bad hygiene.
- **The credit-card regex is intentionally broad.** It flags any 13–16 digit number. Treat `credit_card` findings as "review these files," not "you have a card number leak."
- **`~` contains a lot of files.** The default cap is 50,000 files. Use `--max-files` or `--path` to narrow it.
- **Skipped directories.** Snoop skips `.git`, `node_modules`, `__pycache__`, `venv`, `.venv`, `Library`, `AppData`, `$RECYCLE.BIN`, and other noise. Edit `SKIP_DIRS` if you want them scanned.
- **Only run this on machines you own** or have explicit permission to audit.

---

## Limitations

Being honest about what Snoop doesn't do:

- **It's not a vulnerability scanner.** No CVEs, no exploitability, no missing-patch detection.
- **It doesn't parse application databases.** Browser SQLite files (history, cookies) aren't read — that's a different tool's job.
- **It doesn't monitor.** A point-in-time snapshot, not a daemon. Run it periodically for ongoing visibility.
- **It's not a malware scanner.** It flags suspicious configuration, not malicious binaries.
- **Secrets detection is regex-based.** Catches common formats (AWS, GitHub, Google, Slack, Stripe, PEM) but not every provider. False positives and false negatives both happen.
- **PII detection is heuristic.** Same caveat: regex, not ML.

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

---

## FAQ

**Is this just `lynis` / `spectre` / `Wazuh` with a different name?**
No. Those are full audit frameworks. Snoop is a **single-file, zero-dependency, opinionated** scanner designed to be read in one sitting and run in one command. Different scope.

**Why "Snoop"?**
Because the tool snoops on your machine so nobody else has to. The irony is the brand.

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
2. **No new required dependencies.** `psutil` is optional; anything else that's non-standard needs to be too.
3. **Add a check for a real risk**, not a theoretical one.
4. **Test on at least two platforms** if the check is platform-specific.
5. **Update this README** if you add flags or change output.

### Adding a new check

Each check is a function that returns a dict. To add one:

1. Write the function — e.g. `check_something()` — following the existing patterns.
2. Call it from `scan_system()`, `scan_directory()`, or `scan_browser()`.
3. Add the finding to the appropriate `findings[...]` key.
4. Wire it into `build_advice()` so it produces a recommendation.
5. Update the JSON schema section of this README.

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
- [ ] HTML report output for easier sharing
- [ ] Homebrew / apt / scoop packages

PRs welcome on any of these.

---

## License

MIT License. See [LICENSE](LICENSE) for the full text.

Copyright (c) 2026 Your Name

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
