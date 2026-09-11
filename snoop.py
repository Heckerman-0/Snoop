#!/usr/bin/env python3
"""
Snoop — the only snoop you'll ever invite in.

Audits your computer for privacy issues, analyses files for sensitive
data, and tells you exactly how to fix what it finds.

Usage:
    python snoop.py                      # full scan of home dir
    python snoop.py --scan system        # only system checks
    python snoop.py --scan files --path ~/Documents
    python snoop.py --output snoop.json
    python snoop.py --quiet

License: MIT
"""

import argparse
import json
import os
import platform
import re
import stat
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ───────────────────────────── Optional psutil ─────────────────────────────
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

VERSION = "1.0.0"
TAGLINE = "The only snoop you'll ever invite in."


# ───────────────────────────── Color helpers ─────────────────────────────
class C:
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"


USE_COLOR = True


def c(text, color):
    if not USE_COLOR:
        return text
    return f"{color}{text}{C.RESET}"


def log(msg, color=C.WHITE, quiet=False):
    if not quiet:
        print(c(msg, color))


def header(title, quiet=False):
    if quiet:
        return
    bar = "═" * 60
    print()
    print(c(bar, C.MAGENTA))
    print(c(f"  {title}", C.BOLD + C.MAGENTA))
    print(c(bar, C.MAGENTA))


# ───────────────────────────── Shell helpers ─────────────────────────────
def run(cmd, shell=False, timeout=15):
    """Run a command safely. Always returns (stdout, stderr, code)."""
    try:
        result = subprocess.run(
            cmd, shell=shell, capture_output=True,
            text=True, timeout=timeout, errors="ignore",
        )
        return result.stdout.strip(), result.stderr.strip(), result.returncode
    except FileNotFoundError:
        return "", "command not found", 127
    except subprocess.TimeoutExpired:
        return "", "timeout", 124
    except Exception as e:
        return "", str(e), -1


def has(cmd):
    from shutil import which
    return which(cmd) is not None


def expand(path):
    return os.path.expanduser(os.path.expandvars(path))


def read_text(path, max_bytes=2_000_000):
    try:
        with open(expand(path), "r", encoding="utf-8", errors="ignore") as f:
            return f.read(max_bytes)
    except (OSError, IOError):
        return None


def human_size(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


# ══════════════════════════════════════════════════════════════════════════
# SYSTEM SCAN
# ══════════════════════════════════════════════════════════════════════════

def check_os_info():
    return {
        "os": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "hostname": platform.node(),
        "python": platform.python_version(),
    }


def check_firewall():
    result = {"enabled": None, "details": ""}

    if IS_WIN:
        out, _, code = run(["netsh", "advfirewall", "show", "allprofiles", "state"])
        if code == 0:
            states = re.findall(r"State\s+(\w+)", out)
            enabled = all(s.upper() == "ON" for s in states) and len(states) > 0
            result["enabled"] = enabled
            result["details"] = f"Profiles: {', '.join(states) or 'unknown'}"

    elif IS_MAC:
        out, _, code = run([
            "defaults", "read",
            "/Library/Preferences/com.apple.alf", "globalstate",
        ])
        if code == 0:
            enabled = out.strip() in ("1", "2")
            result["enabled"] = enabled
            result["details"] = f"globalstate={out.strip()}"

    elif IS_LINUX:
        if has("ufw"):
            out, _, _ = run(["ufw", "status"])
            if "Status: active" in out:
                result["enabled"] = True
                result["details"] = "ufw active"
                return result
        if has("firewall-cmd"):
            out, _, code = run(["firewall-cmd", "--state"])
            if code == 0 and "running" in out:
                result["enabled"] = True
                result["details"] = "firewalld running"
                return result
        if has("iptables"):
            out, _, code = run(["iptables", "-L", "-n"])
            if code == 0:
                has_rules = "Chain" in out and "policy ACCEPT" not in out.split("Chain")[1][:200]
                result["enabled"] = has_rules
                result["details"] = "iptables rules present" if has_rules else "iptables permissive"

    return result


def check_disk_encryption():
    result = {"encrypted": None, "details": ""}

    if IS_MAC:
        out, _, code = run(["fdesetup", "status"])
        if code == 0:
            result["encrypted"] = "FileVault is On" in out
            result["details"] = out

    elif IS_WIN:
        out, _, code = run(["manage-bde", "-status"], timeout=25)
        if code == 0:
            result["encrypted"] = "Fully Encrypted" in out or "Fully Decrypted" not in out
            result["details"] = "BitLocker status checked"

    elif IS_LINUX:
        out, _, code = run(["lsblk", "-o", "NAME,TYPE,FSTYPE", "-n"])
        if code == 0:
            encrypted = bool(re.search(r"\bcrypt\b|\bcrypto_LUKS\b", out))
            result["encrypted"] = encrypted
            result["details"] = "LUKS/crypt device detected" if encrypted else "no encrypted volume found"

    return result


def check_open_ports():
    ports = []
    if HAS_PSUTIL:
        try:
            for conn in psutil.net_connections(kind="inet"):
                if conn.status == "LISTEN":
                    ports.append({
                        "port": conn.laddr.port,
                        "address": conn.laddr.ip,
                        "pid": conn.pid,
                    })
        except (psutil.AccessDenied, PermissionError):
            pass
    else:
        if IS_WIN:
            out, _, _ = run(["netstat", "-ano"])
            for line in out.splitlines():
                m = re.match(r"\s*TCP\s+(\S+):(\d+)\s+\S+\s+LISTENING\s+(\d+)", line)
                if m:
                    ports.append({"address": m.group(1), "port": int(m.group(2)), "pid": int(m.group(3))})
        else:
            if has("ss"):
                out, _, _ = run(["ss", "-tlnp"])
                for line in out.splitlines()[1:]:
                    parts = line.split()
                    if len(parts) >= 4:
                        m = re.match(r"^(.*):(\d+)$", parts[3])
                        if m:
                            ports.append({"address": m.group(1), "port": int(m.group(2)), "pid": None})
            elif has("lsof"):
                out, _, _ = run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"])
                for line in out.splitlines()[1:]:
                    m = re.search(r"TCP\s+(\S+):(\d+)\s+\(LISTEN\)", line)
                    if m:
                        ports.append({"address": m.group(1), "port": int(m.group(2)), "pid": None})

    seen = set()
    unique = []
    for p in ports:
        key = (p["port"], p["address"])
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


RISKY_PORTS = {
    21: "FTP (plaintext)", 23: "Telnet (plaintext)", 135: "Windows RPC",
    139: "NetBIOS", 445: "SMB", 1433: "MSSQL", 3306: "MySQL",
    3389: "RDP", 5432: "PostgreSQL", 5900: "VNC", 6379: "Redis",
    9200: "Elasticsearch", 11211: "Memcached", 27017: "MongoDB",
}


def check_risky_ports(ports):
    return [p for p in ports if p["port"] in RISKY_PORTS]


def check_running_processes():
    if not HAS_PSUTIL:
        return {"count": None, "top": []}
    try:
        procs = []
        for proc in psutil.process_iter(["pid", "name", "username", "memory_info"]):
            try:
                info = proc.info
                mem = info["memory_info"].rss if info["memory_info"] else 0
                procs.append({
                    "pid": info["pid"],
                    "name": info["name"],
                    "user": info["username"],
                    "memory": mem,
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        procs.sort(key=lambda p: p["memory"], reverse=True)
        return {"count": len(procs), "top": procs[:10]}
    except Exception:
        return {"count": None, "top": []}


def check_auto_login():
    result = {"enabled": False, "details": ""}

    if IS_MAC:
        out, _, code = run([
            "defaults", "read",
            "/Library/Preferences/com.apple.loginwindow", "autoLoginUser",
        ])
        if code == 0 and out.strip():
            result["enabled"] = True
            result["details"] = f"autoLoginUser = {out.strip()}"

    elif IS_LINUX:
        for path in ("/etc/gdm3/custom.conf", "/etc/gdm/custom.conf",
                     "/etc/lightdm/lightdm.conf"):
            text = read_text(path)
            if text and re.search(r"AutomaticLoginEnable\s*=\s*true", text, re.I):
                result["enabled"] = True
                result["details"] = f"{path} has AutomaticLoginEnable=true"
                break

    elif IS_WIN:
        out, _, code = run([
            "reg", "query",
            r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon",
            "/v", "AutoAdminLogon",
        ])
        if code == 0 and "0x1" in out:
            result["enabled"] = True
            result["details"] = "AutoAdminLogon=1"

    return result


def check_guest_account():
    result = {"enabled": None, "details": ""}
    if IS_MAC:
        out, _, code = run(["sysadminctl", "-guestAccount", "status"], timeout=15)
        if code == 0:
            result["enabled"] = "disabled" not in out.lower()
            result["details"] = out
    return result


def scan_system(quiet=False):
    findings = {"checks": {}}
    header("SYSTEM SCAN", quiet)

    info = check_os_info()
    findings["os_info"] = info
    log(f"  [i] OS: {info['os']} {info['release']} ({info['machine']})", C.BLUE, quiet)
    log(f"  [i] Hostname: {info['hostname']}", C.BLUE, quiet)

    fw = check_firewall()
    findings["checks"]["firewall"] = fw
    if fw["enabled"]:
        log(f"  [+] Firewall is enabled ({fw['details']})", C.GREEN, quiet)
    elif fw["enabled"] is False:
        log(f"  [!] Firewall is DISABLED ({fw['details']})", C.RED, quiet)
    else:
        log(f"  [?] Firewall status unknown ({fw['details']})", C.YELLOW, quiet)

    enc = check_disk_encryption()
    findings["checks"]["disk_encryption"] = enc
    if enc["encrypted"]:
        log(f"  [+] Disk encryption is ON ({enc['details']})", C.GREEN, quiet)
    elif enc["encrypted"] is False:
        log(f"  [!] Disk encryption is OFF ({enc['details']})", C.RED, quiet)

    ports = check_open_ports()
    findings["open_ports"] = ports
    if ports:
        log(f"  [!] {len(ports)} listening port(s) found:", C.YELLOW, quiet)
        for p in ports[:12]:
            label = RISKY_PORTS.get(p["port"], "")
            marker = c(" [HIGH RISK]", C.RED) if label else ""
            log(f"      {p['address']}:{p['port']}{marker} {label}", C.WHITE, quiet)
    else:
        log("  [+] No listening ports found.", C.GREEN, quiet)

    procs = check_running_processes()
    findings["processes"] = procs
    if procs["count"]:
        log(f"  [i] {procs['count']} running processes.", C.BLUE, quiet)

    auto = check_auto_login()
    findings["checks"]["auto_login"] = auto
    if auto["enabled"]:
        log(f"  [!] Auto-login ENABLED ({auto['details']})", C.RED, quiet)

    guest = check_guest_account()
    if guest["enabled"] is not None:
        findings["checks"]["guest_account"] = guest
        if guest["enabled"]:
            log("  [!] Guest account is enabled.", C.YELLOW, quiet)

    return findings


# ══════════════════════════════════════════════════════════════════════════
# FILE SCAN
# ══════════════════════════════════════════════════════════════════════════

SENSITIVE_PATTERNS = [
    (r"\.env(\.|$)", "environment file"),
    (r"id_rsa$|id_dsa$|id_ecdsa$|id_ed25519$", "SSH private key"),
    (r"\.pem$", "PEM certificate/key"),
    (r"\.key$|\.p12$|\.pfx$", "private key"),
    (r"\.gitconfig$", "git config"),
    (r"\.aws[/\\]credentials$", "AWS credentials"),
    (r"\.ssh[/\\]config$", "SSH config"),
    (r"\.bash_history$|\.zsh_history$|\.sh_history$", "shell history"),
    (r"\.mysql_history$|\.psql_history$|\.irb_history$", "DB history"),
    (r"\.netrc$", "netrc credentials"),
    (r"\.npmrc$|\.pypirc$|\.dockercfg$", "package manager credentials"),
    (r"\.kube[/\\]config$", "Kubernetes config"),
    (r"passwords?\.(txt|json|csv)$", "password file"),
    (r"secrets?\.(txt|json|yaml|yml)$", "secrets file"),
]

PII_PATTERNS = {
    "email": re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b"),
    "phone": re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "credit_card": re.compile(r"\b(?:\d[ -]*?){13,16}\b"),
    "ip_address": re.compile(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b"),
}

SECRET_PATTERNS = {
    "AWS key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "GitHub token": re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),
    "Google API key": re.compile(r"AIza[0-9A-Za-z\-_]{35}"),
    "Slack token": re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    "Stripe key": re.compile(r"sk_live_[0-9a-zA-Z]{24,}"),
    "private key block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", "venv", ".venv",
    "env", ".cache", "Library", ".Trash", "$RECYCLE.BIN",
    "System Volume Information", "AppData",
}

SKIP_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".ico", ".svg",
    ".mp3", ".mp4", ".avi", ".mov", ".mkv", ".wav", ".flac",
    ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar", ".xz",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".iso", ".img",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
}

MAX_FILE_SIZE = 2 * 1024 * 1024


def is_world_readable(filepath):
    if IS_WIN:
        return False
    try:
        st = os.stat(filepath)
        return bool(st.st_mode & stat.S_IROTH)
    except OSError:
        return False


def is_world_writable(filepath):
    if IS_WIN:
        return False
    try:
        st = os.stat(filepath)
        return bool(st.st_mode & stat.S_IWOTH)
    except OSError:
        return False


def scan_file_content(filepath):
    findings = {}
    try:
        size = os.path.getsize(filepath)
        if size == 0 or size > MAX_FILE_SIZE:
            return findings
        ext = os.path.splitext(filepath)[1].lower()
        if ext in SKIP_EXTENSIONS:
            return findings

        text = read_text(filepath, max_bytes=MAX_FILE_SIZE)
        if not text:
            return findings

        for label, pattern in SECRET_PATTERNS.items():
            matches = pattern.findall(text)
            if matches:
                findings.setdefault("secrets", {})[label] = len(matches)

        for pii, pattern in PII_PATTERNS.items():
            matches = pattern.findall(text)
            if matches:
                if pii == "credit_card":
                    matches = [m for m in matches if len(re.sub(r"\D", "", m)) >= 13]
                if pii == "ip_address":
                    matches = [m for m in matches if not m.startswith(("0.", "127.", "255."))]
                if matches:
                    findings.setdefault("pii", {})[pii] = len(matches)
    except (OSError, UnicodeDecodeError):
        pass
    return findings


def scan_directory(root_path, quiet=False, max_files=50000):
    root_path = expand(root_path)
    findings = {
        "path": root_path,
        "sensitive_files": [],
        "world_readable": [],
        "world_writable": [],
        "with_pii": [],
        "with_secrets": [],
        "files_scanned": 0,
    }

    if not os.path.isdir(root_path):
        log(f"  [!] Path not found: {root_path}", C.RED, quiet)
        return findings

    log(f"  [i] Snooping around {root_path} ...", C.BLUE, quiet)
    start = time.time()

    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fname in filenames:
            if findings["files_scanned"] >= max_files:
                break
            filepath = os.path.join(dirpath, fname)
            findings["files_scanned"] += 1

            for pattern, label in SENSITIVE_PATTERNS:
                if re.search(pattern, filepath, re.IGNORECASE):
                    findings["sensitive_files"].append({"path": filepath, "type": label})
                    break

            if is_world_readable(filepath):
                findings["world_readable"].append(filepath)
            if is_world_writable(filepath):
                findings["world_writable"].append(filepath)

            content_hits = scan_file_content(filepath)
            if content_hits.get("secrets"):
                findings["with_secrets"].append({"path": filepath, "types": content_hits["secrets"]})
            if content_hits.get("pii"):
                findings["with_pii"].append({"path": filepath, "types": content_hits["pii"]})

    elapsed = time.time() - start
    log(f"  [i] Poked at {findings['files_scanned']} files in {elapsed:.1f}s", C.BLUE, quiet)

    if findings["sensitive_files"]:
        log(f"  [!] {len(findings['sensitive_files'])} sensitive file(s)", C.YELLOW, quiet)
    if findings["world_readable"]:
        log(f"  [!] {len(findings['world_readable'])} world-readable file(s)", C.YELLOW, quiet)
    if findings["with_secrets"]:
        log(f"  [!] {len(findings['with_secrets'])} file(s) contain secrets", C.RED, quiet)
    if findings["with_pii"]:
        log(f"  [!] {len(findings['with_pii'])} file(s) contain PII", C.RED, quiet)

    return findings


# ══════════════════════════════════════════════════════════════════════════
# BROWSER SCAN
# ══════════════════════════════════════════════════════════════════════════

BROWSER_PATHS = {
    "Chrome": {
        "darwin": "~/Library/Application Support/Google/Chrome",
        "linux": "~/.config/google-chrome",
        "win32": "~/AppData/Local/Google/Chrome/User Data",
    },
    "Brave": {
        "darwin": "~/Library/Application Support/BraveSoftware/Brave-Browser",
        "linux": "~/.config/BraveSoftware/Brave-Browser",
        "win32": "~/AppData/Local/BraveSoftware/Brave-Browser/User Data",
    },
    "Edge": {
        "darwin": "~/Library/Application Support/Microsoft Edge",
        "linux": "~/.config/microsoft-edge",
        "win32": "~/AppData/Local/Microsoft/Edge/User Data",
    },
    "Chromium": {
        "darwin": "~/Library/Application Support/Chromium",
        "linux": "~/.config/chromium",
        "win32": "~/AppData/Local/Chromium/User Data",
    },
    "Firefox": {
        "darwin": "~/Library/Application Support/Firefox/Profiles",
        "linux": "~/.mozilla/firefox",
        "win32": "~/AppData/Roaming/Mozilla/Firefox/Profiles",
    },
}


def scan_browser(quiet=False):
    findings = {"browsers": [], "issues": []}

    platform_key = sys.platform
    for name, paths in BROWSER_PATHS.items():
        root = paths.get(platform_key)
        if not root:
            continue
        expanded = expand(root)
        if os.path.isdir(expanded):
            findings["browsers"].append({"name": name, "path": expanded})

    if not findings["browsers"]:
        log("  [i] No browser profiles detected.", C.BLUE, quiet)
        return findings

    for browser in findings["browsers"]:
        name = browser["name"]
        log(f"  [+] {name} found at {browser['path']}", C.GREEN, quiet)

        if name == "Firefox":
            issues = _check_firefox(browser["path"])
        else:
            issues = _check_chromium(browser["path"])

        for issue in issues:
            findings["issues"].append({"browser": name, **issue})

    for issue in findings["issues"]:
        log(f"  [!] {issue['browser']}: {issue['title']}", C.YELLOW, quiet)

    return findings


def _check_chromium(root):
    issues = []
    for prefs_file in Path(root).glob("*/Preferences"):
        try:
            with open(prefs_file, "r", encoding="utf-8", errors="ignore") as f:
                prefs = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue

        profile = prefs_file.parent.name

        metrics = prefs.get("metrics", {})
        if isinstance(metrics, dict) and metrics.get("reporting_enabled"):
            issues.append({"profile": profile, "title": "usage statistics enabled"})

        search = prefs.get("search", {})
        if isinstance(search, dict) and search.get("suggest_enabled"):
            issues.append({"profile": profile, "title": "search suggestions on"})

        profile_prefs = prefs.get("profile", {})
        content = profile_prefs.get("default_content_setting_values", {}) if isinstance(profile_prefs, dict) else {}
        if isinstance(content, dict):
            if content.get("geolocation") == 1:
                issues.append({"profile": profile, "title": "geolocation allowed by default"})
            if content.get("media_stream_camera") == 1:
                issues.append({"profile": profile, "title": "camera allowed by default"})
            if content.get("media_stream_mic") == 1:
                issues.append({"profile": profile, "title": "microphone allowed by default"})

    return issues


def _check_firefox(root):
    issues = []
    for prefs_file in Path(root).glob("*/prefs.js"):
        text = read_text(str(prefs_file), max_bytes=1_000_000)
        if not text:
            continue
        profile = prefs_file.parent.name

        def pref(name):
            m = re.search(rf'user_pref\("{re.escape(name)}",\s*([^)]+)\)', text)
            return m.group(1).strip().strip('"') if m else None

        if pref("toolkit.telemetry.enabled") == "true":
            issues.append({"profile": profile, "title": "telemetry enabled"})
        if pref("datareporting.healthreport.uploadEnabled") == "true":
            issues.append({"profile": profile, "title": "data upload enabled"})
        if pref("network.trr.mode") in (None, "0", "5"):
            issues.append({"profile": profile, "title": "DoH off"})

    return issues


# ══════════════════════════════════════════════════════════════════════════
# ADVICE ENGINE
# ══════════════════════════════════════════════════════════════════════════

def build_advice(system, files, browser):
    advice = []
    severity = []

    if system:
        checks = system.get("checks", {})

        fw = checks.get("firewall", {})
        if fw.get("enabled") is False:
            severity.append(("HIGH", "Firewall is disabled. Enable it now."))
            if IS_MAC:
                advice.append("macOS: System Settings → Network → Firewall → Turn On, enable stealth mode.")
            elif IS_WIN:
                advice.append("Windows: Settings → Privacy & Security → Windows Security → Firewall & network protection.")
            else:
                advice.append("Linux: sudo ufw default deny incoming && sudo ufw enable")

        enc = checks.get("disk_encryption", {})
        if enc.get("encrypted") is False:
            severity.append(("HIGH", "Disk is not encrypted. Anyone with physical access can read your data."))
            if IS_MAC:
                advice.append("macOS: System Settings → Privacy & Security → FileVault → Turn On.")
            elif IS_WIN:
                advice.append("Windows: Settings → Privacy & Security → Device encryption, or run 'manage-bde -on C:'.")
            else:
                advice.append("Linux: back up and reinstall with LUKS, or use systemd-cryptenroll on a spare partition.")

        auto = checks.get("auto_login", {})
        if auto.get("enabled"):
            severity.append(("HIGH", "Auto-login is enabled — anyone who boots the machine gets your session."))
            advice.append("Disable auto-login in your OS account settings.")

        guest = checks.get("guest_account", {})
        if guest.get("enabled"):
            severity.append(("MEDIUM", "Guest account is enabled."))
            advice.append("Turn off the guest account in Users & Groups settings.")

        risky = check_risky_ports(system.get("open_ports", []))
        if risky:
            severity.append(("HIGH", f"{len(risky)} risky port(s) listening — {', '.join(str(p['port']) for p in risky)}"))
            advice.append("Bind services like Redis/MongoDB/MySQL to 127.0.0.1, or firewall the ports. Never expose them publicly.")

        ports = system.get("open_ports", [])
        if len(ports) > 5:
            advice.append(f"{len(ports)} listening ports detected. Review with 'netstat -tlnp' or 'ss -tlnp' and close what you don't need.")

    if files:
        sens = files.get("sensitive_files", [])
        if sens:
            severity.append(("MEDIUM", f"{len(sens)} sensitive file(s) found (SSH keys, .env, credential stores)."))
            advice.append("Restrict permissions: chmod 600 on private keys and credential files, chmod 700 on ~/.ssh.")

        secrets = files.get("with_secrets", [])
        if secrets:
            severity.append(("HIGH", f"{len(secrets)} file(s) contain live-looking secrets (API keys, tokens)."))
            advice.append("Rotate any exposed credentials immediately, move them into a password manager or OS keychain, and add the files to .gitignore.")

        pii = files.get("with_pii", [])
        if pii:
            severity.append(("MEDIUM", f"{len(pii)} file(s) contain PII (emails, phones, SSNs, cards)."))
            advice.append("Encrypt or remove PII files. Use full-disk encryption and avoid storing PII in plaintext.")

        wr = files.get("world_readable", [])
        if wr:
            severity.append(("LOW", f"{len(wr)} world-readable file(s)."))
            advice.append("Run 'chmod o-rwx' on sensitive directories (e.g., your home dir: chmod 750 ~).")

        ww = files.get("world_writable", [])
        if ww:
            severity.append(("MEDIUM", f"{len(ww)} world-writable file(s) — anyone can modify them."))
            advice.append("Remove world-write: chmod o-w <file>. Investigate anything unexpected.")

    if browser:
        issues = browser.get("issues", [])
        if issues:
            severity.append(("LOW", f"{len(issues)} browser privacy setting(s) need review."))
            advice.append("Open each browser's Privacy settings. Disable telemetry, search suggestions, and default camera/mic/geolocation permissions.")

    advice.extend([
        "Use a password manager (Bitwarden, 1Password, KeePassXC) and enable 2FA everywhere.",
        "Keep the OS, browsers, and all software updated.",
        "Use a reputable VPN on public Wi-Fi.",
        "Prefer HTTPS-only browsing and privacy-respecting DNS (1.1.1.1, 9.9.9.9).",
        "Review app permissions regularly — revoke anything you don't use.",
    ])

    return {"severity": severity, "advice": advice}


# ══════════════════════════════════════════════════════════════════════════
# REPORTING
# ══════════════════════════════════════════════════════════════════════════

def print_advice(advice_data, quiet=False):
    header("ADVICE", quiet)

    sev = advice_data.get("severity", [])
    if sev:
        log("  Priority findings:", C.BOLD + C.RED, quiet)
        for level, text in sev:
            color = {"HIGH": C.RED, "MEDIUM": C.YELLOW, "LOW": C.BLUE}.get(level, C.WHITE)
            log(f"    [{level}] {text}", color, quiet)
        print()

    log("  Recommended actions:", C.BOLD + C.CYAN, quiet)
    for i, tip in enumerate(advice_data.get("advice", []), 1):
        log(f"    {i:>2}. {tip}", C.CYAN, quiet)


def save_report(report, path):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)
        log(f"\n[+] Report saved to {path}", C.GREEN)
    except OSError as e:
        log(f"\n[!] Could not write report: {e}", C.RED)


def compute_score(system, files):
    score = 100

    if system:
        checks = system.get("checks", {})
        if checks.get("firewall", {}).get("enabled") is False:
            score -= 15
        if checks.get("disk_encryption", {}).get("encrypted") is False:
            score -= 20
        if checks.get("auto_login", {}).get("enabled"):
            score -= 10
        risky = check_risky_ports(system.get("open_ports", []))
        score -= min(20, len(risky) * 5)

    if files:
        score -= min(15, len(files.get("with_secrets", [])) * 3)
        score -= min(10, len(files.get("with_pii", [])) * 2)
        score -= min(10, len(files.get("sensitive_files", [])))
        score -= min(5, len(files.get("world_writable", [])) * 2)

    return max(0, score)


def grade(score):
    if score >= 90: return "A"
    if score >= 80: return "B"
    if score >= 70: return "C"
    if score >= 60: return "D"
    return "F"


# ══════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════

def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="snoop",
        description="Snoop — audit your machine for privacy issues and get fix advice.",
        epilog=TAGLINE,
    )
    p.add_argument("--scan", choices=["all", "system", "files", "browser"],
                   default="all", help="Which scan to run (default: all)")
    p.add_argument("--path", default=os.path.expanduser("~"),
                   help="Directory to scan for files (default: home)")
    p.add_argument("--output", metavar="FILE",
                   help="Write full JSON report to FILE")
    p.add_argument("--quiet", "-q", action="store_true",
                   help="Suppress progress output (only advice + score)")
    p.add_argument("--no-color", action="store_true",
                   help="Disable ANSI colors")
    p.add_argument("--max-files", type=int, default=50000,
                   help="Cap on files scanned (default 50000)")
    p.add_argument("--version", action="version",
                   version=f"Snoop v{VERSION}")
    return p.parse_args(argv)


def print_banner():
    print()
    print(c("╔" + "═" * 58 + "╗", C.MAGENTA))
    print(c(f"║  Snoop v{VERSION}".ljust(59) + "║", C.MAGENTA + C.BOLD))
    print(c(f"║  {TAGLINE}".ljust(59) + "║", C.MAGENTA))
    print(c("║  Local-only · Nothing leaves this machine.".ljust(59) + "║", C.MAGENTA))
    print(c("╚" + "═" * 58 + "╝", C.MAGENTA))


def main(argv=None):
    global USE_COLOR

    args = parse_args(argv)
    if args.no_color or not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
        USE_COLOR = False

    print_banner()

    if not HAS_PSUTIL:
        log("\n[i] psutil not installed — some checks will use fallbacks.", C.YELLOW)
        log("    Install for best results: pip install psutil", C.DIM)

    report = {
        "tool": "snoop",
        "version": VERSION,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "platform": platform.platform(),
    }

    system_findings = {}
    file_findings = {}
    browser_findings = {}

    if args.scan in ("all", "system"):
        system_findings = scan_system(quiet=args.quiet)
        report["system"] = system_findings

    if args.scan in ("all", "files"):
        header("FILE SCAN", args.quiet)
        file_findings = scan_directory(args.path, quiet=args.quiet, max_files=args.max_files)
        report["files"] = file_findings

    if args.scan in ("all", "browser"):
        header("BROWSER SCAN", args.quiet)
        browser_findings = scan_browser(quiet=args.quiet)
        report["browser"] = browser_findings

    advice_data = build_advice(system_findings, file_findings, browser_findings)
    report["advice"] = advice_data

    score = compute_score(system_findings, file_findings)
    report["score"] = score
    report["grade"] = grade(score)

    header("SCORE", args.quiet)
    score_color = C.GREEN if score >= 80 else C.YELLOW if score >= 60 else C.RED
    log(f"  Privacy score: {score}/100  (grade {grade(score)})", score_color, args.quiet)

    print_advice(advice_data, quiet=False)

    if args.output:
        save_report(report, args.output)

    print()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[!] Interrupted.", file=sys.stderr)
        sys.exit(130)
