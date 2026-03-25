"""
LM Studio Monitor — Passive Floating Desktop Widget
Monitors LM Studio in real time. Shows per-generation stats and TPS history.

Data sources:
  GET /api/v0/models     → model metadata (arch, quant, format, ctx)
  lms ps --json          → generation status + queue depth
  lms log stream --stats → post-generation stats (TPS, TTFT, tokens, time)

Requirements: pip install requests
Usage:        python lmstudio-monitor.py [url] [--debug]
"""

import ctypes
import tkinter as tk
from tkinter import font as tkfont
import threading
import subprocess
import time
import json
import shutil
import sys
import os
import atexit
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, Dict

try:
    import requests
except ImportError:
    print("Missing dependency. Run:  pip install requests")
    sys.exit(1)

# ── DPI Awareness (must run before any tkinter) ──────────────────────────────

DPI_SCALE = 1.0

if sys.platform == "win32":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
    try:
        hdc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
        ctypes.windll.user32.ReleaseDC(0, hdc)
        DPI_SCALE = dpi / 96.0
    except Exception:
        pass


def S(v):
    """Scale a pixel value by DPI factor."""
    return int(v * DPI_SCALE)


# ── Config ────────────────────────────────────────────────────────────────────

BASE_URL = "http://localhost:1234"
POLL_INTERVAL_S = 2.0
PS_INTERVAL_S = 1.0
MAX_HISTORY = 50
W_W = S(340)
W_H_FULL = S(560)
W_H_MINI = S(158)
CORNER_R = S(16)

DATA_DIR = os.path.expanduser("~/.lmstudio-monitor")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")
DEBUG = "--debug" in sys.argv


def dbg(msg):
    if DEBUG:
        print(f"[DBG] {msg}", flush=True)


# ── Theme ─────────────────────────────────────────────────────────────────────

THEMES = {
    "light": {
        "TKEY": "#f0f1f0", "BG": "#f5f5f9", "SURFACE": "#ffffff",
        "BORDER": "#d6d6de", "TEXT": "#1a1a2e", "TEXT_DIM": "#8888a0",
        "ACCENT": "#6c5ce7", "GREEN": "#16a34a", "GREEN_BG": "#dcfce7",
        "RED": "#dc2626", "RED_BG": "#fee2e2", "AMBER": "#d97706",
        "AMBER_BG": "#fef3c7", "CYAN": "#0e7490", "SPARK_FILL": "#cffafe",
        "SPARK_LINE": "#0e7490", "SPARK_DOT": "#0e7490"
    },
    "dark": {
        "TKEY": "#1e1e1e", "BG": "#2b2b2b", "SURFACE": "#3c3f41",
        "BORDER": "#555555", "TEXT": "#d4d4d4", "TEXT_DIM": "#999999",
        "ACCENT": "#cc7832", "GREEN": "#499c54", "GREEN_BG": "#2d3a2a",
        "RED": "#e06c6c", "RED_BG": "#4a2323", "AMBER": "#e8bf6a",
        "AMBER_BG": "#3d3220", "CYAN": "#7ab0d4", "SPARK_FILL": "#2e4057",
        "SPARK_LINE": "#7ab0d4", "SPARK_DOT": "#7ab0d4"
    }
}

class ThemeManager:
    def __init__(self):
        self.current_theme = "light"
        self.themes = THEMES

    def set_theme(self, theme_name):
        """Switch to a different theme"""
        if theme_name in self.themes:
            self.current_theme = theme_name
            return True
        return False

    def get_colors(self):
        """Get current color scheme"""
        return self.themes[self.current_theme]

    def cycle_theme(self):
        """Cycle through available themes"""
        themes_list = list(self.themes.keys())
        idx = (themes_list.index(self.current_theme) + 1) % len(themes_list)
        self.current_theme = themes_list[idx]

class C:
    TKEY       = "#f0f1f0"
    BG         = "#f5f5f9"
    SURFACE    = "#ffffff"
    BORDER     = "#d6d6de"
    TEXT       = "#1a1a2e"
    TEXT_DIM   = "#8888a0"
    ACCENT     = "#6c5ce7"
    GREEN      = "#16a34a"
    GREEN_BG   = "#dcfce7"
    RED        = "#dc2626"
    RED_BG     = "#fee2e2"
    AMBER      = "#d97706"
    AMBER_BG   = "#fef3c7"
    CYAN       = "#0e7490"
    SPARK_FILL = "#cffafe"
    SPARK_LINE = "#0e7490"
    SPARK_DOT  = "#0e7490"


# ── Data ──────────────────────────────────────────────────────────────────────

@dataclass
class GenRecord:
    timestamp: float = 0.0
    tps: float = 0.0
    ttft_sec: float = 0.0
    total_sec: float = 0.0
    prompt_tokens: int = 0
    predicted_tokens: int = 0
    total_tokens: int = 0
    stop_reason: str = ""

    def to_dict(self):
        return vars(self)

    @staticmethod
    def from_dict(d: dict) -> "GenRecord":
        r = GenRecord()
        for k in vars(r):
            if k in d:
                setattr(r, k, type(getattr(r, k))(d[k]))
        return r


@dataclass
class ModelInfo:
    id: str = ""
    name: str = ""
    arch: str = ""
    quant: str = ""
    fmt: str = ""
    ctx: int = 0


@dataclass
class AppState:
    connected: bool = False
    connected_since: Optional[float] = None
    server_status: str = "offline"
    primary_model: Optional[ModelInfo] = None
    ps_available: bool = False
    generation_status: str = ""
    queued_requests: int = 0
    model_history: Dict[str, deque] = field(default_factory=dict)
    # Per-model totals: model_id -> {"gens": int, "tokens": int}
    model_totals: Dict[str, Dict] = field(default_factory=dict)
    log_stream_active: bool = False
    api_latency_ms: float = 0.0
    dirty: bool = False
    theme_manager: ThemeManager = field(default_factory=ThemeManager)

    def get_model_id(self) -> str:
        """Get the current model's identifier for history lookup."""
        return self.primary_model.id if self.primary_model else ""

    def last_gen_for(self, mid: str) -> Optional[GenRecord]:
        """Get the most recent generation for a specific model."""
        hist = self.model_history.get(mid)
        return hist[-1] if hist else None

    def totals_for(self, mid: str) -> tuple:
        """Return (gens, tokens) for a model."""
        t = self.model_totals.get(mid, {})
        return t.get("gens", 0), t.get("tokens", 0)

    def tps_data_for(self, mid: str) -> list:
        """Return list of TPS values for a model's history."""
        return [r.tps for r in self.model_history.get(mid, []) if r.tps > 0]

    @property
    def all_gens(self) -> int:
        return sum(t.get("gens", 0) for t in self.model_totals.values())

    @property
    def all_tokens(self) -> int:
        return sum(t.get("tokens", 0) for t in self.model_totals.values())


state = AppState()
_lms_path: Optional[str] = None


# ── Persistence ───────────────────────────────────────────────────────────────

def save_history():
    if not state.model_history and not state.model_totals:
        return
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        out = {}
        for mid, recs in state.model_history.items():
            out[mid] = {"records": [r.to_dict() for r in recs], "totals": state.model_totals.get(mid, {"gens": 0, "tokens": 0})}
        out["__meta__"] = {"version": 2, "saved_at": time.time()}
        tmp = HISTORY_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f)
        os.replace(tmp, HISTORY_FILE) if os.path.exists(HISTORY_FILE) else os.rename(tmp, HISTORY_FILE)
        state.dirty = False
    except Exception as e:
        dbg(f"Save failed: {e}")


def load_history():
    try:
        if not os.path.exists(HISTORY_FILE):
            return
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return
        meta = data.pop("__meta__", {})
        version = meta.get("version", 1)

        if version >= 2:
            # v2: per-model records + totals
            for mid, mdata in data.items():
                if not isinstance(mdata, dict):
                    continue
                recs = mdata.get("records", [])
                totals = mdata.get("totals", {"gens": 0, "tokens": 0})
                dq = deque(maxlen=MAX_HISTORY)
                for rd in recs:
                    if isinstance(rd, dict):
                        dq.append(GenRecord.from_dict(rd))
                if dq:
                    state.model_history[mid] = dq
                state.model_totals[mid] = {"gens": int(totals.get("gens", 0)), "tokens": int(totals.get("tokens", 0))}
        else:
            # v1 legacy: flat list per model, global totals
            global_gens = int(meta.get("total_gens", 0))
            for mid, recs in data.items():
                if not isinstance(recs, list):
                    continue
                dq = deque(maxlen=MAX_HISTORY)
                model_tokens = 0
                for rd in recs:
                    if isinstance(rd, dict):
                        rec = GenRecord.from_dict(rd)
                        dq.append(rec)
                        model_tokens += rec.total_tokens
                if dq:
                    state.model_history[mid] = dq
                    state.model_totals[mid] = {"gens": len(dq), "tokens": model_tokens}

        dbg(f"Loaded: {len(state.model_history)} models, {state.all_gens} total gens")
    except Exception as e:
        dbg(f"Load failed: {e}")


def save_settings():
    """Save user preferences including theme"""
    settings = {
        "theme": state.theme_manager.current_theme,
        "version": 1
    }
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(os.path.join(DATA_DIR, "settings.json"), "w", encoding="utf-8") as f:
            json.dump(settings, f)
    except Exception as e:
        dbg(f"Settings save failed: {e}")


def load_settings():
    """Load user preferences including theme"""
    settings_file = os.path.join(DATA_DIR, "settings.json")
    if not os.path.exists(settings_file):
        return

    try:
        with open(settings_file, "r", encoding="utf-8") as f:
            settings = json.load(f)
        if isinstance(settings, dict) and "theme" in settings:
            theme_name = settings["theme"]
            if state.theme_manager.set_theme(theme_name):
                dbg(f"Loaded theme: {theme_name}")
    except Exception as e:
        dbg(f"Settings load failed: {e}")


def periodic_save():
    while True:
        time.sleep(30)
        if state.dirty:
            save_history()


# ── Find lms ──────────────────────────────────────────────────────────────────

def find_lms():
    global _lms_path
    p = shutil.which("lms")
    if p:
        _lms_path = p
        return
    candidates = []
    if sys.platform == "win32":
        for base in [os.environ.get("LOCALAPPDATA", "")]:
            if base:
                candidates += [os.path.join(base, "LM Studio", "lms.exe"),
                               os.path.join(base, "Programs", "LM Studio", "lms.exe")]
        ad = os.environ.get("APPDATA", "")
        if ad:
            candidates.append(os.path.join(ad, "npm", "lms.cmd"))
        candidates.append(os.path.join(os.path.expanduser("~"), ".lmstudio", "bin", "lms.exe"))
    else:
        candidates += [os.path.expanduser("~/.lmstudio/bin/lms"), "/usr/local/bin/lms",
                       os.path.expanduser("~/.local/bin/lms")]
    for c in candidates:
        if os.path.isfile(c):
            _lms_path = c
            return


# ── Source 1: /api/v0/models ──────────────────────────────────────────────────

def poll_models():
    for ep in ["/api/v0/models", "/v1/models"]:
        try:
            t0 = time.perf_counter()
            r = requests.get(f"{BASE_URL}{ep}", timeout=4)
            state.api_latency_ms = round((time.perf_counter() - t0) * 1000, 1)
            r.raise_for_status()
            data = r.json()
            break
        except Exception:
            data = None
    if data is None:
        state.connected, state.connected_since, state.primary_model = False, None, None
        if not state.ps_available:
            state.server_status = "offline"
        state.api_latency_ms = 0
        return
    if not state.connected:
        state.connected, state.connected_since = True, time.time()
    for m in data.get("data", []):
        if m.get("state", "loaded") not in ("loaded", ""):
            continue
        mi = ModelInfo(id=m.get("id", ""), arch=m.get("arch", ""), quant=m.get("quantization", ""),
                       fmt=m.get("format", ""), ctx=int(m.get("max_context_length", 0) or m.get("context_length", 0) or 0))
        mi.name = mi.id.replace("\\", "/").split("/")[-1] or mi.id
        state.primary_model = mi
        break


def poll_models_loop():
    while True:
        poll_models()
        time.sleep(POLL_INTERVAL_S)


# ── Source 2: lms ps --json ───────────────────────────────────────────────────

def poll_ps():
    if not _lms_path:
        return
    try:
        kw = dict(capture_output=True, text=True, timeout=5)
        if sys.platform == "win32":
            kw["creationflags"] = subprocess.CREATE_NO_WINDOW
        res = subprocess.run([_lms_path, "ps", "--json"], **kw)
        if res.returncode != 0:
            state.ps_available = False
            return
        raw = res.stdout.strip()
        ji = raw.find("[")
        if ji == -1:
            ji = raw.find("{")
        if ji == -1:
            state.ps_available = False
            return
        data = json.loads(raw[ji:])
        state.ps_available = True
        models = data if isinstance(data, list) else [data]
        if models:
            m = models[0]
            gen = next((str(m[k]).lower() for k in ["generation_status", "generationStatus", "status"] if k in m), "")
            queue = next((int(m[k]) for k in ["queued_requests", "queuedRequests", "queued_prediction_requests"] if k in m), 0)
            state.generation_status, state.queued_requests = gen, queue
            state.server_status = "generating" if gen in ("generating", "predicting", "running") else ("idle" if state.connected else "offline")
    except Exception:
        state.ps_available = False


def poll_ps_loop():
    while True:
        poll_ps()
        time.sleep(PS_INTERVAL_S)


# ── Source 3: lms log stream ──────────────────────────────────────────────────

def log_stream_thread():
    if not _lms_path:
        return
    flag_combos = [
        ["log", "stream", "--source", "model", "--stats"],
        ["log", "stream", "--stats"],
        ["log", "stream", "--source", "model"],
        ["log", "stream"],
    ]
    while True:
        for flags in flag_combos:
            try:
                kw = dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
                if sys.platform == "win32":
                    kw["creationflags"] = subprocess.CREATE_NO_WINDOW
                proc = subprocess.Popen([_lms_path] + flags, **kw)
                state.log_stream_active = True
                block, model = {}, ""
                buf = b""
                while True:
                    byte = proc.stdout.read(1)
                    if not byte:
                        break
                    if byte == b"\n":
                        line = buf.decode("utf-8", errors="replace").strip()
                        buf = b""
                        if not line:
                            if block:
                                process_block(block, model)
                                block, model = {}, ""
                            continue
                        dbg(f"LOG: {line[:200]}")
                        ci = line.find(": ")
                        if ci > 0:
                            k, v = line[:ci].strip(), line[ci+2:].strip()
                            block[k] = v
                            if k == "modelIdentifier":
                                model = v
                    else:
                        buf += byte
                proc.wait()
                state.log_stream_active = False
                break
            except (FileNotFoundError, OSError):
                continue
        time.sleep(5)


def process_block(block, model_id):
    tps = _pf(block.get("tokensPerSecond", block.get("tokens_per_second", "")))
    if tps <= 0:
        return
    rec = GenRecord(
        timestamp=time.time(), tps=round(tps, 2),
        ttft_sec=_pf(block.get("timeToFirstTokenSec", block.get("time_to_first_token", "0"))),
        total_sec=_pf(block.get("totalTimeSec", block.get("generation_time", "0"))),
        prompt_tokens=_pi(block.get("promptTokensCount", block.get("prompt_tokens", "0"))),
        predicted_tokens=_pi(block.get("predictedTokensCount", block.get("completion_tokens", "0"))),
        total_tokens=_pi(block.get("totalTokensCount", block.get("total_tokens", "0"))),
        stop_reason=block.get("stopReason", block.get("stop_reason", "")),
    )
    if model_id not in state.model_history:
        state.model_history[model_id] = deque(maxlen=MAX_HISTORY)
    state.model_history[model_id].append(rec)
    # Per-model totals
    if model_id not in state.model_totals:
        state.model_totals[model_id] = {"gens": 0, "tokens": 0}
    state.model_totals[model_id]["gens"] += 1
    state.model_totals[model_id]["tokens"] += rec.total_tokens
    state.dirty = True


def _pf(s):
    try: return float(s)
    except: return 0.0

def _pi(s):
    try: return int(float(s))
    except: return 0

def fmt_dur(sec):
    s = int(sec)
    if s < 60: return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60: return f"{m}m {s}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m"


# ── HTTP Server ───────────────────────────────────────────────────────────────

import http.server
import socketserver

class StateHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/state':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            s = state
            m = s.primary_model
            mid = s.get_model_id()
            g = s.last_gen_for(mid)
            td = s.tps_data_for(mid)
            mg, mt = s.totals_for(mid)
            
            response = {
                "connected": s.connected,
                "connected_since": s.connected_since,
                "server_status": s.server_status,
                "generation_status": s.generation_status,
                "queued_requests": s.queued_requests,
                "api_latency_ms": s.api_latency_ms,
                "log_stream_active": s.log_stream_active,
                "model": vars(m) if m else None,
                "last_gen": vars(g) if g else None,
                "history_tps": td,
                "model_totals": {"gens": mg, "tokens": mt}
            }
            self.wfile.write(json.dumps(response).encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass

def run_server():
    port = 12344
    with socketserver.TCPServer(("127.0.0.1", port), StateHandler) as httpd:
        if DEBUG:
            print(f"Serving HTTP on port {port}")
        httpd.serve_forever()

# ── Entry ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if args: BASE_URL = args[0].rstrip("/")

    find_lms()
    load_history()
    atexit.register(save_history)
    
    if DEBUG:
        print(f"URL: {BASE_URL}\nlms: {_lms_path or 'NOT FOUND'}\nHistory: {len(state.model_history)} models, {state.all_gens} total gens")
    
    for fn in [poll_models_loop, poll_ps_loop, log_stream_thread, periodic_save]:
        threading.Thread(target=fn, daemon=True).start()
        
    run_server()
