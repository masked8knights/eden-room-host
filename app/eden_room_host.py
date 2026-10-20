#!/usr/bin/env python3
"""Eden Room Host: tiny launcher for the eden-room dedicated server.

Works on Linux and Windows. The playit.gg agent can be started and claimed
from inside the app, so a fresh install only needs this one program.
"""
import glob
import json
import os

import queue
import re
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import ttk, messagebox

IS_WIN = os.name == "nt"

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
CLAIM_RE = re.compile(r"https://playit\.gg/claim/[A-Za-z0-9\-]+")

HOME = os.path.expanduser("~")


def app_dir():
    """Folder holding bundled binaries (exe/AppImage data or source dir)."""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def data_dir():
    """Per-user folder for logs and settings."""
    if IS_WIN:
        base = os.environ.get("LOCALAPPDATA") or os.path.join(HOME, "AppData", "Local")
        return os.path.join(base, "EdenRoomHost")
    return os.path.join(HOME, ".local/share/eden-room-host")

APP_DIR = app_dir()
LOG_DIR = data_dir()
OUT_LOG = os.path.join(LOG_DIR, "room.log")
ROOM_LOG = os.path.join(LOG_DIR, "eden-room.log")
SETTINGS = os.path.join(LOG_DIR, "settings.json")
PLAYIT_URL = "https://playit.gg/account/tunnels"
PORT = 24872
EXTS = (".nsp", ".xci", ".nso", ".nro", ".kip", ".kip1")

EDEN_CONFIGS = []
if IS_WIN:
    appdata = os.environ.get("APPDATA") or os.path.join(HOME, "AppData", "Roaming")
    EDEN_CONFIGS = [
        os.path.join(appdata, "eden", "qt-config.ini"),
        os.path.join(appdata, "Eden", "qt-config.ini"),
    ]
else:
    EDEN_CONFIGS = [os.path.join(HOME, ".config/eden/qt-config.ini")]


def find_binary(names, extra_dirs=()):
    """First existing match for these names in the app folder, extras or PATH."""
    for base in (APP_DIR,) + tuple(extra_dirs):
        for name in names:
            p = os.path.join(base, name)
            if os.path.exists(p):
                return p
    for name in names:
        for d in os.environ.get("PATH", "").split(os.pathsep):
            p = os.path.join(d, name)
            if os.path.exists(p):
                return p
    return None

EXE = ".exe" if IS_WIN else ""
ROOM_BIN = find_binary(["eden-room" + EXE], [os.path.join(HOME, ".local/bin")])
PLAYIT_BIN = find_binary(["playit" + EXE, "playit-cli" + EXE],
                         [os.path.join(HOME, ".local/bin"), "/usr/bin", "/usr/local/bin"])

# Nobara / Eden dark navy theme with indigo accents
BG = "#12141d"
PANEL = "#1a1d2b"
PANEL2 = "#232839"
ACCENT = "#5b67d8"
ACCENT_BRIGHT = "#7d88e8"
TEXT = "#e8e9f2"
MUTED = "#8a8fa8"
OK = "#4ade80"
BAD = "#f87171"
LINK = "#8f9cf0"

MONO = ("Consolas", 9) if IS_WIN else ("Monospace", 9)


def load_settings():
    try:
        with open(SETTINGS, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(cfg):
    os.makedirs(LOG_DIR, exist_ok=True)
    try:
        with open(SETTINGS, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
    except OSError:
        pass


def eden_game_dirs():
    """Read the game folders Eden scans from its config."""
    dirs = []
    for conf in EDEN_CONFIGS:
        try:
            with open(conf, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        for line in text.splitlines():
            m = re.match(r"Paths\\gamedirs\\\d+\\path=(.+)$", line.strip())
            if m:
                path = m.group(1).strip().strip('"')
                if path and (IS_WIN or path.startswith("/")) and os.path.isdir(path):
                    dirs.append(path)
        if dirs:
            break
    return dirs


def pretty_name(filename):
    """Strip folder and the [ids][v..][region] tags from a ROM filename."""
    name = os.path.splitext(os.path.basename(filename))[0]
    name = re.sub(r"\s*\[[^\]]*\]", "", name)
    return name.strip() or name


def find_games():
    """One entry per game title, preferring the base game over updates/DLC."""
    candidates = {}
    seen = set()
    for d in eden_game_dirs():
        for ext in EXTS:
            for path in sorted(glob.glob(os.path.join(d, "**", "*" + ext), recursive=True)):
                if path in seen:
                    continue
                seen.add(path)
                name = pretty_name(path)
                key = re.sub(r"[^a-z0-9]", "", name.lower())
                tid = re.search(r"\[([0-9A-Fa-f]{16})\]", path)
                is_base_id = bool(tid and tid.group(1).upper().endswith("000"))
                has_version = bool(re.search(r"\[v[1-9]", path))
                score = (1 if is_base_id else 0, 0 if has_version else 1)
                current = candidates.get(key)
                if current is None or score > current[0]:
                    candidates[key] = (score, path, name)
    return [(c[2], c[1]) for _, c in sorted(candidates.items())]


if IS_WIN:
    def room_running():
        """True when a live eden-room.exe process exists."""
        r = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq eden-room.exe", "/NH"],
            capture_output=True, text=True, creationflags=0x08000000)
        return "eden-room.exe" in (r.stdout or "").lower()

    def stop_room():
        subprocess.run(["taskkill", "/F", "/T", "/IM", "eden-room.exe"],
                       capture_output=True, creationflags=0x08000000)
else:
    def room_running():
        """True when a live (non-zombie) room server process exists."""
        for name in ("eden-room", "eden-room-x86_6"):
            r = subprocess.run(["pgrep", "-x", name], capture_output=True, text=True)
            for pid in r.stdout.split():
                try:
                    with open(f"/proc/{pid}/stat") as f:
                        if f.read().split()[2] != "Z":
                            return True
                except OSError:
                    continue
        return False

    def stop_room():
        """Kill every live room server (TERM is ignored by eden-room, use KILL)."""
        for name in ("eden-room", "eden-room-x86_6"):
            subprocess.run(["pkill", "-9", "-x", name], capture_output=True)


def wait_port_free(timeout=3.0):
    """Block until UDP 127.0.0.1:PORT can be bound again."""
    end = time.time() + timeout
    while time.time() < end:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.bind(("127.0.0.1", PORT))
            s.close()
            return True
        except OSError:
            s.close()
            time.sleep(0.05)
    return False


def start_room(name, password, players, game_title, game_id=None):
    """Launch eden-room and return (proc, stream) where stream yields its output."""
    stop_room()
    if not wait_port_free():
        raise RuntimeError(f"port {PORT} is still in use")
    os.makedirs(LOG_DIR, exist_ok=True)
    args = [
        ROOM_BIN,
        "--room-name", name,
        "--bind-address", "127.0.0.1",
        "--port", str(PORT),
        "--max-members", str(players),
        "--password", password,
        "--preferred-game", game_title,
        "--preferred-game-id", game_id or "0000000000000000",
        "--log-file", ROOM_LOG,
    ]
    if IS_WIN:
        proc = subprocess.Popen(args, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                creationflags=0x08000000)
        return proc, proc.stdout
    import pty
    master, slave = pty.openpty()
    try:
        proc = subprocess.Popen(args, stdin=slave, stdout=slave, stderr=slave,
                                start_new_session=True, close_fds=True)
    finally:
        os.close(slave)
    return proc, master


# Find the playit systemd unit by scanning unit directories directly instead
# of asking systemctl, which may not be on PATH when launched from a GUI menu.
PLAYIT_UNIT = None
if not IS_WIN:
    for _d in ("/etc/systemd/system", "/usr/lib/systemd/system"):
        try:
            for _f in os.listdir(_d):
                if _f.startswith("playit") and _f.endswith(".service"):
                    PLAYIT_UNIT = _f
                    break
        except OSError:
            continue
        if PLAYIT_UNIT:
            break


def _systemctl(*args):
    """Run systemctl via an absolute path so GUI launches (sparse PATH) work."""
    for cmd in ("/usr/bin/systemctl", "/bin/systemctl", "/usr/local/bin/systemctl"):
        if os.path.exists(cmd):
            return subprocess.run([cmd, *args], capture_output=True, text=True)
    return subprocess.run(["systemctl", *args], capture_output=True, text=True)


def tunnel_service_exists():
    """Linux only: is a playit systemd unit installed?"""
    return PLAYIT_UNIT is not None


def tunnel_active():
    """True when the playit agent is running (service or child process)."""
    if IS_WIN:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq playit.exe", "/NH"],
                           capture_output=True, text=True, creationflags=0x08000000)
        return "playit.exe" in (r.stdout or "").lower()
    if PLAYIT_UNIT:
        r = _systemctl("is-active", PLAYIT_UNIT)
        return bool(r) and r.stdout.strip() == "active"
    r = subprocess.run(["pgrep", "-x", "playit"], capture_output=True, text=True)
    return bool(r.stdout.strip())


def tunnel_toggle(on):
    """Linux: start/stop the playit service (polkit rule makes this passwordless)."""
    if not PLAYIT_UNIT:
        return False
    _systemctl("start" if on else "stop", PLAYIT_UNIT)
    return tunnel_active()


def playit_linked():
    """Has this machine ever been signed in to playit (a claim exists)?"""
    if IS_WIN:
        base = os.environ.get("LOCALAPPDATA") or os.path.join(HOME, "AppData", "Local")
        paths = [os.path.join(base, "playit", "playit.toml"),
                 os.path.join(base, "playit-agent", "playit.toml")]
    else:
        paths = ["/etc/playit/playit.toml",
                 "/etc/playit/playit.d",
                 os.path.join(HOME, ".config", "playit", "playit.toml"),
                 os.path.join(HOME, ".playit.toml")]
    return (PLAYIT_UNIT is not None
            or any(os.path.exists(p) for p in paths)
            or load_settings().get("playit_claimed", False))


class Wizard(tk.Toplevel):
    """First-run setup: playit claim + links for everything else."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Eden Room Host setup")
        self.configure(background=BG)
        self.resizable(False, False)
        self.transient(app)
        self.grab_set()
        frm = tk.Frame(self, background=BG)
        frm.pack(padx=18, pady=14)
        tk.Label(frm, text="Welcome to Eden Room Host", background=BG,
                 foreground=ACCENT_BRIGHT, font=("Sans", 13, "bold")).pack(anchor="w")
        self.lines = tk.Frame(frm, background=BG)
        self.lines.pack(anchor="w", pady=(8, 4))
        self.step_idx = 0
        self.claim_seen = load_settings().get("playit_claimed", False)
        self.show_intro()

    def row(self, text, ok=None):
        color = TEXT if ok is None else (OK if ok else BAD)
        mark = "\u2713" if ok else ("\u2717" if ok is False else "\u2022")
        tk.Label(self.lines, text=f"{mark}  {text}", background=BG,
                 foreground=color, font=("Sans", 10), justify="left",
                 anchor="w").pack(anchor="w", pady=2)

    def show_intro(self):
        for w in self.lines.winfo_children():
            w.destroy()
        self.row("eden-room server "
                 + (f"found ({os.path.basename(ROOM_BIN)})" if ROOM_BIN
                    else "NOT found, re-download this app"),
                 ROOM_BIN is not None)
        self.row("playit agent "
                 + (f"found ({os.path.basename(PLAYIT_BIN)})" if PLAYIT_BIN
                    else "NOT found"),
                 PLAYIT_BIN is not None)
        if not tunnel_service_exists() and PLAYIT_BIN is None:
            self.row("No playit agent anywhere, get it from playit.gg/download", False)
        btns = tk.Frame(self, background=BG)
        btns.pack(pady=(4, 12))
        if PLAYIT_BIN and not playit_linked():
            b = ttk.Button(btns, text="Sign in to playit (claim agent)" if IS_WIN
                           else "Run playit to claim this agent",
                           command=self.claim)
            b.pack(side="left", padx=6)
        elif PLAYIT_BIN and playit_linked():
            self.row("playit already signed in on this machine", True)
        ttk.Button(btns, text="Open Eden setup guide",
                   command=lambda: webbrowser.open(
                       "https://eden-emu.dev/")).pack(side="left", padx=6)
        ttk.Button(btns, text="Done", command=self.close).pack(side="left", padx=6)
        tip = ("Eden needs its own setup for keys and games; the guide above "
               "covers that. This app reads the game folders Eden already scans.")
        tk.Label(self, text=tip, background=BG, foreground=MUTED,
                 font=("Sans", 9), wraplength=380, justify="left"
                 ).pack(padx=18, pady=(0, 12), anchor="w")

    def claim(self):
        """Start the agent and open its claim URL in the browser."""
        self.app.emit("Starting playit agent for sign-in...")
        self.app.start_playit()
        cfg = load_settings()
        cfg["playit_claimed"] = True
        save_settings(cfg)
        self.claim_seen = True
        for w in self.lines.winfo_children():
            w.destroy()
        self.row("Waiting for a claim link from the agent...")
        self.row("If a browser tab opened, click Agree to link this agent "
                 "to your playit.gg account.")
        btns = tk.Frame(self, background=BG)
        btns.pack(pady=(4, 12))
        ttk.Button(btns, text="Close", command=self.close).pack(side="left", padx=6)

    def close(self):
        self.grab_release()
        self.destroy()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Eden Room Host")
        self.resizable(False, False)
        self.configure(background=BG)
        self.process = None
        self.stream = None
        self.tunnel_proc = None
        self.q = queue.Queue()
        self._last_tunnel_check = 0.0
        self._claim_shown = False
        self.settings = load_settings()
        try:
            icon = os.path.join(APP_DIR, "icon.png" if not IS_WIN else "icon.ico")
            if os.path.exists(icon):
                if IS_WIN:
                    self.iconbitmap(icon)
                else:
                    self._icon = tk.PhotoImage(file=icon)
                    self.iconphoto(True, self._icon)
        except Exception:
            pass

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=TEXT,
                        fieldbackground=PANEL, bordercolor=PANEL2,
                        lightcolor=PANEL2, darkcolor=PANEL2, troughcolor=PANEL)
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("TEntry", fieldbackground=PANEL, foreground=TEXT,
                        insertcolor=TEXT, bordercolor=PANEL2, lightcolor=PANEL2,
                        darkcolor=PANEL2, padding=4)
        style.configure("TCombobox", fieldbackground=PANEL, background=PANEL2,
                        foreground=TEXT, arrowcolor=ACCENT, bordercolor=PANEL2,
                        lightcolor=PANEL2, darkcolor=PANEL2, padding=4)
        style.map("TCombobox",
                  fieldbackground=[("readonly", PANEL)],
                  foreground=[("readonly", TEXT)],
                  selectbackground=[("readonly", PANEL2)],
                  selectforeground=[("readonly", TEXT)])
        self.option_add("*TCombobox*Listbox.background", PANEL)
        self.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", PANEL2)
        self.option_add("*TCombobox*Listbox.selectForeground", ACCENT_BRIGHT)
        style.configure("TButton", background=ACCENT, foreground="#12121c",
                        borderwidth=0, focusthickness=0, padding=(12, 6))
        style.map("TButton", background=[("active", ACCENT_BRIGHT)])
        style.configure("Status.TLabel", background=BG)

        pad = dict(padx=14, pady=5)
        header = tk.Label(self, text="Eden Room Host", background=BG,
                          foreground=ACCENT_BRIGHT, font=("Sans", 14, "bold"))
        header.grid(row=0, column=0, columnspan=2, sticky="w", **pad)

        ttk.Label(self, text="Game").grid(row=1, column=0, sticky="w", **pad)
        self.game_box = ttk.Combobox(self, width=38, state="readonly")
        self.game_box.grid(row=1, column=1, sticky="we", **pad)
        games = find_games()
        if not games:
            self.game_box["values"] = ["(no games found)"]
            self.game_box.current(0)
            self.games = []
        else:
            self.games = games
            self.game_box["values"] = [g[0] for g in games]
            self.game_box.current(0)

        ttk.Label(self, text="Room name").grid(row=2, column=0, sticky="w", **pad)
        self.room_name = ttk.Entry(self, width=30)
        self.room_name.insert(0, "Eden Room")
        self.room_name.grid(row=2, column=1, sticky="we", **pad)

        ttk.Label(self, text="Password").grid(row=3, column=0, sticky="w", **pad)
        self.room_password = ttk.Entry(self, width=30)
        self.room_password.grid(row=3, column=1, sticky="we", **pad)

        ttk.Label(self, text="Max players").grid(row=4, column=0, sticky="w", **pad)
        self.players = ttk.Spinbox(self, from_=2, to=16, width=8)
        self.players.set(8)
        self.players.grid(row=4, column=1, sticky="w", **pad)

        self.status = ttk.Label(self, text="Room stopped", style="Status.TLabel",
                                foreground=BAD)
        self.status.grid(row=5, column=0, columnspan=2, sticky="w", **pad)

        btns = tk.Frame(self, background=BG)
        btns.grid(row=6, column=0, columnspan=2, sticky="we", padx=14, pady=(8, 2))
        self.start_btn = ttk.Button(btns, text="Start Room", command=self.start)
        self.start_btn.pack(side="left", expand=True, fill="x", padx=(0, 6))
        self.stop_btn = ttk.Button(btns, text="Stop Room", command=self.stop)
        self.stop_btn.pack(side="left", expand=True, fill="x")

        bottom = tk.Frame(self, background=BG)
        bottom.grid(row=7, column=0, columnspan=2, sticky="we",
                    padx=14, pady=(8, 18))
        link = tk.Label(bottom, text="open playit.gg tunnels", background=BG,
                        foreground=LINK, cursor="hand2")
        link.pack(side="left")
        link.bind("<Button-1>", lambda e: webbrowser.open(
            self.settings.get("playit_url", PLAYIT_URL)))
        self.tunnel = tk.Label(bottom, text="tunnel: ...", background=BG,
                               foreground=MUTED, cursor="hand2")
        self.tunnel.pack(side="right")
        self.tunnel.bind("<Button-1>", lambda e: self.toggle_tunnel())

        logbar = tk.Frame(self, background=BG)
        logbar.grid(row=8, column=0, columnspan=2, sticky="we",
                    padx=14, pady=(4, 2))
        tk.Label(logbar, text="LOG", background=BG, foreground=TEXT,
                 font=("Sans", 10, "bold")).pack(side="left")
        logbox = tk.Frame(self, background=PANEL)
        logbox.grid(row=9, column=0, columnspan=2, sticky="we",
                    padx=14, pady=(0, 14))
        text = tk.Text(logbox, background=PANEL, foreground=TEXT,
                       insertbackground=TEXT, relief="flat", height=9,
                       width=62, font=MONO, wrap="word",
                       borderwidth=0, highlightthickness=0)
        scroll = ttk.Scrollbar(logbox, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=4)
        scroll.pack(side="right", fill="y", padx=(0, 4), pady=4)
        self.log_text = text

        self.columnconfigure(1, weight=1)
        self.refresh_status()
        self.pulse()
        if not ROOM_BIN or not PLAYIT_BIN or not playit_linked():
            self.after(400, lambda: Wizard(self))

    # ---- logging -------------------------------------------------------
    def emit(self, msg):
        """App event, thread-safe, shown with a timestamp."""
        self.q.put(("event", f"[{time.strftime('%H:%M:%S')}] {msg}\n"))

    def start_pump(self, stream, logfile):
        """Read the room server's output stream and feed every chunk to the UI."""
        def reader():
            try:
                while True:
                    if IS_WIN:
                        chunk = stream.readline()
                        if not chunk:
                            break
                    else:
                        chunk = os.read(stream, 4096)
                        if not chunk:
                            break
                    text = ANSI_RE.sub("", chunk.decode("utf-8", "replace"))
                    self.q.put(("out", text))
                    if logfile:
                        logfile.write(text)
                        logfile.flush()
            except (OSError, ValueError):
                pass
            finally:
                if not IS_WIN:
                    try:
                        os.close(stream)
                    except OSError:
                        pass
                if logfile:
                    logfile.close()
        threading.Thread(target=reader, daemon=True).start()

    def pulse(self):
        """Drain the queue into the log widget, then refresh status."""
        while True:
            try:
                kind, payload = self.q.get_nowait()
            except queue.Empty:
                break
            if kind in ("event", "out") and self.log_text is not None:
                self.log_text.insert("end", payload)
                lines = int(self.log_text.index("end-1c").split(".")[0])
                if lines > 800:
                    self.log_text.delete("1.0", f"{lines - 500}.0")
                self.log_text.see("end")
        self.refresh_status()
        if time.time() - self._last_tunnel_check > 2:
            self._last_tunnel_check = time.time()
            self.refresh_tunnel()
        self.after(300, self.pulse)

    # ---- playit (Windows runs the agent as our child) --------------------
    def start_playit(self):
        """Launch the agent (child process) and watch for a claim URL.

        On Linux with the playit systemd service installed the service is
        used instead, since it is already claimed and survives reboots.
        """
        if not IS_WIN and tunnel_service_exists():
            return tunnel_toggle(True)
        if self.tunnel_proc is not None and self.tunnel_proc.poll() is None:
            return True
        if not PLAYIT_BIN:
            self.emit("playit agent not found!")
            return False
        self.tunnel_proc = subprocess.Popen(
            [PLAYIT_BIN], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=0x08000000 if IS_WIN else 0)
        threading.Thread(target=self.watch_playit, daemon=True).start()
        return True

    def watch_playit(self):
        """Parse agent output: claim link on first run, errors afterwards."""
        try:
            for raw in self.tunnel_proc.stdout:
                line = ANSI_RE.sub("", raw.decode("utf-8", "replace")).strip()
                if not line:
                    continue
                self.q.put(("out", line + "\n"))
                m = CLAIM_RE.search(line)
                if m and not self._claim_shown:
                    self._claim_shown = True
                    url = m.group(0)
                    self.q.put(("event",
                                "Open this link to sign in to playit: " + url + "\n"))
                    self.after(0, lambda u=url: webbrowser.open(u))
        except (OSError, ValueError, AttributeError):
            pass

    def refresh_tunnel(self):
        on = tunnel_active()
        self.tunnel.config(text=f"tunnel: {'on' if on else 'off'}",
                           foreground=OK if on else BAD)

    def toggle_tunnel(self):
        if IS_WIN or not tunnel_service_exists():
            if tunnel_active():
                if IS_WIN:
                    subprocess.run(["taskkill", "/F", "/T", "/IM", "playit.exe"],
                                   capture_output=True, creationflags=0x08000000)
                else:
                    subprocess.run(["pkill", "-9", "-x", "playit"], capture_output=True)
                self.tunnel_proc = None
                self.emit(f"Tunnel turned off at {time.strftime('%H:%M:%S')}")
            else:
                self.emit("Starting playit agent...")
                self.start_playit()
        else:
            on = tunnel_active()
            result = tunnel_toggle(not on)
            if result == on:
                self.emit(f"Failed to turn the tunnel {'off' if on else 'on'}!")
            else:
                self.emit(f"Tunnel turned {'on' if result else 'off'} "
                          f"at {time.strftime('%H:%M:%S')}")
        self.refresh_tunnel()

    # ---- actions -------------------------------------------------------
    def start(self):
        if not ROOM_BIN:
            messagebox.showerror(
                "Eden Room Host",
                "The eden-room server binary was not found.\n"
                "Re-download this app or get it from eden's releases.")
            return
        if not self.games or self.game_box.get() not in [g[0] for g in self.games]:
            messagebox.showerror(
                "Eden Room Host",
                "No game selected or no games found.\n"
                "Add game folders in Eden itself first.")
            return
        if room_running():
            self.emit("A room is already running, stopping it first...")
            stop_room()
        self.start_btn.config(state="disabled")
        game_path = dict(self.games).get(self.game_box.get(), "")
        m = re.search(r"\[([0-9A-Fa-f]{16})\]", game_path)
        game_id = m.group(1).upper() if m else None
        self.emit(f"Starting room '{self.room_name.get().strip() or 'Room'}' "
                  f"with game '{self.game_box.get()}'...")
        try:
            self.process, stream = start_room(
                self.room_name.get().strip() or "Room",
                self.room_password.get(),
                int(self.players.get()),
                self.game_box.get(),
                game_id,
            )
        except Exception as e:
            self.emit(f"Failed to start room: {e}")
            messagebox.showerror("Eden Room Host", f"Could not start room:\n{e}")
            self.start_btn.config(state="normal")
            return
        self.stream = stream
        try:
            logfile = open(OUT_LOG, "w")
        except OSError:
            logfile = None
        self.start_pump(stream, logfile)
        self.emit(f"Room server started, listening on 127.0.0.1:{PORT} (UDP)")
        if not tunnel_active():
            self.emit("Tunnel was off, starting playit...")
            if self.start_playit():
                self.emit("Tunnel starting, friends can reach the room "
                          "via your playit.gg address")
            else:
                self.emit("Could not start the playit tunnel!")
        if self.log_text is not None:
            self.log_text.delete("1.0", "end")
        self.start_btn.config(state="normal")
        self.refresh_status()

    def stop(self):
        self.emit("Stop pressed, killing room server...")
        stop_room()
        t0 = time.time()
        while room_running() and time.time() - t0 < 2:
            time.sleep(0.05)
        if self.process is not None:
            try:
                self.process.wait(timeout=1)
            except Exception:
                pass
            self.process = None
        if room_running():
            self.emit("FAILED to stop the room server!")
        else:
            self.emit(f"Room stopped at {time.strftime('%H:%M:%S')}")
        self.refresh_status()

    # ---- status --------------------------------------------------------
    def refresh_status(self, *_):
        if self.process is not None and self.process.poll() is not None:
            code = self.process.returncode
            self.emit(f"Room server exited unexpectedly (exit code {code})")
            self.process = None
        alive = (self.process is not None and self.process.poll() is None) \
            or room_running()
        if alive:
            self.status.config(text=f"Room running on 127.0.0.1:{PORT}",
                               foreground=OK)
        else:
            self.status.config(text="Room stopped", foreground=BAD)


def main():
    os.makedirs(LOG_DIR, exist_ok=True)
    try:
        app = App()
    except Exception as e:
        try:
            r = tk.Tk(); r.withdraw()
            messagebox.showerror("Eden Room Host", f"Failed to start:\n{e}")
        except Exception:
            print(f"Eden Room Host failed to start: {e}", file=sys.stderr)
        sys.exit(1)
    if getattr(sys, "frozen", False):
        try:
            import pyi_splash  # only present when built with --splash
            app.update()
            pyi_splash.close()
        except Exception:
            pass
    app.mainloop()


if __name__ == "__main__":
    main()
