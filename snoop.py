#!/usr/bin/env python3
"""
Snoop — the only snoop you'll ever invite in.

Single-file privacy auditor with CLI, web dashboard, and live progress bar.
Zero dependencies beyond the Python standard library (psutil optional).

Usage:
    python snoop.py                          # one-shot scan, print report
    python snoop.py --scan system            # only system checks
    python snoop.py --output snoop.json      # save report
    python snoop.py --dashboard              # launch web dashboard
    python snoop.py --dashboard --port 9000  # custom port
    python snoop.py --schedule-every 6h      # headless recurring scans

License: MIT
"""

import argparse
import copy
import json
import os
import platform
import re
import stat
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# ───────────────────────────── Optional psutil ─────────────────────────────
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

VERSION = "2.1.1"
TAGLINE = "The only snoop you'll ever invite in."
SNOOP_HOME = Path.home() / ".snoop"
HISTORY_DIR = SNOOP_HOME / "history"
CONFIG_FILE = SNOOP_HOME / "config.json"


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


# ───────────────────────────── Progress bar ─────────────────────────────
class ProgressBar:
    """TTY-aware progress bar. Silent when output isn't a terminal.

    Supports:
      - determinate bar (when total is known)
      - spinner (when total is unknown)
      - log() that prints above the bar without clobbering it
      - automatic suppression on --quiet, --no-progress, piped output,
        NO_COLOR, or NO_PROGRESS
    """

    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, total=None, label="", enabled=True, quiet=False, width=26):
        self.total = total
        self.label = label
        self.width = width
        self.current = 0
        self.quiet = quiet
        self.enabled = (
            enabled
            and not quiet
            and self._isatty()
            and not os.environ.get("NO_PROGRESS")
            and not os.environ.get("NO_COLOR")
        )
        self._start = time.time()
        self._last_render = 0.0
        self._last_len = 0
        self._finished = False

    @staticmethod
    def _isatty():
        try:
            return sys.stdout.isatty()
        except (AttributeError, ValueError):
            return False

    # ── internals ──────────────────────────────────────────────────
    def _term_width(self):
        try:
            return os.get_terminal_size().columns
        except OSError:
            return 80

    def _clear_line(self):
        if not self.enabled or self._last_len == 0:
            return
        try:
            sys.stdout.write("\r" + " " * self._last_len + "\r")
            sys.stdout.flush()
        except (OSError, ValueError):
            pass
        self._last_len = 0

    def _render(self, force=False):
        if not self.enabled or self._finished:
            return
        now = time.time()
        if not force and now - self._last_render < 0.08:
            return
        self._last_render = now
        elapsed = now - self._start

        if self.total and self.total > 0:
            pct = min(1.0, self.current / self.total)
            filled = int(self.width * pct)
            bar = "█" * filled + "░" * (self.width - filled)
            counter = f"{self.current}/{self.total}"

            eta = ""
            if self.current > 5 and elapsed > 0.5:
                rate = self.current / elapsed
                if rate > 0:
                    remaining = (self.total - self.current) / rate
                    eta = f"  eta {int(remaining):>4}s"

            line = f"  {bar}  {pct * 100:4.0f}%  {counter:>10}  {elapsed:5.1f}s{eta}"
        else:
            spin = self.FRAMES[int(elapsed * 12) % len(self.FRAMES)]
            line = f"  {spin}  working  {elapsed:5.1f}s"

        if self.label:
            line += f"  {self.label}"

        cols = self._term_width()
        line = line[: max(0, cols - 1)]
        self._last_len = len(line)
        try:
            sys.stdout.write("\r" + line)
            sys.stdout.flush()
        except (OSError, ValueError):
            pass

    # ── public API ─────────────────────────────────────────────────
    def update(self, current=None, label=None, force=False):
        if current is not None:
            self.current = current
        if label is not None:
            self.label = label
        self._render(force=force)

    def advance(self, n=1, label=None):
        self.current += n
        if label is not None:
            self.label = label
        self._render()

    def log(self, msg, color=C.WHITE):
        """Print a line, then redraw the bar underneath it."""
        if self.quiet:
            return
        if not self.enabled:
            print(c(msg, color))
            return
        self._clear_line()
        print(c(msg, color))
        self._last_render = 0.0
        self._render(force=True)

    def finish(self, label=None):
        if self._finished:
            return
        self._finished = True
        if not self.enabled:
            return
        if self.total and self.total > 0:
            self.current = self.total
        if label is not None:
            self.label = label
        self._last_render = 0.0
        self._render(force=True)
        try:
            sys.stdout.write("\n")
            sys.stdout.flush()
        except (OSError, ValueError):
            pass


# ───────────────────────────── Shell helpers ─────────────────────────────
def run(cmd, shell=False, timeout=15):
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


def atomic_write_json(path, data):
    """Write JSON atomically: temp file, fsync, rename."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
        try:
            f.flush()
            os.fsync(f.fileno())
        except (OSError, ValueError):
            pass
    os.replace(tmp, path)


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
            states = re.findall(r"^\s*State\s+(\w+)", out, re.MULTILINE)
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
        else:
            # manage-bde is missing on Windows Home editions — try PowerShell
            ps_out, _, ps_code = run([
                "powershell", "-NoProfile", "-Command",
                "(Get-BitLockerVolume -MountPoint $env:SystemDrive).ProtectionStatus"
            ], timeout=20)
            if ps_code == 0:
                status = ps_out.strip().lower()
                # ProtectionStatus: 0 = Off, 1 = On, 2 = Unknown
                result["encrypted"] = status in ("1", "on")
                result["details"] = f"BitLocker ProtectionStatus={status or 'unknown'}"

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


def _safe(fn, default):
    """Call fn(), returning default if it raises. Used to isolate check failures."""
    try:
        return fn(), None
    except Exception as e:
        return default, str(e)


def scan_system(quiet=False, progress=True):
    findings = {"checks": {}}
    header("SYSTEM SCAN", quiet)

    info = check_os_info()
    findings["os_info"] = info
    log(f"  [i] OS: {info['os']} {info['release']} ({info['machine']})", C.BLUE, quiet)
    log(f"  [i] Hostname: {info['hostname']}", C.BLUE, quiet)

    phases = ["firewall", "disk encryption", "open ports", "processes", "auto-login"]
    if IS_MAC:
        phases.append("guest account")

    bar = ProgressBar(
        total=len(phases),
        label=phases[0],
        enabled=progress,
        quiet=quiet,
    )
    bar.update(force=True)

    # ── Firewall ──
    bar.update(label=phases[0])
    fw, err = _safe(check_firewall, {"enabled": None, "details": ""})
    findings["checks"]["firewall"] = fw
    bar.advance()
    if err:
        bar.log(f"  [!] firewall check failed: {err}", C.YELLOW)
    elif fw["enabled"]:
        bar.log(f"  [+] Firewall is enabled ({fw['details']})", C.GREEN)
    elif fw["enabled"] is False:
        bar.log(f"  [!] Firewall is DISABLED ({fw['details']})", C.RED)
    else:
        bar.log(f"  [?] Firewall status unknown ({fw['details']})", C.YELLOW)

    # ── Disk encryption ──
    bar.update(label=phases[1])
    enc, err = _safe(check_disk_encryption, {"encrypted": None, "details": ""})
    findings["checks"]["disk_encryption"] = enc
    bar.advance()
    if err:
        bar.log(f"  [!] disk encryption check failed: {err}", C.YELLOW)
    elif enc["encrypted"]:
        bar.log(f"  [+] Disk encryption is ON ({enc['details']})", C.GREEN)
    elif enc["encrypted"] is False:
        bar.log(f"  [!] Disk encryption is OFF ({enc['details']})", C.RED)

    # ── Open ports ──
    bar.update(label=phases[2])
    ports, err = _safe(check_open_ports, [])
    findings["open_ports"] = ports
    bar.advance()
    if err:
        bar.log(f"  [!] port scan failed: {err}", C.YELLOW)
    elif ports:
        bar.log(f"  [!] {len(ports)} listening port(s) found:", C.YELLOW)
        for p in ports[:12]:
            label = RISKY_PORTS.get(p["port"], "")
            marker = c(" [HIGH RISK]", C.RED) if label else ""
            bar.log(f"      {p['address']}:{p['port']}{marker} {label}", C.WHITE)
    else:
        bar.log("  [+] No listening ports found.", C.GREEN)

    # ── Processes ──
    bar.update(label=phases[3])
    procs, err = _safe(check_running_processes, {"count": None, "top": []})
    findings["processes"] = procs
    bar.advance()
    if err:
        bar.log(f"  [!] process check failed: {err}", C.YELLOW)
    elif procs["count"]:
        bar.log(f"  [i] {procs['count']} running processes.", C.BLUE)

    # ── Auto-login ──
    bar.update(label=phases[4])
    auto, err = _safe(check_auto_login, {"enabled": False, "details": ""})
    findings["checks"]["auto_login"] = auto
    bar.advance()
    if err:
        bar.log(f"  [!] auto-login check failed: {err}", C.YELLOW)
    elif auto["enabled"]:
        bar.log(f"  [!] Auto-login ENABLED ({auto['details']})", C.RED)

    # ── Guest account (macOS) ──
    if IS_MAC:
        bar.update(label=phases[5])
        guest, err = _safe(check_guest_account, {"enabled": None, "details": ""})
        if guest.get("enabled") is not None:
            findings["checks"]["guest_account"] = guest
        bar.advance()
        if err:
            bar.log(f"  [!] guest account check failed: {err}", C.YELLOW)
        elif guest.get("enabled"):
            bar.log("  [!] Guest account is enabled.", C.YELLOW)

    bar.finish(label=f"system scan · {len(phases)} checks")

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
    "System Volume Information", "AppData", ".snoop",
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


def collect_files(root_path, max_files=50000):
    """Walk the tree once and return a list of file paths."""
    root_path = expand(root_path)
    files = []
    if not os.path.isdir(root_path):
        return files
    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIRS and not d.startswith(".")
        ]
        for fname in filenames:
            files.append(os.path.join(dirpath, fname))
            if len(files) >= max_files:
                return files
    return files


def scan_directory(root_path, quiet=False, max_files=50000, progress=True):
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

    if not os.path.exists(root_path):
        log(f"  [!] Path not found: {root_path}", C.RED, quiet)
        return findings

    # If user pointed --path at a file, use its containing directory
    if os.path.isfile(root_path):
        root_path = os.path.dirname(os.path.abspath(root_path))
        findings["path"] = root_path
        log(f"  [i] Path was a file, scanning its directory: {root_path}", C.BLUE, quiet)

    if not os.path.isdir(root_path):
        log(f"  [!] Not a directory: {root_path}", C.RED, quiet)
        return findings

    log(f"  [i] Snooping around {root_path} ...", C.BLUE, quiet)

    # Phase 1 — collect
    bar = ProgressBar(
        total=None,
        label=f"collecting files in {root_path}",
        enabled=progress,
        quiet=quiet,
    )
    bar.update(force=True)
    start = time.time()
    file_list = collect_files(root_path, max_files=max_files)
    bar.update(current=len(file_list), label="collected", force=True)
    bar.finish(label=f"{len(file_list)} files found")

    if not file_list:
        log("  [+] No files to scan.", C.GREEN, quiet)
        return findings

    # Phase 2 — analyse each file
    bar = ProgressBar(
        total=len(file_list),
        label="scanning",
        enabled=progress,
        quiet=quiet,
    )
    bar.update(force=True)

    for idx, filepath in enumerate(file_list, start=1):
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
            findings["with_secrets"].append(
                {"path": filepath, "types": content_hits["secrets"]}
            )
        if content_hits.get("pii"):
            findings["with_pii"].append(
                {"path": filepath, "types": content_hits["pii"]}
            )

        if idx % 10 == 0 or idx == len(file_list):
            display = os.path.basename(filepath)[:40] or filepath[:40]
            bar.update(current=idx, label=display)

    elapsed = time.time() - start
    bar.finish(label=f"done · {findings['files_scanned']} files in {elapsed:.1f}s")

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


def scan_browser(quiet=False, progress=True):
    findings = {"browsers": [], "issues": []}

    platform_key = sys.platform
    candidates = []
    for name, paths in BROWSER_PATHS.items():
        root = paths.get(platform_key)
        if not root:
            continue
        candidates.append((name, expand(root)))

    bar = ProgressBar(
        total=len(candidates),
        label="probing browsers",
        enabled=progress,
        quiet=quiet,
    )
    bar.update(force=True)

    for name, expanded in candidates:
        bar.update(label=f"checking {name}")
        if os.path.isdir(expanded):
            findings["browsers"].append({"name": name, "path": expanded})
            try:
                issues = _check_firefox(expanded) if name == "Firefox" else _check_chromium(expanded)
            except Exception:
                issues = []
            for issue in issues:
                findings["issues"].append({"browser": name, **issue})
        bar.advance()

    bar.finish(label=f"browser scan · {len(findings['browsers'])} found")

    if not findings["browsers"]:
        log("  [i] No browser profiles detected.", C.BLUE, quiet)
        return findings

    for browser in findings["browsers"]:
        log(f"  [+] {browser['name']} found at {browser['path']}", C.GREEN, quiet)
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
# ADVICE + SCORING
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


def extract_threats(system, files, browser):
    """Flatten findings into a list of threat objects for the dashboard."""
    threats = []

    if system:
        checks = system.get("checks", {})
        fw = checks.get("firewall", {})
        if fw.get("enabled") is False:
            threats.append({
                "category": "system", "severity": "high",
                "title": "Firewall disabled",
                "detail": fw.get("details", ""),
            })
        enc = checks.get("disk_encryption", {})
        if enc.get("encrypted") is False:
            threats.append({
                "category": "system", "severity": "critical",
                "title": "Disk encryption off",
                "detail": enc.get("details", ""),
            })
        auto = checks.get("auto_login", {})
        if auto.get("enabled"):
            threats.append({
                "category": "system", "severity": "high",
                "title": "Auto-login enabled",
                "detail": auto.get("details", ""),
            })
        guest = checks.get("guest_account", {})
        if guest.get("enabled"):
            threats.append({
                "category": "system", "severity": "medium",
                "title": "Guest account enabled",
                "detail": guest.get("details", ""),
            })
        for p in check_risky_ports(system.get("open_ports", [])):
            threats.append({
                "category": "network", "severity": "high",
                "title": f"Risky port {p['port']} exposed",
                "detail": f"{p['address']}:{p['port']} — {RISKY_PORTS.get(p['port'], '')}",
            })

    if files:
        for entry in files.get("with_secrets", []):
            types = ", ".join(entry["types"].keys())
            threats.append({
                "category": "files", "severity": "high",
                "title": f"Secrets in {Path(entry['path']).name}",
                "detail": f"{types} — {entry['path']}",
            })
        for entry in files.get("with_pii", []):
            types = ", ".join(entry["types"].keys())
            threats.append({
                "category": "files", "severity": "medium",
                "title": f"PII in {Path(entry['path']).name}",
                "detail": f"{types} — {entry['path']}",
            })
        for entry in files.get("world_writable", [])[:20]:
            threats.append({
                "category": "files", "severity": "medium",
                "title": "World-writable file",
                "detail": entry,
            })
        for entry in files.get("sensitive_files", [])[:20]:
            threats.append({
                "category": "files", "severity": "low",
                "title": f"Sensitive file: {entry['type']}",
                "detail": entry["path"],
            })

    if browser:
        for issue in browser.get("issues", []):
            threats.append({
                "category": "browser", "severity": "low",
                "title": f"{issue['browser']}: {issue['title']}",
                "detail": f"profile {issue.get('profile', 'default')}",
            })

    return threats


def severity_counts(threats):
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for t in threats:
        counts[t["severity"]] = counts.get(t["severity"], 0) + 1
    return counts


def run_full_scan(scan_type="all", path=None, quiet=False,
                  max_files=50000, progress=True):
    """Run scans and return a complete report dict."""
    if path is None:
        path = os.path.expanduser("~")

    report = {
        "tool": "snoop",
        "version": VERSION,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "platform": platform.platform(),
        "scan_type": scan_type,
    }

    system_findings = {}
    file_findings = {}
    browser_findings = {}

    if scan_type in ("all", "system"):
        system_findings = scan_system(quiet=quiet, progress=progress)
        report["system"] = system_findings
    if scan_type in ("all", "files"):
        if not quiet:
            header("FILE SCAN", quiet)
        file_findings = scan_directory(
            path, quiet=quiet, max_files=max_files, progress=progress,
        )
        report["files"] = file_findings
    if scan_type in ("all", "browser"):
        if not quiet:
            header("BROWSER SCAN", quiet)
        browser_findings = scan_browser(quiet=quiet, progress=progress)
        report["browser"] = browser_findings

    advice_data = build_advice(system_findings, file_findings, browser_findings)
    report["advice"] = advice_data

    score = compute_score(system_findings, file_findings)
    report["score"] = score
    report["grade"] = grade(score)

    threats = extract_threats(system_findings, file_findings, browser_findings)
    report["threats"] = threats
    report["severity_counts"] = severity_counts(threats)

    return report


# ══════════════════════════════════════════════════════════════════════════
# HISTORY / STORAGE
# ══════════════════════════════════════════════════════════════════════════

_STORAGE_LOCK = threading.Lock()


def ensure_dirs():
    SNOOP_HOME.mkdir(exist_ok=True)
    HISTORY_DIR.mkdir(exist_ok=True)


def _report_id(ts=None):
    ts = ts or datetime.now()
    return ts.strftime("%Y%m%d-%H%M%S")


def save_history(report):
    ensure_dirs()
    rid = _report_id()
    report["id"] = rid
    path = HISTORY_DIR / f"{rid}.json"
    with _STORAGE_LOCK:
        atomic_write_json(path, report)
    return rid


def list_history(limit=None):
    """Return summaries, newest first."""
    ensure_dirs()
    files = sorted(HISTORY_DIR.glob("*.json"), reverse=True)
    if limit:
        files = files[:limit]

    summaries = []
    for fp in files:
        try:
            with open(fp, "r", encoding="utf-8") as f:
                data = json.load(f)
            summaries.append({
                "id": data.get("id", fp.stem),
                "timestamp": data.get("timestamp", ""),
                "score": data.get("score", 0),
                "grade": data.get("grade", "?"),
                "severity_counts": data.get("severity_counts", {}),
                "scan_type": data.get("scan_type", "all"),
            })
        except (OSError, json.JSONDecodeError):
            continue
    return summaries


def load_report(report_id):
    path = HISTORY_DIR / f"{report_id}.json"
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def delete_report(report_id):
    path = HISTORY_DIR / f"{report_id}.json"
    if path.exists():
        path.unlink()
        return True
    return False


DEFAULT_CONFIG = {
    "schedule": {
        "enabled": False,
        "interval_hours": 24,
        "scan_type": "all",
        "path": "~",
        "max_files": 50000,
        "last_run": None,
        "next_run": None,
    }
}


def load_config():
    ensure_dirs()
    if not CONFIG_FILE.exists():
        default = copy.deepcopy(DEFAULT_CONFIG)
        save_config(default)
        return default
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        for k, v in DEFAULT_CONFIG.items():
            cfg.setdefault(k, copy.deepcopy(v))
        return cfg
    except (OSError, json.JSONDecodeError):
        return copy.deepcopy(DEFAULT_CONFIG)


def save_config(cfg):
    ensure_dirs()
    with _STORAGE_LOCK:
        atomic_write_json(CONFIG_FILE, cfg)


# ══════════════════════════════════════════════════════════════════════════
# SCHEDULER
# ══════════════════════════════════════════════════════════════════════════

class Scheduler:
    """Background scheduler that runs scans periodically."""

    def __init__(self, on_scan_complete=None):
        self._stop = threading.Event()
        self._thread = None
        self._scan_lock = threading.Lock()
        self._scanning = False
        self._on_complete = on_scan_complete
        self._last_error = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def is_scanning(self):
        return self._scanning

    def last_error(self):
        return self._last_error

    def run_now(self, scan_type="all", path=None, max_files=50000):
        """Trigger an immediate scan in a background thread."""
        if not self._scan_lock.acquire(blocking=False):
            return False
        try:
            self._scanning = True
            threading.Thread(
                target=self._do_scan,
                args=(scan_type, path, max_files),
                daemon=True,
            ).start()
            return True
        except Exception:
            self._scan_lock.release()
            self._scanning = False
            raise

    def _do_scan(self, scan_type, path, max_files):
        try:
            report = run_full_scan(
                scan_type=scan_type,
                path=path,
                quiet=True,
                max_files=max_files,
                progress=False,
            )
            rid = save_history(report)
            cfg = load_config()
            cfg["schedule"]["last_run"] = report["timestamp"]
            if cfg["schedule"].get("enabled"):
                interval = cfg["schedule"].get("interval_hours", 24)
                next_run = datetime.now() + timedelta(hours=interval)
                cfg["schedule"]["next_run"] = next_run.isoformat(timespec="seconds")
            save_config(cfg)
            self._last_error = None
            if self._on_complete:
                try:
                    self._on_complete(rid, report)
                except Exception:
                    pass
        except Exception as e:
            self._last_error = str(e)
        finally:
            self._scanning = False
            self._scan_lock.release()

    def _loop(self):
        time.sleep(2)
        while not self._stop.is_set():
            try:
                cfg = load_config()
                sched = cfg.get("schedule", {})
                if sched.get("enabled"):
                    next_run = sched.get("next_run")
                    due = False
                    if not next_run:
                        due = True
                    else:
                        try:
                            due = datetime.fromisoformat(next_run) <= datetime.now()
                        except ValueError:
                            due = True

                    if due and not self._scanning:
                        self.run_now(
                            scan_type=sched.get("scan_type", "all"),
                            path=expand(sched.get("path", "~")),
                            max_files=sched.get("max_files", 50000),
                        )
            except Exception:
                pass
            self._stop.wait(30)


# ══════════════════════════════════════════════════════════════════════════
# DASHBOARD (HTTP server + embedded HTML/JS)
# ══════════════════════════════════════════════════════════════════════════

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Snoop Dashboard</title>
<style>
  :root {
    --bg: #0d1117;
    --bg2: #161b22;
    --border: #30363d;
    --fg: #e6edf3;
    --muted: #8b949e;
    --accent: #ff79c6;
    --green: #4ade80;
    --yellow: #facc15;
    --orange: #fb923c;
    --red: #ef4444;
    --blue: #60a5fa;
    --purple: #a78bfa;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--fg);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    font-size: 14px;
    line-height: 1.5;
  }
  header {
    padding: 18px 28px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    gap: 16px;
    background: var(--bg2);
    position: sticky;
    top: 0;
    z-index: 10;
  }
  header h1 {
    margin: 0;
    font-size: 20px;
    font-weight: 700;
    letter-spacing: -0.2px;
  }
  header h1 .dot { color: var(--accent); }
  header .tagline {
    color: var(--muted);
    font-size: 12px;
    margin-left: 4px;
  }
  header .spacer { flex: 1; }
  header .status {
    display: flex;
    align-items: center;
    gap: 8px;
    color: var(--muted);
    font-size: 12px;
  }
  .pulse {
    display: inline-block;
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--green);
    box-shadow: 0 0 0 0 rgba(74, 222, 128, 0.6);
    animation: pulse 2s infinite;
  }
  .pulse.scanning {
    background: var(--yellow);
    box-shadow: 0 0 0 0 rgba(250, 204, 21, 0.6);
  }
  @keyframes pulse {
    0%   { box-shadow: 0 0 0 0 rgba(74, 222, 128, 0.5); }
    70%  { box-shadow: 0 0 0 10px rgba(74, 222, 128, 0); }
    100% { box-shadow: 0 0 0 0 rgba(74, 222, 128, 0); }
  }

  main {
    padding: 22px 28px 40px;
    max-width: 1280px;
    margin: 0 auto;
  }

  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 16px;
    margin-bottom: 16px;
  }

  .card {
    background: var(--bg2);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 18px 20px;
    margin-bottom: 16px;
  }
  .card h2 {
    margin: 0 0 14px;
    font-size: 13px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--muted);
  }
  .card .body { display: block; }

  .gauge-wrap { display: flex; align-items: center; gap: 22px; }
  .gauge-wrap svg { flex-shrink: 0; }
  .gauge-meta .big {
    font-size: 42px;
    font-weight: 800;
    line-height: 1;
    letter-spacing: -1px;
  }
  .gauge-meta .grade {
    font-size: 13px;
    color: var(--muted);
    margin-top: 6px;
  }

  .chart-svg { display: block; width: 100%; height: auto; overflow: visible; }

  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }
  th, td {
    text-align: left;
    padding: 9px 10px;
    border-bottom: 1px solid var(--border);
  }
  th {
    color: var(--muted);
    font-weight: 600;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }
  tbody tr:last-child td { border-bottom: none; }

  .chip {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 999px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .chip.critical { background: rgba(239, 68, 68, 0.15);  color: var(--red); }
  .chip.high     { background: rgba(251, 146, 60, 0.15); color: var(--orange); }
  .chip.medium   { background: rgba(250, 204, 21, 0.15); color: var(--yellow); }
  .chip.low      { background: rgba(96, 165, 250, 0.15); color: var(--blue); }
  .chip.pass     { background: rgba(74, 222, 128, 0.15); color: var(--green); }

  .mono {
    font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
    font-size: 12px;
  }
  .muted { color: var(--muted); }

  .threats-scroll { max-height: 420px; overflow-y: auto; margin: -4px -8px; padding: 4px 8px; }

  form.schedule {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 14px;
    align-items: end;
  }
  form.schedule label {
    display: flex;
    flex-direction: column;
    gap: 5px;
    font-size: 12px;
    color: var(--muted);
  }
  form.schedule input[type=text],
  form.schedule input[type=number],
  form.schedule select {
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--fg);
    padding: 7px 9px;
    border-radius: 6px;
    font-size: 13px;
    font-family: inherit;
  }
  form.schedule input[type=checkbox] {
    accent-color: var(--accent);
    width: 16px; height: 16px;
  }
  .check-label {
    display: flex !important;
    flex-direction: row !important;
    align-items: center;
    gap: 8px;
    color: var(--fg) !important;
    font-size: 13px !important;
  }

  button {
    background: var(--accent);
    color: #0d1117;
    border: none;
    padding: 8px 16px;
    border-radius: 6px;
    font-weight: 600;
    font-size: 13px;
    cursor: pointer;
    font-family: inherit;
    transition: filter 0.15s;
  }
  button:hover { filter: brightness(1.1); }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
  button.secondary {
    background: transparent;
    color: var(--fg);
    border: 1px solid var(--border);
  }
  button.secondary:hover { background: var(--bg); }

  .actions {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
    margin-top: 14px;
  }

  .banner {
    padding: 12px 16px;
    border-radius: 8px;
    background: rgba(255, 121, 198, 0.08);
    border: 1px solid rgba(255, 121, 198, 0.3);
    color: var(--fg);
    font-size: 13px;
    margin-bottom: 16px;
    display: none;
  }
  .banner.show { display: block; }
  .banner.error {
    background: rgba(239, 68, 68, 0.08);
    border-color: rgba(239, 68, 68, 0.4);
  }

  .empty {
    color: var(--muted);
    font-size: 13px;
    padding: 20px 0;
    text-align: center;
  }

  footer {
    text-align: center;
    color: var(--muted);
    font-size: 11px;
    padding: 10px 20px 30px;
  }

  @media (max-width: 640px) {
    header { padding: 14px 16px; }
    header .tagline { display: none; }
    main { padding: 16px; }
    .card { padding: 14px; }
  }
</style>
</head>
<body>

<header>
  <h1>🔍 Snoop<span class="dot">.</span></h1>
  <span class="tagline">__TAGLINE__</span>
  <div class="spacer"></div>
  <div class="status">
    <span class="pulse" id="pulse"></span>
    <span id="status-text">connecting…</span>
  </div>
</header>

<main>
  <div class="banner" id="banner"></div>

  <div class="grid">
    <div class="card" style="grid-column: span 1;">
      <h2>Privacy Score</h2>
      <div class="gauge-wrap">
        <svg id="gauge" width="180" height="130" viewBox="0 0 180 130"></svg>
        <div class="gauge-meta">
          <div class="big" id="score-big">—</div>
          <div class="grade" id="score-grade">no scans yet</div>
        </div>
      </div>
    </div>

    <div class="card">
      <h2>Severity Breakdown</h2>
      <div class="body" id="severity-chart"></div>
    </div>

    <div class="card">
      <h2>Categories</h2>
      <div class="body" id="category-chart"></div>
    </div>
  </div>

  <div class="card">
    <h2>Score History</h2>
    <svg id="history-chart" class="chart-svg" viewBox="0 0 800 220" preserveAspectRatio="none"></svg>
  </div>

  <div class="card">
    <h2>Recent Threats</h2>
    <div class="threats-scroll">
      <table>
        <thead>
          <tr><th style="width:90px">Severity</th><th style="width:100px">Category</th><th>Threat</th><th class="muted">Detail</th></tr>
        </thead>
        <tbody id="threats-body">
          <tr><td colspan="4" class="empty">No scans yet — run one to see threats.</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <h2>Schedule</h2>
    <form class="schedule" id="schedule-form">
      <label class="check-label">
        <input type="checkbox" id="sched-enabled">
        Enabled
      </label>
      <label>
        Every (hours)
        <input type="number" id="sched-interval" min="1" max="720" value="24">
      </label>
      <label>
        Scan
        <select id="sched-scan">
          <option value="all">All</option>
          <option value="system">System</option>
          <option value="files">Files</option>
          <option value="browser">Browsers</option>
        </select>
      </label>
      <label>
        Path
        <input type="text" id="sched-path" value="~">
      </label>
      <button type="submit">Save</button>
    </form>
    <div class="actions">
      <button id="scan-now">Scan Now</button>
      <button id="delete-all" class="secondary">Clear History</button>
    </div>
    <div class="muted mono" id="sched-info" style="margin-top:12px; font-size:12px;"></div>
  </div>
</main>

<footer>
  Snoop v__VERSION__ · local only · nothing leaves this machine
</footer>

<script>
const SEVERITY_COLORS = {
  critical: '#ef4444',
  high:     '#fb923c',
  medium:   '#facc15',
  low:      '#60a5fa',
  pass:     '#4ade80',
};
const CATEGORY_COLORS = {
  system:  '#ff79c6',
  network: '#a78bfa',
  files:   '#60a5fa',
  browser: '#4ade80',
};

function el(id) { return document.getElementById(id); }
function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function showBanner(msg, isError) {
  const b = el('banner');
  b.textContent = msg;
  b.className = 'banner show' + (isError ? ' error' : '');
  setTimeout(() => { b.className = 'banner'; }, 4000);
}

function renderGauge(score) {
  score = Number(score) || 0;
  const svg = el('gauge');
  const cx = 90, cy = 100, r = 70;
  const startAngle = 180, endAngle = 0;

  function polar(cx, cy, r, deg) {
    const rad = deg * Math.PI / 180;
    return [cx + r * Math.cos(rad), cy - r * Math.sin(rad)];
  }
  function arcPath(cx, cy, r, a1, a2) {
    const [x1, y1] = polar(cx, cy, r, a1);
    const [x2, y2] = polar(cx, cy, r, a2);
    const large = Math.abs(a2 - a1) > 180 ? 1 : 0;
    const sweep = a2 < a1 ? 1 : 0;
    return `M ${x1} ${y1} A ${r} ${r} 0 ${large} ${sweep} ${x2} ${y2}`;
  }

  const color = score >= 80 ? '#4ade80' : score >= 60 ? '#facc15' : '#ef4444';
  const angle = startAngle + (score / 100) * (endAngle - startAngle);
  const bg = `<path d="${arcPath(cx, cy, r, startAngle, endAngle)}" stroke="#30363d" stroke-width="12" fill="none" stroke-linecap="round"/>`;
  const fg = score > 0
    ? `<path d="${arcPath(cx, cy, r, startAngle, angle)}" stroke="${color}" stroke-width="12" fill="none" stroke-linecap="round"/>`
    : '';

  svg.innerHTML = bg + fg + `
    <text x="${cx}" y="${cy - 8}" text-anchor="middle" fill="#e6edf3" font-size="26" font-weight="800">${score}</text>
    <text x="${cx}" y="${cy + 10}" text-anchor="middle" fill="#8b949e" font-size="11">out of 100</text>
  `;
}

function renderSeverityChart(counts) {
  const container = el('severity-chart');
  const levels = ['critical', 'high', 'medium', 'low'];
  const labels = { critical: 'Critical', high: 'High', medium: 'Medium', low: 'Low' };
  const max = Math.max(1, ...levels.map(l => counts[l] || 0));

  let html = '';
  levels.forEach(l => {
    const v = counts[l] || 0;
    const pct = (v / max) * 100;
    html += `
      <div style="margin-bottom:10px;">
        <div style="display:flex; justify-content:space-between; font-size:12px; margin-bottom:4px;">
          <span>${labels[l]}</span>
          <span class="mono" style="color:${SEVERITY_COLORS[l]}">${v}</span>
        </div>
        <div style="height:8px; background:#0d1117; border-radius:4px; overflow:hidden;">
          <div style="width:${pct}%; height:100%; background:${SEVERITY_COLORS[l]}; transition:width 0.4s;"></div>
        </div>
      </div>`;
  });
  container.innerHTML = html;
}

function renderCategoryChart(threats) {
  const container = el('category-chart');
  const counts = { system: 0, network: 0, files: 0, browser: 0 };
  threats.forEach(t => { counts[t.category] = (counts[t.category] || 0) + 1; });
  const total = Object.values(counts).reduce((a, b) => a + b, 0);

  if (total === 0) {
    container.innerHTML = '<div class="empty">No threats detected.</div>';
    return;
  }

  let html = '<div style="display:flex; flex-direction:column; gap:10px;">';
  Object.entries(counts).forEach(([cat, n]) => {
    if (n === 0) return;
    const pct = Math.round((n / total) * 100);
    html += `
      <div style="display:flex; align-items:center; gap:10px; font-size:13px;">
        <span style="background:${CATEGORY_COLORS[cat] || '#888'}; width:10px; height:10px; border-radius:2px; display:inline-block;"></span>
        <span style="width:70px; text-transform:capitalize;">${cat}</span>
        <span class="mono muted" style="width:40px; text-align:right;">${n}</span>
        <div style="flex:1; height:6px; background:#0d1117; border-radius:3px; overflow:hidden;">
          <div style="width:${pct}%; height:100%; background:${CATEGORY_COLORS[cat] || '#888'};"></div>
        </div>
        <span class="muted mono" style="width:40px; text-align:right; font-size:11px;">${pct}%</span>
      </div>`;
  });
  html += '</div>';
  container.innerHTML = html;
}

function renderHistoryChart(history) {
  const svg = el('history-chart');
  const W = 800, H = 220;
  const pad = { l: 44, r: 20, t: 16, b: 34 };
  const chartW = W - pad.l - pad.r;
  const chartH = H - pad.t - pad.b;

  const data = history.slice().reverse();

  if (data.length === 0) {
    svg.innerHTML = `<text x="${W/2}" y="${H/2}" fill="#8b949e" font-size="13" text-anchor="middle">No scans yet</text>`;
    return;
  }

  const xs = i => pad.l + (data.length === 1 ? chartW / 2 : (i / (data.length - 1)) * chartW);
  const ys = s => pad.t + (1 - s / 100) * chartH;

  let grid = '';
  for (let s = 0; s <= 100; s += 25) {
    const y = ys(s);
    grid += `<line x1="${pad.l}" y1="${y}" x2="${W - pad.r}" y2="${y}" stroke="#21262d" stroke-width="1"/>`;
    grid += `<text x="${pad.l - 10}" y="${y + 4}" fill="#8b949e" font-size="11" text-anchor="end">${s}</text>`;
  }

  const points = data.map((d, i) => `${xs(i)},${ys(d.score)}`).join(' ');

  const areaPath = `M ${xs(0)} ${ys(data[0].score)} `
    + data.map((d, i) => `L ${xs(i)} ${ys(d.score)}`).join(' ')
    + ` L ${xs(data.length - 1)} ${H - pad.b} L ${xs(0)} ${H - pad.b} Z`;

  let dots = '';
  data.forEach((d, i) => {
    const cx = xs(i), cy = ys(d.score);
    const color = d.score >= 80 ? '#4ade80' : d.score >= 60 ? '#facc15' : '#ef4444';
    dots += `<circle cx="${cx}" cy="${cy}" r="3.5" fill="${color}" stroke="#161b22" stroke-width="1.5"/>`;
  });

  let xlabels = '';
  const step = Math.max(1, Math.ceil(data.length / 8));
  data.forEach((d, i) => {
    if (i % step !== 0 && i !== data.length - 1) return;
    const ts = d.timestamp ? d.timestamp.replace('T', ' ').slice(5, 16) : '';
    xlabels += `<text x="${xs(i)}" y="${H - pad.b + 18}" fill="#8b949e" font-size="10" text-anchor="middle">${esc(ts)}</text>`;
  });

  svg.innerHTML = `
    <defs>
      <linearGradient id="area-grad" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="#ff79c6" stop-opacity="0.25"/>
        <stop offset="100%" stop-color="#ff79c6" stop-opacity="0"/>
      </linearGradient>
    </defs>
    ${grid}
    <path d="${areaPath}" fill="url(#area-grad)"/>
    <polyline points="${points}" stroke="#ff79c6" stroke-width="2" fill="none" stroke-linejoin="round" stroke-linecap="round"/>
    ${dots}
    ${xlabels}
  `;
}

function renderThreats(threats) {
  const body = el('threats-body');
  if (!threats || threats.length === 0) {
    body.innerHTML = '<tr><td colspan="4" class="empty">No threats detected. Nice.</td></tr>';
    return;
  }
  const order = { critical: 0, high: 1, medium: 2, low: 3 };
  const sorted = threats.slice().sort((a, b) => (order[a.severity] ?? 9) - (order[b.severity] ?? 9));

  body.innerHTML = sorted.map(t => `
    <tr>
      <td><span class="chip ${esc(t.severity)}">${esc(t.severity)}</span></td>
      <td class="muted" style="text-transform:capitalize;">${esc(t.category)}</td>
      <td>${esc(t.title)}</td>
      <td class="muted mono" style="max-width:380px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${esc(t.detail)}</td>
    </tr>
  `).join('');
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

async function refresh() {
  try {
    const status = await api('/api/status');
    el('status-text').textContent = status.scanning
      ? 'scanning…'
      : (status.last_scan ? `last scan ${status.last_scan.timestamp.replace('T',' ').slice(0,16)}` : 'idle');
    el('pulse').className = 'pulse' + (status.scanning ? ' scanning' : '');

    const sched = status.schedule || {};
    if (document.activeElement !== el('sched-enabled'))
      el('sched-enabled').checked = !!sched.enabled;
    if (document.activeElement !== el('sched-interval'))
      el('sched-interval').value = sched.interval_hours || 24;
    if (document.activeElement !== el('sched-scan'))
      el('sched-scan').value = sched.scan_type || 'all';
    if (document.activeElement !== el('sched-path'))
      el('sched-path').value = sched.path || '~';

    el('sched-info').textContent = sched.enabled
      ? `Next run: ${sched.next_run ? sched.next_run.replace('T',' ').slice(0,16) : 'pending'}`
      : 'Scheduler is off.';

    if (status.last_scan && status.last_scan.id) {
      const report = await api('/api/report/' + encodeURIComponent(status.last_scan.id));
      renderGauge(report.score);
      el('score-big').textContent = report.score;
      el('score-grade').textContent = `grade ${report.grade}`;
      renderSeverityChart(report.severity_counts || {});
      renderCategoryChart(report.threats || []);
      renderThreats(report.threats || []);
    } else {
      renderGauge(0);
      el('score-big').textContent = '—';
      el('score-grade').textContent = 'no scans yet';
      renderSeverityChart({});
      renderCategoryChart([]);
      renderThreats([]);
    }

    const hist = await api('/api/history?limit=60');
    renderHistoryChart(hist);

  } catch (e) {
    el('status-text').textContent = 'disconnected';
    el('pulse').className = 'pulse';
  }
}

el('scan-now').addEventListener('click', async () => {
  el('scan-now').disabled = true;
  try {
    await api('/api/scan', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    showBanner('Scan started — results will appear shortly.');
    setTimeout(refresh, 1500);
  } catch (e) {
    showBanner('Failed to start scan: ' + e.message, true);
  } finally {
    el('scan-now').disabled = false;
  }
});

el('schedule-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const payload = {
    enabled: el('sched-enabled').checked,
    interval_hours: parseInt(el('sched-interval').value, 10) || 24,
    scan_type: el('sched-scan').value,
    path: el('sched-path').value || '~',
  };
  try {
    await api('/api/schedule', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    showBanner('Schedule saved.');
    refresh();
  } catch (e) {
    showBanner('Failed to save schedule: ' + e.message, true);
  }
});

el('delete-all').addEventListener('click', async () => {
  if (!confirm('Clear all scan history? This cannot be undone.')) return;
  try {
    await api('/api/history', { method: 'DELETE' });
    showBanner('History cleared.');
    refresh();
  } catch (e) {
    showBanner('Failed to clear: ' + e.message, true);
  }
});

refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""


class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP handler for the dashboard and API."""

    scheduler = None  # set by serve_dashboard

    # Quiet the default request logger
    def log_message(self, format, *args):
        pass

    # ── helpers ────────────────────────────────────────────────────
    def _same_origin(self):
        """Return True if the request looks same-origin (or non-browser).

        Browsers always send an Origin header on cross-origin fetch/POST.
        A page at evil.com trying to POST to 127.0.0.1:8765 will include
        Origin: https://evil.com, which we reject. curl and native clients
        often omit Origin entirely — those are allowed.
        """
        origin = self.headers.get("Origin", "")
        referer = self.headers.get("Referer", "")
        loopback = ("127.0.0.1", "localhost", "[::1]")

        if origin:
            return any(h in origin for h in loopback)
        if referer:
            return any(h in referer for h in loopback)
        return True

    def _send(self, status, body, content_type="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, default=str).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")

        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        if content_type.startswith("text/html"):
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; "
                "connect-src 'self'; "
                "frame-ancestors 'none'; "
                "base-uri 'none'; "
                "form-action 'none'",
            )
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length == 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    def _forbidden(self):
        return self._send(403, {"error": "cross-origin request rejected"})

    # ── HTTP methods ───────────────────────────────────────────────
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        try:
            if path == "/" or path == "/index.html":
                html = (
                    DASHBOARD_HTML
                    .replace("__TAGLINE__", TAGLINE)
                    .replace("__VERSION__", VERSION)
                )
                return self._send(200, html, "text/html; charset=utf-8")

            if path == "/api/status":
                history = list_history(limit=1)
                last = history[0] if history else None
                cfg = load_config()
                return self._send(200, {
                    "version": VERSION,
                    "scanning": self.scheduler.is_scanning() if self.scheduler else False,
                    "last_error": self.scheduler.last_error() if self.scheduler else None,
                    "last_scan": last,
                    "schedule": cfg.get("schedule", {}),
                    "history_count": len(list_history()),
                })

            if path == "/api/history":
                raw = query.get("limit", ["60"])[0]
                try:
                    limit = int(raw)
                except (ValueError, TypeError):
                    limit = 60
                limit = max(1, min(limit, 500))
                return self._send(200, list_history(limit=limit))

            if path == "/api/latest":
                history = list_history(limit=1)
                if not history:
                    return self._send(404, {"error": "no reports"})
                report = load_report(history[0]["id"])
                return self._send(200, report or {})

            if path.startswith("/api/report/"):
                rid = path[len("/api/report/"):]
                report = load_report(rid)
                if report is None:
                    return self._send(404, {"error": "not found"})
                return self._send(200, report)

            if path == "/api/schedule":
                cfg = load_config()
                return self._send(200, cfg.get("schedule", {}))

            return self._send(404, {"error": "not found"})

        except Exception as e:
            return self._send(500, {"error": str(e)})

    def do_POST(self):
        if not self._same_origin():
            return self._forbidden()

        parsed = urlparse(self.path)
        path = parsed.path

        try:
            if path == "/api/scan":
                if not self.scheduler:
                    return self._send(500, {"error": "scheduler not running"})
                body = self._read_json()
                scan_type = body.get("scan_type", "all")
                scan_path = body.get("path") or None
                try:
                    max_files = int(body.get("max_files", 50000))
                except (ValueError, TypeError):
                    max_files = 50000
                ok = self.scheduler.run_now(scan_type=scan_type, path=scan_path, max_files=max_files)
                return self._send(200, {"ok": ok, "message": "scan started" if ok else "scan already running"})

            if path == "/api/schedule":
                body = self._read_json()
                cfg = load_config()
                sched = cfg.setdefault("schedule", {})
                sched["enabled"] = bool(body.get("enabled", False))
                try:
                    sched["interval_hours"] = max(1, int(body.get("interval_hours", 24)))
                except (ValueError, TypeError):
                    sched["interval_hours"] = 24
                sched["scan_type"] = body.get("scan_type", "all")
                sched["path"] = body.get("path", "~")
                try:
                    sched["max_files"] = int(body.get("max_files", 50000))
                except (ValueError, TypeError):
                    sched["max_files"] = 50000
                if sched["enabled"]:
                    next_run = datetime.now() + timedelta(hours=sched["interval_hours"])
                    sched["next_run"] = next_run.isoformat(timespec="seconds")
                else:
                    sched["next_run"] = None
                save_config(cfg)
                return self._send(200, {"ok": True, "schedule": sched})

            return self._send(404, {"error": "not found"})

        except Exception as e:
            return self._send(500, {"error": str(e)})

    def do_DELETE(self):
        if not self._same_origin():
            return self._forbidden()

        parsed = urlparse(self.path)
        path = parsed.path

        try:
            if path == "/api/history":
                ensure_dirs()
                count = 0
                for fp in HISTORY_DIR.glob("*.json"):
                    try:
                        fp.unlink()
                        count += 1
                    except OSError:
                        pass
                return self._send(200, {"ok": True, "deleted": count})

            if path.startswith("/api/report/"):
                rid = path[len("/api/report/"):]
                ok = delete_report(rid)
                return self._send(200 if ok else 404, {"ok": ok})

            return self._send(404, {"error": "not found"})

        except Exception as e:
            return self._send(500, {"error": str(e)})


def find_free_port(start=8765, end=8775):
    """Return the first free port in [start, end], or None."""
    import socket
    for p in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return None


def serve_dashboard(port=8765, open_browser=True):
    """Start the dashboard server and scheduler. Blocks until Ctrl+C."""
    ensure_dirs()
    load_config()

    # Resolve the port. port=0 means "find a free one, or let the OS pick".
    requested_port = port
    if port == 0:
        port = find_free_port()
    if port is None:
        port = 0  # OS picks any free port

    scheduler = Scheduler()
    scheduler.start()

    handler = type("Handler", (DashboardHandler,), {"scheduler": scheduler})

    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError as e:
        scheduler.stop()
        print()
        print(c(f"[!] Could not bind 127.0.0.1:{requested_port or port}: {e}", C.RED))
        print(c(f"    Try a different port: python snoop.py --dashboard --port 9000", C.DIM))
        print(c(f"    Or let the OS pick one: python snoop.py --dashboard --port 0", C.DIM))
        return 1

    actual_port = server.server_address[1]
    url = f"http://127.0.0.1:{actual_port}/"

    print()
    print(c("╔" + "═" * 58 + "╗", C.MAGENTA))
    print(c(f"║  Snoop Dashboard v{VERSION}".ljust(59) + "║", C.MAGENTA + C.BOLD))
    print(c(f"║  {TAGLINE}".ljust(59) + "║", C.MAGENTA))
    print(c("╚" + "═" * 58 + "╝", C.MAGENTA))
    print()
    print(c(f"  → Dashboard:  {url}", C.CYAN + C.BOLD))
    print(c(f"  → History:    {HISTORY_DIR}", C.BLUE))
    print(c(f"  → Config:     {CONFIG_FILE}", C.BLUE))
    print()
    print(c("  Press Ctrl+C to stop.", C.DIM))
    print()

    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(c("\n[*] Shutting down…", C.YELLOW))
    finally:
        scheduler.stop()
        try:
            server.shutdown()
        except Exception:
            pass
    return 0


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
    p.add_argument("--no-progress", action="store_true",
                   help="Disable the live progress bar")
    p.add_argument("--max-files", type=int, default=50000,
                   help="Cap on files scanned (default 50000)")
    p.add_argument("--no-history", action="store_true",
                   help="Don't save this scan to ~/.snoop/history")
    p.add_argument("--history", action="store_true",
                   help="Show recent scan history and exit")
    p.add_argument("--dashboard", action="store_true",
                   help="Launch the web dashboard")
    p.add_argument("--port", type=int, default=8765,
                   help="Dashboard port (default 8765, use 0 for auto)")
    p.add_argument("--no-browser", action="store_true",
                   help="Don't auto-open the browser when starting the dashboard")
    p.add_argument("--schedule-every", metavar="Nh",
                   help="Headless mode: run scans every N hours (e.g. 6h, 24h)")
    p.add_argument("--version", action="version", version=f"Snoop v{VERSION}")
    return p.parse_args(argv)


def print_banner():
    print()
    print(c("╔" + "═" * 58 + "╗", C.MAGENTA))
    print(c(f"║  Snoop v{VERSION}".ljust(59) + "║", C.MAGENTA + C.BOLD))
    print(c(f"║  {TAGLINE}".ljust(59) + "║", C.MAGENTA))
    print(c("║  Local-only · Nothing leaves this machine.".ljust(59) + "║", C.MAGENTA))
    print(c("╚" + "═" * 58 + "╝", C.MAGENTA))


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


def show_history():
    history = list_history(limit=20)
    if not history:
        print(c("No scan history yet. Run a scan first.", C.YELLOW))
        return 0

    print()
    print(c(f"{'ID':<20} {'When':<20} {'Score':<8} {'Grade':<6} Threats", C.BOLD))
    print(c("─" * 70, C.DIM))
    for h in history:
        counts = h.get("severity_counts", {})
        total = sum(counts.values()) if counts else 0
        ts = h.get("timestamp", "").replace("T", " ")[:16]
        color = C.GREEN if h["score"] >= 80 else C.YELLOW if h["score"] >= 60 else C.RED
        print(c(f"{h['id']:<20} {ts:<20} {h['score']:<8} {h['grade']:<6} {total}", color))
    print()
    print(c(f"Full reports in: {HISTORY_DIR}", C.DIM))
    print()
    return 0


def parse_interval(s):
    """Parse '6h', '24h', '30m', '2d' → hours as float."""
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*([hmd]?)\s*$", s.lower())
    if not m:
        raise argparse.ArgumentTypeError(f"invalid interval: {s}")
    n = float(m.group(1))
    unit = m.group(2) or "h"
    if unit == "m":
        return n / 60
    if unit == "d":
        return n * 24
    return n


def headless_schedule(interval_hours, scan_type, path, max_files, quiet=False):
    """Run scans on a fixed interval, headless (no HTTP server)."""
    if not quiet:
        print_banner()
        print(c(f"  Headless mode · scanning every {interval_hours}h", C.CYAN))
        print(c(f"  Reports saved to {HISTORY_DIR}", C.BLUE))
        print(c("  Press Ctrl+C to stop.\n", C.DIM))

    while True:
        start = datetime.now()
        log(f"[{start:%Y-%m-%d %H:%M:%S}] Starting scheduled scan…", C.MAGENTA, quiet)
        try:
            report = run_full_scan(
                scan_type=scan_type, path=path,
                quiet=True, max_files=max_files, progress=False,
            )
            rid = save_history(report)
            threats = report.get("severity_counts", {})
            total = sum(threats.values()) if threats else 0
            color = C.GREEN if report["score"] >= 80 else C.YELLOW if report["score"] >= 60 else C.RED
            log(f"  → {rid}  score={report['score']}  grade={report['grade']}  threats={total}", color, quiet)
        except Exception as e:
            log(f"  [!] Scan failed: {e}", C.RED, quiet)

        next_run = start + timedelta(hours=interval_hours)
        log(f"  Next scan at {next_run:%Y-%m-%d %H:%M:%S}\n", C.DIM, quiet)
        try:
            time.sleep(interval_hours * 3600)
        except KeyboardInterrupt:
            log("\n[*] Stopped.", C.YELLOW, quiet)
            return 0


def main(argv=None):
    global USE_COLOR

    args = parse_args(argv)
    if args.no_color or not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
        USE_COLOR = False

    # Dashboard mode
    if args.dashboard:
        return serve_dashboard(port=args.port, open_browser=not args.no_browser)

    # History viewer
    if args.history:
        return show_history()

    # Headless scheduler
    if args.schedule_every:
        try:
            interval = parse_interval(args.schedule_every)
        except argparse.ArgumentTypeError as e:
            print(c(f"[!] {e}", C.RED), file=sys.stderr)
            return 2
        return headless_schedule(
            interval, args.scan, args.path, args.max_files, quiet=args.quiet,
        )

    # Default: one-shot scan
    if not args.quiet:
        print_banner()

    if not HAS_PSUTIL and not args.quiet:
        log("\n[i] psutil not installed — some checks will use fallbacks.", C.YELLOW)
        log("    Install for best results: pip install psutil", C.DIM)

    use_progress = (
        not args.no_progress
        and not args.quiet
        and not os.environ.get("NO_PROGRESS")
    )

    report = run_full_scan(
        scan_type=args.scan,
        path=args.path,
        quiet=args.quiet,
        max_files=args.max_files,
        progress=use_progress,
    )

    header("SCORE", args.quiet)
    score_color = C.GREEN if report["score"] >= 80 else C.YELLOW if report["score"] >= 60 else C.RED
    log(f"  Privacy score: {report['score']}/100  (grade {report['grade']})", score_color, args.quiet)

    print_advice(report["advice"], quiet=args.quiet)

    if not args.no_history:
        try:
            rid = save_history(report)
            if not args.quiet:
                log(f"\n[i] Saved to history: {rid}", C.DIM)
                log(f"    View all: python snoop.py --history", C.DIM)
                log(f"    Dashboard: python snoop.py --dashboard", C.DIM)
        except Exception as e:
            log(f"\n[!] Could not save to history: {e}", C.RED, args.quiet)

    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, default=str)
            log(f"[+] Report saved to {args.output}", C.GREEN, args.quiet)
        except OSError as e:
            log(f"[!] Could not write report: {e}", C.RED, args.quiet)

    if not args.quiet:
        print()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[!] Interrupted.", file=sys.stderr)
        sys.exit(130)
