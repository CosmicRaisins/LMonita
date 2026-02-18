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

# ── Config ────────────────────────────────────────────────────────────────────

BASE_URL = "http://localhost:1234"
POLL_INTERVAL_S = 2.0
PS_INTERVAL_S = 1.0
MAX_HISTORY = 50
WINDOW_WIDTH = 340
WINDOW_HEIGHT_FULL = 560
WINDOW_HEIGHT_MINI = 158
CORNER_RADIUS = 16

DATA_DIR = os.path.expanduser("~/.lmstudio-monitor")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")

DEBUG = "--debug" in sys.argv


def dbg(msg):
    if DEBUG:
        print(f"[DBG] {msg}", flush=True)


# ── Theme ─────────────────────────────────────────────────────────────────────

class C:
    TKEY       = "#f0f1f0"
    BG         = "#f5f5f9"
    SURFACE    = "#ffffff"
    SURFACE2   = "#eaeaef"
    BORDER     = "#d6d6de"
    TEXT       = "#1a1a2e"
    TEXT_SEC   = "#555570"
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

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp, "tps": self.tps,
            "ttft_sec": self.ttft_sec, "total_sec": self.total_sec,
            "prompt_tokens": self.prompt_tokens,
            "predicted_tokens": self.predicted_tokens,
            "total_tokens": self.total_tokens,
            "stop_reason": self.stop_reason,
        }

    @staticmethod
    def from_dict(d: dict) -> "GenRecord":
        r = GenRecord()
        r.timestamp = float(d.get("timestamp", 0))
        r.tps = float(d.get("tps", 0))
        r.ttft_sec = float(d.get("ttft_sec", 0))
        r.total_sec = float(d.get("total_sec", 0))
        r.prompt_tokens = int(d.get("prompt_tokens", 0))
        r.predicted_tokens = int(d.get("predicted_tokens", 0))
        r.total_tokens = int(d.get("total_tokens", 0))
        r.stop_reason = str(d.get("stop_reason", ""))
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
    last_gen: Optional[GenRecord] = None
    last_gen_model: str = ""
    total_gens: int = 0
    total_tokens: int = 0

    log_stream_active: bool = False
    api_latency_ms: float = 0.0
    dirty: bool = False


state = AppState()
_lms_path: Optional[str] = None


# ── Persistence ───────────────────────────────────────────────────────────────

def save_history():
    if not state.model_history:
        return
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        out = {}
        for mid, records in state.model_history.items():
            out[mid] = [r.to_dict() for r in records]
        out["__meta__"] = {
            "total_gens": state.total_gens,
            "total_tokens": state.total_tokens,
            "saved_at": time.time(),
        }
        tmp = HISTORY_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f)
        if os.path.exists(HISTORY_FILE):
            os.replace(tmp, HISTORY_FILE)
        else:
            os.rename(tmp, HISTORY_FILE)
        state.dirty = False
        dbg(f"Saved history: {sum(len(v) for v in state.model_history.values())} records")
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
        state.total_gens = int(meta.get("total_gens", 0))
        state.total_tokens = int(meta.get("total_tokens", 0))
        for mid, records in data.items():
            if not isinstance(records, list):
                continue
            dq = deque(maxlen=MAX_HISTORY)
            for rd in records:
                if isinstance(rd, dict):
                    dq.append(GenRecord.from_dict(rd))
            if dq:
                state.model_history[mid] = dq
        latest = None
        latest_mid = ""
        for mid, dq in state.model_history.items():
            if dq and (latest is None or dq[-1].timestamp > latest.timestamp):
                latest = dq[-1]
                latest_mid = mid
        if latest:
            state.last_gen = latest
            state.last_gen_model = latest_mid
        dbg(f"Loaded: {len(state.model_history)} models, {state.total_gens} gens")
    except Exception as e:
        dbg(f"Load failed: {e}")


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
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            candidates.append(os.path.join(local, "LM Studio", "lms.exe"))
            candidates.append(os.path.join(local, "Programs", "LM Studio", "lms.exe"))
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            candidates.append(os.path.join(appdata, "npm", "lms.cmd"))
        candidates.append(os.path.join(os.path.expanduser("~"), ".lmstudio", "bin", "lms.exe"))
    else:
        candidates += [
            os.path.expanduser("~/.lmstudio/bin/lms"),
            "/usr/local/bin/lms",
            os.path.expanduser("~/.local/bin/lms"),
        ]
    for c in candidates:
        if os.path.isfile(c):
            _lms_path = c
            return


# ── Source 1: /api/v0/models ──────────────────────────────────────────────────

def poll_models():
    for endpoint in ["/api/v0/models", "/v1/models"]:
        try:
            t0 = time.perf_counter()
            r = requests.get(f"{BASE_URL}{endpoint}", timeout=4)
            state.api_latency_ms = round((time.perf_counter() - t0) * 1000, 1)
            r.raise_for_status()
            data = r.json()
            break
        except Exception:
            data = None
    if data is None:
        state.connected = False
        state.connected_since = None
        state.primary_model = None
        if not state.ps_available:
            state.server_status = "offline"
        state.api_latency_ms = 0
        return
    if not state.connected:
        state.connected = True
        state.connected_since = time.time()
    for m in data.get("data", []):
        s = m.get("state", "loaded")
        if s not in ("loaded", ""):
            continue
        mi = ModelInfo()
        mi.id = m.get("id", "")
        mi.arch = m.get("arch", "")
        mi.quant = m.get("quantization", "")
        mi.fmt = m.get("format", "")
        mi.ctx = int(m.get("max_context_length", 0) or m.get("context_length", 0) or 0)
        parts = mi.id.replace("\\", "/").split("/")
        mi.name = parts[-1] if parts else mi.id
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
        result = subprocess.run([_lms_path, "ps", "--json"], **kw)
        if result.returncode != 0:
            state.ps_available = False
            return
        raw = result.stdout.strip()
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
            gen = ""
            for k in ["generation_status", "generationStatus", "status"]:
                if k in m:
                    gen = str(m[k]).lower()
                    break
            queue = 0
            for k in ["queued_requests", "queuedRequests", "queued_prediction_requests"]:
                if k in m:
                    queue = int(m[k])
                    break
            state.generation_status = gen
            state.queued_requests = queue
            state.server_status = (
                "generating" if gen in ("generating", "predicting", "running")
                else "idle" if state.connected else "offline"
            )
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
            dbg(f"Trying: lms {' '.join(flags)}")
            try:
                kw = dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
                if sys.platform == "win32":
                    kw["creationflags"] = subprocess.CREATE_NO_WINDOW
                proc = subprocess.Popen([_lms_path] + flags, **kw)
                state.log_stream_active = True
                current_block: Dict[str, str] = {}
                current_model = ""
                buf = b""
                while True:
                    byte = proc.stdout.read(1)
                    if not byte:
                        break
                    if byte == b"\n":
                        try:
                            line = buf.decode("utf-8", errors="replace").strip()
                        except Exception:
                            line = ""
                        buf = b""
                        if not line:
                            if current_block:
                                process_block(current_block, current_model)
                                current_block = {}
                                current_model = ""
                            continue
                        dbg(f"LOG: {line[:200]}")
                        colon = line.find(": ")
                        if colon > 0:
                            key = line[:colon].strip()
                            val = line[colon + 2:].strip()
                            current_block[key] = val
                            if key == "modelIdentifier":
                                current_model = val
                    else:
                        buf += byte
                proc.wait()
                state.log_stream_active = False
                break
            except (FileNotFoundError, OSError) as e:
                dbg(f"Failed: {e}")
                continue
        time.sleep(5)


def process_block(block: Dict[str, str], model_id: str):
    tps = _pf(block.get("tokensPerSecond", block.get("tokens_per_second", "")))
    if tps <= 0:
        return
    rec = GenRecord()
    rec.timestamp = time.time()
    rec.tps = round(tps, 2)
    rec.ttft_sec = _pf(block.get("timeToFirstTokenSec", block.get("time_to_first_token", "0")))
    rec.total_sec = _pf(block.get("totalTimeSec", block.get("generation_time", "0")))
    rec.prompt_tokens = _pi(block.get("promptTokensCount", block.get("prompt_tokens", "0")))
    rec.predicted_tokens = _pi(block.get("predictedTokensCount", block.get("completion_tokens", "0")))
    rec.total_tokens = _pi(block.get("totalTokensCount", block.get("total_tokens", "0")))
    rec.stop_reason = block.get("stopReason", block.get("stop_reason", ""))
    if model_id not in state.model_history:
        state.model_history[model_id] = deque(maxlen=MAX_HISTORY)
    state.model_history[model_id].append(rec)
    state.last_gen = rec
    state.last_gen_model = model_id
    state.total_gens += 1
    state.total_tokens += rec.total_tokens
    state.dirty = True
    dbg(f"GEN: {model_id} → {rec.tps} t/s, {rec.predicted_tokens} tok, {rec.total_sec}s")


def _pf(s) -> float:
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def _pi(s) -> int:
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return 0


# ── Helpers ───────────────────────────────────────────────────────────────────

def fmt_duration(seconds: float) -> str:
    """Format seconds into a compact human string."""
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m"


def get_tps_data() -> list:
    """Get TPS data for the current/last model."""
    m = state.primary_model
    hist_model = state.last_gen_model or (m.id if m else "")
    hist = list(state.model_history.get(hist_model, []))
    return [r.tps for r in hist if r.tps > 0]


# ── GUI ───────────────────────────────────────────────────────────────────────

class MonitorWidget(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("LM Studio Monitor")
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=C.TKEY)

        if sys.platform == "win32":
            try:
                self.attributes("-transparentcolor", C.TKEY)
            except tk.TclError:
                pass

        self._collapsed = False
        self._cur_h = WINDOW_HEIGHT_FULL
        self.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT_FULL}")
        sx = self.winfo_screenwidth()
        sy = self.winfo_screenheight()
        self.geometry(f"+{sx - WINDOW_WIDTH - 30}+{sy - WINDOW_HEIGHT_FULL - 70}")

        self._dx = 0
        self._dy = 0
        self._pinned = True

        mono = "Consolas" if sys.platform == "win32" else "Menlo"
        sans = "Segoe UI" if sys.platform == "win32" else "Helvetica Neue"
        self.F = {
            "mono_sm":  tkfont.Font(family=mono, size=8),
            "mono_md":  tkfont.Font(family=mono, size=11, weight="bold"),
            "mono_lg":  tkfont.Font(family=mono, size=26, weight="bold"),
            "mono_val": tkfont.Font(family=mono, size=10),
            "title":    tkfont.Font(family=mono, size=9, weight="bold"),
            "sans_sm":  tkfont.Font(family=sans, size=8),
            "model":    tkfont.Font(family=sans, size=11, weight="bold"),
            "section":  tkfont.Font(family=mono, size=8, weight="bold"),
            # Compact view fonts
            "c_tps":    tkfont.Font(family=mono, size=16, weight="bold"),
            "c_label":  tkfont.Font(family=mono, size=7),
            "c_val":    tkfont.Font(family=mono, size=9, weight="bold"),
            "c_model":  tkfont.Font(family=sans, size=9, weight="bold"),
        }

        self._build()
        self._start_threads()
        self._tick()

    # ── Rounded background ──

    def _set_height(self, h):
        self._cur_h = h
        self.geometry(f"{WINDOW_WIDTH}x{h}")
        self._content.place_configure(height=h - 12)
        self.bg_canvas.config(height=h)
        self._draw_bg()

    def _draw_bg(self):
        c = self.bg_canvas
        c.delete("bg")
        w, h, r = WINDOW_WIDTH, self._cur_h, CORNER_RADIUS
        self._rrect(c, 3, 3, w-1, h-1, r, fill="#d0d0d8", outline="", tags="bg")
        self._rrect(c, 0, 0, w-4, h-4, r, fill=C.BG, outline=C.BORDER, tags="bg")

    def _rrect(self, cv, x1, y1, x2, y2, r, **kw):
        cv.create_polygon(
            x1+r,y1, x2-r,y1, x2,y1, x2,y1+r,
            x2,y2-r, x2,y2, x2-r,y2, x1+r,y2,
            x1,y2, x1,y2-r, x1,y1+r, x1,y1,
            smooth=True, **kw
        )

    # ── Build ──

    def _build(self):
        self.bg_canvas = tk.Canvas(self, width=WINDOW_WIDTH, height=WINDOW_HEIGHT_FULL,
                                   bg=C.TKEY, highlightthickness=0, bd=0)
        self.bg_canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self._draw_bg()

        self._content = tk.Frame(self, bg=C.BG)
        self._content.place(x=6, y=6, width=WINDOW_WIDTH-12, height=WINDOW_HEIGHT_FULL-12)

        self._build_titlebar(self._content)
        self._build_compact(self._content)   # Collapsed summary
        self._build_full(self._content)      # Expanded details
        self._build_footer(self._content)

        # Start in expanded mode
        self._compact_frame.pack_forget()

    # ── Titlebar (always visible) ──

    def _build_titlebar(self, r):
        tb = tk.Frame(r, bg=C.BG, height=28)
        tb.pack(fill="x", padx=8, pady=(6, 0))
        tb.pack_propagate(False)
        self._draggable(tb)

        self.dot = tk.Canvas(tb, width=10, height=10, bg=C.BG, highlightthickness=0)
        self.dot.pack(side="left", padx=(0, 6))
        self._draggable(self.dot)

        lbl = tk.Label(tb, text="LM STUDIO", font=self.F["title"], fg=C.TEXT_DIM, bg=C.BG)
        lbl.pack(side="left")
        self._draggable(lbl)

        x_btn = tk.Label(tb, text="✕", font=self.F["sans_sm"], fg=C.TEXT_DIM, bg=C.BG, cursor="hand2")
        x_btn.pack(side="right")
        x_btn.bind("<Button-1>", lambda e: self._on_close())
        x_btn.bind("<Enter>", lambda e: x_btn.config(fg=C.RED))
        x_btn.bind("<Leave>", lambda e: x_btn.config(fg=C.TEXT_DIM))

        self.pin = tk.Label(tb, text="📌", font=self.F["sans_sm"], fg=C.ACCENT, bg=C.BG, cursor="hand2")
        self.pin.pack(side="right", padx=(0, 6))
        self.pin.bind("<Button-1>", self._toggle_pin)

        self.collapse_btn = tk.Label(tb, text="▾", font=self.F["title"], fg=C.TEXT_DIM, bg=C.BG, cursor="hand2")
        self.collapse_btn.pack(side="right", padx=(0, 6))
        self.collapse_btn.bind("<Button-1>", self._toggle_collapse)

    # ── Compact view (collapsed) ──

    def _build_compact(self, r):
        self._compact_frame = tk.Frame(r, bg=C.BG)
        self._compact_frame.pack(fill="both", expand=True)

        cf = self._compact_frame

        # Row 1: dot badge + model name + uptime
        row1 = tk.Frame(cf, bg=C.BG)
        row1.pack(fill="x", padx=10, pady=(6, 0))

        self.c_badge = tk.Label(row1, text="●", font=self.F["c_label"], fg=C.GREEN, bg=C.BG)
        self.c_badge.pack(side="left", padx=(0, 4))
        self.c_model = tk.Label(row1, text="—", font=self.F["c_model"], fg=C.TEXT, bg=C.BG, anchor="w")
        self.c_model.pack(side="left", fill="x", expand=True)
        self.c_uptime = tk.Label(row1, text="", font=self.F["c_label"], fg=C.TEXT_DIM, bg=C.BG)
        self.c_uptime.pack(side="right")

        # Row 2: big TPS + stats + mini sparkline
        row2 = tk.Frame(cf, bg=C.SURFACE, highlightbackground=C.BORDER, highlightthickness=1)
        row2.pack(fill="x", padx=10, pady=(6, 0))

        # Left: TPS number
        left = tk.Frame(row2, bg=C.SURFACE)
        left.pack(side="left", padx=(10, 0), pady=6)

        self.c_tps = tk.Label(left, text="—", font=self.F["c_tps"], fg=C.CYAN, bg=C.SURFACE, anchor="w")
        self.c_tps.pack(anchor="w")

        stats_row = tk.Frame(left, bg=C.SURFACE)
        stats_row.pack(anchor="w")
        self.c_stats = tk.Label(stats_row, text="", font=self.F["c_label"], fg=C.TEXT_DIM, bg=C.SURFACE)
        self.c_stats.pack(side="left")

        # Right: mini sparkline
        self.c_spark = tk.Canvas(row2, width=100, height=36, bg=C.SURFACE, highlightthickness=0)
        self.c_spark.pack(side="right", padx=(4, 8), pady=6)

        # Row 3: session summary
        row3 = tk.Frame(cf, bg=C.BG)
        row3.pack(fill="x", padx=10, pady=(6, 0))
        self.c_session = tk.Label(row3, text="", font=self.F["c_label"], fg=C.TEXT_DIM, bg=C.BG, anchor="w")
        self.c_session.pack(fill="x")

    # ── Full view (expanded) ──

    def _build_full(self, r):
        self._full_frame = tk.Frame(r, bg=C.BG)
        self._full_frame.pack(fill="both", expand=True)
        d = self._full_frame

        # Status row
        sr = tk.Frame(d, bg=C.BG)
        sr.pack(fill="x", padx=10, pady=(6, 0))
        self.badge = tk.Label(sr, text="OFFLINE", font=self.F["mono_sm"], fg=C.RED, bg=C.RED_BG, padx=6, pady=1)
        self.badge.pack(side="left")
        self.uptime = tk.Label(sr, text="--:--:--", font=self.F["mono_sm"], fg=C.TEXT_DIM, bg=C.BG)
        self.uptime.pack(side="right")

        # Model card
        mc = tk.Frame(d, bg=C.SURFACE, highlightbackground=C.BORDER, highlightthickness=1)
        mc.pack(fill="x", padx=10, pady=(8, 0))
        tk.Frame(mc, bg=C.ACCENT, width=3).pack(side="left", fill="y")
        mi = tk.Frame(mc, bg=C.SURFACE)
        mi.pack(fill="both", expand=True, padx=8, pady=6)
        self.m_name = tk.Label(mi, text="No model loaded", font=self.F["model"], fg=C.TEXT_DIM, bg=C.SURFACE, anchor="w")
        self.m_name.pack(fill="x")
        self.m_meta = tk.Label(mi, text="", font=self.F["mono_sm"], fg=C.TEXT_DIM, bg=C.SURFACE, anchor="w")
        self.m_meta.pack(fill="x", pady=(1, 0))

        # Last Generation
        self._sec(d, "LAST GENERATION")
        lg = tk.Frame(d, bg=C.SURFACE, highlightbackground=C.BORDER, highlightthickness=1)
        lg.pack(fill="x", padx=10, pady=(4, 0))

        tps_row = tk.Frame(lg, bg=C.SURFACE)
        tps_row.pack(fill="x", padx=10, pady=(8, 0))
        self.tps_val = tk.Label(tps_row, text="—", font=self.F["mono_lg"], fg=C.CYAN, bg=C.SURFACE, anchor="w")
        self.tps_val.pack(side="left")
        tk.Label(tps_row, text="t/s", font=self.F["mono_sm"], fg=C.TEXT_DIM, bg=C.SURFACE, anchor="sw").pack(side="left", padx=(4,0), pady=(0,6))
        self.lg_model = tk.Label(tps_row, text="", font=self.F["mono_sm"], fg=C.TEXT_DIM, bg=C.SURFACE, anchor="se")
        self.lg_model.pack(side="right", pady=(0, 6))

        sg = tk.Frame(lg, bg=C.SURFACE)
        sg.pack(fill="x", padx=10, pady=(4, 8))
        sg.columnconfigure(0, weight=1)
        sg.columnconfigure(1, weight=1)
        sg.columnconfigure(2, weight=1)

        self.lg_ttft   = self._mini(sg, 0, 0, "TTFT")
        self.lg_time   = self._mini(sg, 0, 1, "TIME")
        self.lg_tokens = self._mini(sg, 0, 2, "TOKENS")
        self.lg_prompt = self._mini(sg, 1, 0, "PROMPT")
        self.lg_gen    = self._mini(sg, 1, 1, "GEN'D")
        self.lg_stop   = self._mini(sg, 1, 2, "STOP")

        # TPS History
        self._sec(d, "TPS HISTORY")
        hf = tk.Frame(d, bg=C.SURFACE, highlightbackground=C.BORDER, highlightthickness=1)
        hf.pack(fill="x", padx=10, pady=(4, 0))
        hh = tk.Frame(hf, bg=C.SURFACE)
        hh.pack(fill="x", padx=10, pady=(6, 0))
        self.hist_info = tk.Label(hh, text="No data yet", font=self.F["mono_sm"], fg=C.TEXT_DIM, bg=C.SURFACE, anchor="w")
        self.hist_info.pack(side="left")
        self.hist_stats = tk.Label(hh, text="", font=self.F["mono_sm"], fg=C.CYAN, bg=C.SURFACE, anchor="e")
        self.hist_stats.pack(side="right")
        self.spark = tk.Canvas(hf, height=55, bg=C.SURFACE, highlightthickness=0)
        self.spark.pack(fill="x", padx=10, pady=(2, 8))

        # Session
        self._sec(d, "SESSION")
        sf = tk.Frame(d, bg=C.BG)
        sf.pack(fill="x", padx=10, pady=(4, 0))
        sf.columnconfigure(0, weight=1)
        sf.columnconfigure(1, weight=1)
        sf.columnconfigure(2, weight=1)
        self.s_gens   = self._card(sf, 0, "GENS", "0", C.ACCENT)
        self.s_tokens = self._card(sf, 1, "TOKENS", "0", C.CYAN)
        self.s_queue  = self._card(sf, 2, "QUEUE", "0", C.TEXT)

    def _build_footer(self, r):
        ft = tk.Frame(r, bg=C.BG)
        ft.pack(fill="x", side="bottom", padx=10, pady=(4, 6))
        self.ft_url = tk.Label(ft, text=BASE_URL, font=self.F["mono_sm"], fg=C.TEXT_DIM, bg=C.BG)
        self.ft_url.pack(side="left")
        self.ft_src = tk.Label(ft, text="", font=self.F["mono_sm"], fg=C.TEXT_DIM, bg=C.BG)
        self.ft_src.pack(side="right")

    # ── Widget builders ──

    def _sec(self, parent, text):
        f = tk.Frame(parent, bg=C.BG)
        f.pack(fill="x", padx=10, pady=(8, 0))
        tk.Label(f, text=text, font=self.F["section"], fg=C.TEXT_DIM, bg=C.BG).pack(side="left")

    def _mini(self, parent, row, col, label):
        f = tk.Frame(parent, bg=C.SURFACE)
        f.grid(row=row, column=col, sticky="w", padx=(0, 8), pady=2)
        tk.Label(f, text=label, font=self.F["mono_sm"], fg=C.TEXT_DIM, bg=C.SURFACE).pack(anchor="w")
        v = tk.Label(f, text="—", font=self.F["mono_val"], fg=C.TEXT, bg=C.SURFACE, anchor="w")
        v.pack(anchor="w")
        return v

    def _card(self, parent, col, label, init, color):
        box = tk.Frame(parent, bg=C.SURFACE, highlightbackground=C.BORDER, highlightthickness=1)
        box.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 3, 0))
        inner = tk.Frame(box, bg=C.SURFACE)
        inner.pack(padx=8, pady=5)
        tk.Label(inner, text=label, font=self.F["mono_sm"], fg=C.TEXT_DIM, bg=C.SURFACE).pack(anchor="w")
        v = tk.Label(inner, text=init, font=self.F["mono_md"], fg=color, bg=C.SURFACE, anchor="w")
        v.pack(anchor="w")
        return v

    # ── Drag / Pin / Collapse ──

    def _draggable(self, w):
        w.bind("<Button-1>", self._ds)
        w.bind("<B1-Motion>", self._dm)

    def _ds(self, e):
        self._dx = e.x_root - self.winfo_x()
        self._dy = e.y_root - self.winfo_y()

    def _dm(self, e):
        self.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _toggle_pin(self, e=None):
        self._pinned = not self._pinned
        self.attributes("-topmost", self._pinned)
        self.pin.config(fg=C.ACCENT if self._pinned else C.TEXT_DIM)

    def _toggle_collapse(self, e=None):
        self._collapsed = not self._collapsed
        footer = self.ft_url.master

        if self._collapsed:
            self._full_frame.pack_forget()
            self._compact_frame.pack(fill="both", expand=True, before=footer)
            self.collapse_btn.config(text="▸")
            self._set_height(WINDOW_HEIGHT_MINI)
        else:
            self._compact_frame.pack_forget()
            self._full_frame.pack(fill="both", expand=True, before=footer)
            self.collapse_btn.config(text="▾")
            self._set_height(WINDOW_HEIGHT_FULL)

    def _on_close(self):
        save_history()
        self.destroy()

    def _set_dot(self, color):
        self.dot.delete("all")
        self.dot.create_oval(1, 1, 9, 9, fill=color, outline="")

    # ── Sparkline drawing ──

    def _draw_sparkline(self, canvas, data: list, show_dots=True):
        """Draw a sparkline on any canvas. No value labels."""
        c = canvas
        c.delete("all")
        c.update_idletasks()
        w, h = c.winfo_width(), c.winfo_height()
        if w < 10 or h < 10:
            return
        if not data:
            c.create_line(0, h-1, w, h-1, fill=C.BORDER)
            return
        if len(data) == 1:
            cx, cy = w/2, h/2
            c.create_oval(cx-3, cy-3, cx+3, cy+3, fill=C.SPARK_LINE, outline="")
            return

        mx = max(data) * 1.1 if max(data) > 0 else 1
        px, py = 6, 4
        uw, uh = w - 2*px, h - 2*py
        step = uw / (len(data) - 1)
        pts = [(px + i*step, h - py - (v/mx)*uh) for i, v in enumerate(data)]

        # Fill
        fp = [(pts[0][0], h-py)] + pts + [(pts[-1][0], h-py)]
        c.create_polygon([co for p in fp for co in p], fill=C.SPARK_FILL, outline="", smooth=True)
        # Line
        lf = [co for p in pts for co in p]
        if len(lf) >= 4:
            c.create_line(lf, fill=C.SPARK_LINE, width=1.5, smooth=True)
        # Dots
        if show_dots:
            for i, (x, y) in enumerate(pts):
                r = 3 if i == len(pts)-1 else 2
                c.create_oval(x-r, y-r, x+r, y+r, fill=C.SPARK_DOT, outline="")

    # ── Tick ──

    def _tick(self):
        try:
            if self._collapsed:
                self._update_compact()
            else:
                self._update_full()
            self._update_footer()
            self._update_dot()
        except Exception as e:
            dbg(f"UI error: {e}")
        self.after(300, self._tick)

    def _update_dot(self):
        if not state.connected:
            self._set_dot(C.RED)
        elif state.server_status == "generating":
            self._set_dot(C.AMBER)
        else:
            self._set_dot(C.GREEN)

    def _update_compact(self):
        s = state
        m = s.primary_model
        g = s.last_gen
        tps_data = get_tps_data()

        # Status dot
        if not s.connected:
            self.c_badge.config(fg=C.RED, text="●")
        elif s.server_status == "generating":
            self.c_badge.config(fg=C.AMBER, text="●")
        else:
            self.c_badge.config(fg=C.GREEN, text="●")

        # Model name
        if m:
            name = m.name if len(m.name) <= 24 else m.name[:22] + "…"
            self.c_model.config(text=name, fg=C.TEXT)
        else:
            self.c_model.config(text="No model", fg=C.TEXT_DIM)

        # Uptime
        if s.connected and s.connected_since:
            self.c_uptime.config(text=fmt_duration(time.time() - s.connected_since))
        else:
            self.c_uptime.config(text="")

        # TPS
        if g:
            self.c_tps.config(text=f"{g.tps:.1f}", fg=C.CYAN)
            # Compact stats line
            ttft_ms = g.ttft_sec * 1000 if g.ttft_sec < 10 else g.ttft_sec
            parts = [f"TTFT {ttft_ms:.0f}ms", f"{g.total_sec:.1f}s", f"{g.predicted_tokens} tok"]
            self.c_stats.config(text="  ·  ".join(parts))
        else:
            self.c_tps.config(text="—", fg=C.TEXT_DIM)
            self.c_stats.config(text="waiting for generation…")

        # Mini sparkline
        self._draw_sparkline(self.c_spark, tps_data, show_dots=False)

        # Session line
        parts = [f"{s.total_gens} gens"]
        if s.total_tokens:
            parts.append(f"{s.total_tokens:,} tok")
        if tps_data:
            avg = sum(tps_data) / len(tps_data)
            parts.append(f"avg {avg:.1f} t/s")
        self.c_session.config(text="  ·  ".join(parts))

    def _update_full(self):
        s = state
        m = s.primary_model
        g = s.last_gen

        # Badge
        if not s.connected:
            self.badge.config(text="OFFLINE", fg=C.RED, bg=C.RED_BG)
        elif s.server_status == "generating":
            self.badge.config(text="GENERATING", fg=C.AMBER, bg=C.AMBER_BG)
        else:
            self.badge.config(text="CONNECTED", fg=C.GREEN, bg=C.GREEN_BG)

        # Uptime
        if s.connected and s.connected_since:
            d = int(time.time() - s.connected_since)
            hr, rm = divmod(d, 3600)
            mn, sc = divmod(rm, 60)
            self.uptime.config(text=f"{hr:02}:{mn:02}:{sc:02}")
        else:
            self.uptime.config(text="--:--:--")

        # Model
        if m:
            name = m.name if len(m.name) <= 30 else m.name[:28] + "…"
            self.m_name.config(text=name, fg=C.TEXT)
            parts = [p for p in [m.arch, m.quant, m.fmt.upper() if m.fmt else "",
                                 f"{m.ctx:,} ctx" if m.ctx else ""] if p]
            self.m_meta.config(text="  ·  ".join(parts))
        else:
            self.m_name.config(text="No model loaded", fg=C.TEXT_DIM)
            self.m_meta.config(text="")

        # Last generation
        if g:
            self.tps_val.config(text=f"{g.tps:.1f}", fg=C.CYAN)
            ttft_ms = g.ttft_sec * 1000 if g.ttft_sec < 10 else g.ttft_sec
            self.lg_ttft.config(text=f"{ttft_ms:.0f}ms")
            self.lg_time.config(text=f"{g.total_sec:.1f}s")
            self.lg_tokens.config(text=f"{g.total_tokens:,}")
            self.lg_prompt.config(text=f"{g.prompt_tokens:,}")
            self.lg_gen.config(text=f"{g.predicted_tokens:,}")
            self.lg_stop.config(text=g.stop_reason or "—")
            if s.last_gen_model:
                short = s.last_gen_model.split("/")[-1]
                if len(short) > 22:
                    short = short[:20] + "…"
                self.lg_model.config(text=short)
        else:
            self.tps_val.config(text="—", fg=C.TEXT_DIM)
            for lbl in [self.lg_ttft, self.lg_time, self.lg_tokens,
                        self.lg_prompt, self.lg_gen, self.lg_stop]:
                lbl.config(text="—")
            self.lg_model.config(text="")

        # TPS History
        tps_data = get_tps_data()
        if tps_data:
            avg = sum(tps_data) / len(tps_data)
            peak = max(tps_data)
            self.hist_info.config(text=f"{len(tps_data)} generations")
            self.hist_stats.config(text=f"avg {avg:.1f}  peak {peak:.1f}")
        else:
            self.hist_info.config(text="No data yet")
            self.hist_stats.config(text="")
        self._draw_sparkline(self.spark, tps_data, show_dots=True)

        # Session
        self.s_gens.config(text=str(s.total_gens))
        self.s_tokens.config(text=f"{s.total_tokens:,}")
        self.s_queue.config(text=str(s.queued_requests),
                            fg=C.AMBER if s.queued_requests > 0 else C.TEXT)

    def _update_footer(self):
        parts = []
        if state.api_latency_ms > 0:
            parts.append(f"{state.api_latency_ms:.0f}ms")
        if state.log_stream_active:
            parts.append("stream ✓")
        elif _lms_path:
            parts.append("stream …")
        else:
            parts.append("no lms")
        self.ft_src.config(text="  ".join(parts))

    # ── Threads ──

    def _start_threads(self):
        for fn in [poll_models_loop, poll_ps_loop, log_stream_thread, periodic_save]:
            threading.Thread(target=fn, daemon=True).start()


# ── Entry ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if args:
        BASE_URL = args[0].rstrip("/")
    find_lms()
    load_history()
    atexit.register(save_history)
    if DEBUG:
        print(f"URL:  {BASE_URL}")
        print(f"lms:  {_lms_path or 'NOT FOUND'}")
        print(f"Data: {DATA_DIR}")
        print(f"History: {len(state.model_history)} models, {state.total_gens} gens")
    app = MonitorWidget()
    app.mainloop()
