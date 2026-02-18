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


# ── GUI ───────────────────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("LM Studio Monitor")
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=C.TKEY)
        if sys.platform == "win32":
            try: self.attributes("-transparentcolor", C.TKEY)
            except: pass

        self._col = False
        self._h = W_H_FULL
        self.geometry(f"{W_W}x{W_H_FULL}")
        sx, sy = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"+{sx-W_W-S(30)}+{sy-W_H_FULL-S(70)}")

        self._dx = self._dy = 0
        self._pin = True

        mn = "Consolas" if sys.platform == "win32" else "Menlo"
        sn = "Segoe UI" if sys.platform == "win32" else "Helvetica Neue"
        self.F = {
            "ms": tkfont.Font(family=mn, size=8), "mm": tkfont.Font(family=mn, size=11, weight="bold"),
            "ml": tkfont.Font(family=mn, size=26, weight="bold"), "mv": tkfont.Font(family=mn, size=10),
            "tt": tkfont.Font(family=mn, size=9, weight="bold"), "ss": tkfont.Font(family=sn, size=8),
            "md": tkfont.Font(family=sn, size=11, weight="bold"), "sc": tkfont.Font(family=mn, size=8, weight="bold"),
            "ct": tkfont.Font(family=mn, size=16, weight="bold"), "cl": tkfont.Font(family=mn, size=7),
            "cm": tkfont.Font(family=sn, size=9, weight="bold"),
        }

        self._build()
        for fn in [poll_models_loop, poll_ps_loop, log_stream_thread, periodic_save]:
            threading.Thread(target=fn, daemon=True).start()
        self._tick()

    # ── Rounded bg ──

    def _seth(self, h):
        self._h = h
        self.geometry(f"{W_W}x{h}")
        self._ct.place_configure(height=h-S(12))
        self._cv.config(height=h)
        self._drawbg()

    def _drawbg(self):
        c = self._cv; c.delete("bg")
        w, h, r = W_W, self._h, CORNER_R
        self._rr(c, S(3),S(3), w-1,h-1, r, fill="#d0d0d8", outline="", tags="bg")
        self._rr(c, 0,0, w-S(4),h-S(4), r, fill=C.BG, outline=C.BORDER, tags="bg")

    def _rr(self, cv, x1,y1,x2,y2, r, **kw):
        cv.create_polygon(x1+r,y1, x2-r,y1, x2,y1, x2,y1+r, x2,y2-r, x2,y2,
                          x2-r,y2, x1+r,y2, x1,y2, x1,y2-r, x1,y1+r, x1,y1, smooth=True, **kw)

    # ── Build ──

    def _build(self):
        self._cv = tk.Canvas(self, width=W_W, height=W_H_FULL, bg=C.TKEY, highlightthickness=0, bd=0)
        self._cv.place(x=0, y=0, relwidth=1, relheight=1)
        self._drawbg()
        self._ct = tk.Frame(self, bg=C.BG)
        self._ct.place(x=S(6), y=S(6), width=W_W-S(12), height=W_H_FULL-S(12))
        r = self._ct

        # ── Titlebar ──
        tb = tk.Frame(r, bg=C.BG, height=S(28)); tb.pack(fill="x", padx=S(8), pady=(S(6),0)); tb.pack_propagate(False)
        self._drag(tb)
        self.dot = tk.Canvas(tb, width=S(10), height=S(10), bg=C.BG, highlightthickness=0)
        self.dot.pack(side="left", padx=(0,S(6))); self._drag(self.dot)
        l = tk.Label(tb, text="LM STUDIO", font=self.F["tt"], fg=C.TEXT_DIM, bg=C.BG); l.pack(side="left"); self._drag(l)
        xb = tk.Label(tb, text="✕", font=self.F["ss"], fg=C.TEXT_DIM, bg=C.BG, cursor="hand2"); xb.pack(side="right")
        xb.bind("<Button-1>", lambda e: self._close())
        xb.bind("<Enter>", lambda e: xb.config(fg=C.RED)); xb.bind("<Leave>", lambda e: xb.config(fg=C.TEXT_DIM))
        self.pinb = tk.Label(tb, text="📌", font=self.F["ss"], fg=C.ACCENT, bg=C.BG, cursor="hand2")
        self.pinb.pack(side="right", padx=(0,S(6))); self.pinb.bind("<Button-1>", self._tpin)
        self.colb = tk.Label(tb, text="▾", font=self.F["tt"], fg=C.TEXT_DIM, bg=C.BG, cursor="hand2")
        self.colb.pack(side="right", padx=(0,S(6))); self.colb.bind("<Button-1>", self._tcol)

        # ── Compact view ──
        self._cf = tk.Frame(r, bg=C.BG)
        self._cf.pack(fill="both", expand=True)
        r1 = tk.Frame(self._cf, bg=C.BG); r1.pack(fill="x", padx=S(10), pady=(S(6),0))
        self.cb = tk.Label(r1, text="●", font=self.F["cl"], fg=C.GREEN, bg=C.BG); self.cb.pack(side="left", padx=(0,S(4)))
        self.cmod = tk.Label(r1, text="—", font=self.F["cm"], fg=C.TEXT, bg=C.BG, anchor="w"); self.cmod.pack(side="left", fill="x", expand=True)
        self.cup = tk.Label(r1, text="", font=self.F["cl"], fg=C.TEXT_DIM, bg=C.BG); self.cup.pack(side="right")
        r2 = tk.Frame(self._cf, bg=C.SURFACE, highlightbackground=C.BORDER, highlightthickness=1)
        r2.pack(fill="x", padx=S(10), pady=(S(6),0))
        lf = tk.Frame(r2, bg=C.SURFACE); lf.pack(side="left", padx=(S(10),0), pady=S(6))
        self.ctps = tk.Label(lf, text="—", font=self.F["ct"], fg=C.CYAN, bg=C.SURFACE, anchor="w"); self.ctps.pack(anchor="w")
        self.cst = tk.Label(lf, text="", font=self.F["cl"], fg=C.TEXT_DIM, bg=C.SURFACE); self.cst.pack(anchor="w")
        self.cspk = tk.Canvas(r2, width=S(100), height=S(36), bg=C.SURFACE, highlightthickness=0)
        self.cspk.pack(side="right", padx=(S(4),S(8)), pady=S(6))
        r3 = tk.Frame(self._cf, bg=C.BG); r3.pack(fill="x", padx=S(10), pady=(S(6),0))
        self.cses = tk.Label(r3, text="", font=self.F["cl"], fg=C.TEXT_DIM, bg=C.BG, anchor="w"); self.cses.pack(fill="x")

        # ── Full view ──
        self._ff = tk.Frame(r, bg=C.BG)
        self._ff.pack(fill="both", expand=True)
        d = self._ff

        sr = tk.Frame(d, bg=C.BG); sr.pack(fill="x", padx=S(10), pady=(S(6),0))
        self.badge = tk.Label(sr, text="OFFLINE", font=self.F["ms"], fg=C.RED, bg=C.RED_BG, padx=S(6), pady=S(1)); self.badge.pack(side="left")
        self.uptime = tk.Label(sr, text="--:--:--", font=self.F["ms"], fg=C.TEXT_DIM, bg=C.BG); self.uptime.pack(side="right")

        mc = tk.Frame(d, bg=C.SURFACE, highlightbackground=C.BORDER, highlightthickness=1)
        mc.pack(fill="x", padx=S(10), pady=(S(8),0))
        tk.Frame(mc, bg=C.ACCENT, width=S(3)).pack(side="left", fill="y")
        mi = tk.Frame(mc, bg=C.SURFACE); mi.pack(fill="both", expand=True, padx=S(8), pady=S(6))
        self.mn = tk.Label(mi, text="No model loaded", font=self.F["md"], fg=C.TEXT_DIM, bg=C.SURFACE, anchor="w"); self.mn.pack(fill="x")
        self.mm = tk.Label(mi, text="", font=self.F["ms"], fg=C.TEXT_DIM, bg=C.SURFACE, anchor="w"); self.mm.pack(fill="x", pady=(S(1),0))

        self._sl(d, "LAST GENERATION")
        lg = tk.Frame(d, bg=C.SURFACE, highlightbackground=C.BORDER, highlightthickness=1); lg.pack(fill="x", padx=S(10), pady=(S(4),0))
        tr = tk.Frame(lg, bg=C.SURFACE); tr.pack(fill="x", padx=S(10), pady=(S(8),0))
        self.ftps = tk.Label(tr, text="—", font=self.F["ml"], fg=C.CYAN, bg=C.SURFACE, anchor="w"); self.ftps.pack(side="left")
        tk.Label(tr, text="t/s", font=self.F["ms"], fg=C.TEXT_DIM, bg=C.SURFACE, anchor="sw").pack(side="left", padx=(S(4),0), pady=(0,S(6)))
        self.fmod = tk.Label(tr, text="", font=self.F["ms"], fg=C.TEXT_DIM, bg=C.SURFACE, anchor="se"); self.fmod.pack(side="right", pady=(0,S(6)))
        sg = tk.Frame(lg, bg=C.SURFACE); sg.pack(fill="x", padx=S(10), pady=(S(4),S(8)))
        sg.columnconfigure(0, weight=1); sg.columnconfigure(1, weight=1); sg.columnconfigure(2, weight=1)
        self.fttft = self._ms(sg,0,0,"TTFT"); self.ftime = self._ms(sg,0,1,"TIME"); self.ftok = self._ms(sg,0,2,"TOKENS")
        self.fprom = self._ms(sg,1,0,"PROMPT"); self.fgen = self._ms(sg,1,1,"GEN'D"); self.fstp = self._ms(sg,1,2,"STOP")

        self._sl(d, "TPS HISTORY")
        hf = tk.Frame(d, bg=C.SURFACE, highlightbackground=C.BORDER, highlightthickness=1); hf.pack(fill="x", padx=S(10), pady=(S(4),0))
        hh = tk.Frame(hf, bg=C.SURFACE); hh.pack(fill="x", padx=S(10), pady=(S(6),0))
        self.hi = tk.Label(hh, text="No data yet", font=self.F["ms"], fg=C.TEXT_DIM, bg=C.SURFACE, anchor="w"); self.hi.pack(side="left")
        self.hs = tk.Label(hh, text="", font=self.F["ms"], fg=C.CYAN, bg=C.SURFACE, anchor="e"); self.hs.pack(side="right")
        self.spk = tk.Canvas(hf, height=S(55), bg=C.SURFACE, highlightthickness=0); self.spk.pack(fill="x", padx=S(10), pady=(S(2),S(8)))

        self._sl(d, "SESSION")
        sf = tk.Frame(d, bg=C.BG); sf.pack(fill="x", padx=S(10), pady=(S(4),0))
        sf.columnconfigure(0,weight=1); sf.columnconfigure(1,weight=1); sf.columnconfigure(2,weight=1)
        self.sg = self._cd(sf,0,"GENS","0",C.ACCENT); self.st = self._cd(sf,1,"TOKENS","0",C.CYAN); self.sq = self._cd(sf,2,"QUEUE","0",C.TEXT)

        # Footer
        ft = tk.Frame(r, bg=C.BG); ft.pack(fill="x", side="bottom", padx=S(10), pady=(S(4),S(6)))
        self.fu = tk.Label(ft, text=BASE_URL, font=self.F["ms"], fg=C.TEXT_DIM, bg=C.BG); self.fu.pack(side="left")
        self.fs = tk.Label(ft, text="", font=self.F["ms"], fg=C.TEXT_DIM, bg=C.BG); self.fs.pack(side="right")

        # Start expanded
        self._cf.pack_forget()

    def _sl(self, p, t):
        f = tk.Frame(p, bg=C.BG); f.pack(fill="x", padx=S(10), pady=(S(8),0))
        tk.Label(f, text=t, font=self.F["sc"], fg=C.TEXT_DIM, bg=C.BG).pack(side="left")

    def _ms(self, p, r, c, lb):
        f = tk.Frame(p, bg=C.SURFACE); f.grid(row=r, column=c, sticky="w", padx=(0,S(8)), pady=S(2))
        tk.Label(f, text=lb, font=self.F["ms"], fg=C.TEXT_DIM, bg=C.SURFACE).pack(anchor="w")
        v = tk.Label(f, text="—", font=self.F["mv"], fg=C.TEXT, bg=C.SURFACE, anchor="w"); v.pack(anchor="w")
        return v

    def _cd(self, p, c, lb, iv, cl):
        b = tk.Frame(p, bg=C.SURFACE, highlightbackground=C.BORDER, highlightthickness=1)
        b.grid(row=0, column=c, sticky="nsew", padx=(0 if c==0 else S(3), 0))
        i = tk.Frame(b, bg=C.SURFACE); i.pack(padx=S(8), pady=S(5))
        tk.Label(i, text=lb, font=self.F["ms"], fg=C.TEXT_DIM, bg=C.SURFACE).pack(anchor="w")
        v = tk.Label(i, text=iv, font=self.F["mm"], fg=cl, bg=C.SURFACE, anchor="w"); v.pack(anchor="w")
        return v

    # ── Interactions ──

    def _drag(self, w):
        w.bind("<Button-1>", lambda e: self.__setattr__('_dx', e.x_root-self.winfo_x()) or self.__setattr__('_dy', e.y_root-self.winfo_y()))
        w.bind("<B1-Motion>", lambda e: self.geometry(f"+{e.x_root-self._dx}+{e.y_root-self._dy}"))

    def _tpin(self, e=None):
        self._pin = not self._pin
        self.attributes("-topmost", self._pin)
        self.pinb.config(fg=C.ACCENT if self._pin else C.TEXT_DIM)

    def _tcol(self, e=None):
        self._col = not self._col
        footer = self.fu.master
        if self._col:
            self._ff.pack_forget()
            self._cf.pack(fill="both", expand=True, before=footer)
            self.colb.config(text="▸")
            self._seth(W_H_MINI)
        else:
            self._cf.pack_forget()
            self._ff.pack(fill="both", expand=True, before=footer)
            self.colb.config(text="▾")
            self._seth(W_H_FULL)

    def _close(self):
        save_history()
        self.destroy()

    def _sdot(self, c):
        self.dot.delete("all")
        self.dot.create_oval(1,1,S(9),S(9), fill=c, outline="")

    # ── Sparkline ──

    def _dspk(self, cv, data, dots=True):
        cv.delete("all"); cv.update_idletasks()
        w, h = cv.winfo_width(), cv.winfo_height()
        if w < S(10) or h < S(10): return
        if not data:
            cv.create_line(0,h-1,w,h-1, fill=C.BORDER); return
        if len(data) == 1:
            cx,cy = w//2, h//2
            cv.create_oval(cx-S(3),cy-S(3),cx+S(3),cy+S(3), fill=C.SPARK_LINE, outline=""); return
        mx = max(data)*1.1 if max(data)>0 else 1
        px,py = S(6),S(4)
        uw,uh = w-2*px, h-2*py
        step = uw/(len(data)-1)
        pts = [(px+i*step, h-py-(v/mx)*uh) for i,v in enumerate(data)]
        fp = [(pts[0][0],h-py)] + pts + [(pts[-1][0],h-py)]
        cv.create_polygon([c for p in fp for c in p], fill=C.SPARK_FILL, outline="", smooth=True)
        lf = [c for p in pts for c in p]
        if len(lf)>=4: cv.create_line(lf, fill=C.SPARK_LINE, width=max(1,S(1.5)), smooth=True)
        if dots:
            for i,(x,y) in enumerate(pts):
                r = S(3) if i==len(pts)-1 else S(2)
                cv.create_oval(x-r,y-r,x+r,y+r, fill=C.SPARK_DOT, outline="")

    # ── Tick ──

    def _tick(self):
        try:
            s = state
            # Dot
            self._sdot(C.RED if not s.connected else (C.AMBER if s.server_status=="generating" else C.GREEN))
            if self._col: self._uc()
            else: self._uf()
            # Footer
            p = []
            if s.api_latency_ms > 0: p.append(f"{s.api_latency_ms:.0f}ms")
            p.append("stream ✓" if s.log_stream_active else ("stream …" if _lms_path else "no lms"))
            self.fs.config(text="  ".join(p))
        except Exception as e:
            dbg(f"UI: {e}")
        self.after(300, self._tick)

    def _uc(self):
        """Update compact view — shows current model's stats."""
        s = state; m = s.primary_model
        mid = s.get_model_id()
        g = s.last_gen_for(mid)
        td = s.tps_data_for(mid)
        mg, mt = s.totals_for(mid)

        self.cb.config(fg=C.RED if not s.connected else (C.AMBER if s.server_status=="generating" else C.GREEN))
        self.cmod.config(text=(m.name[:22]+"…" if m and len(m.name)>24 else m.name) if m else "No model", fg=C.TEXT if m else C.TEXT_DIM)
        self.cup.config(text=fmt_dur(time.time()-s.connected_since) if s.connected and s.connected_since else "")
        if g:
            self.ctps.config(text=f"{g.tps:.1f}", fg=C.CYAN)
            ttft = g.ttft_sec*1000 if g.ttft_sec < 10 else g.ttft_sec
            self.cst.config(text=f"TTFT {ttft:.0f}ms  ·  {g.total_sec:.1f}s  ·  {g.predicted_tokens} tok")
        else:
            self.ctps.config(text="—", fg=C.TEXT_DIM); self.cst.config(text="waiting…")
        self._dspk(self.cspk, td, dots=False)
        p = [f"{mg} gens"]
        if mt: p.append(f"{mt:,} tok")
        if td: p.append(f"avg {sum(td)/len(td):.1f} t/s")
        self.cses.config(text="  ·  ".join(p))

    def _uf(self):
        """Update full view — shows current model's stats."""
        s = state; m = s.primary_model
        mid = s.get_model_id()
        g = s.last_gen_for(mid)
        td = s.tps_data_for(mid)
        mg, mt = s.totals_for(mid)

        # Badge
        if not s.connected: self.badge.config(text="OFFLINE", fg=C.RED, bg=C.RED_BG)
        elif s.server_status=="generating": self.badge.config(text="GENERATING", fg=C.AMBER, bg=C.AMBER_BG)
        else: self.badge.config(text="CONNECTED", fg=C.GREEN, bg=C.GREEN_BG)
        # Uptime
        if s.connected and s.connected_since:
            d=int(time.time()-s.connected_since); h,rm=divmod(d,3600); mn,sc=divmod(rm,60)
            self.uptime.config(text=f"{h:02}:{mn:02}:{sc:02}")
        else: self.uptime.config(text="--:--:--")
        # Model
        if m:
            self.mn.config(text=(m.name[:28]+"…" if len(m.name)>30 else m.name), fg=C.TEXT)
            self.mm.config(text="  ·  ".join(p for p in [m.arch, m.quant, m.fmt.upper() if m.fmt else "", f"{m.ctx:,} ctx" if m.ctx else ""] if p))
        else:
            self.mn.config(text="No model loaded", fg=C.TEXT_DIM); self.mm.config(text="")
        # Last gen for THIS model
        if g:
            self.ftps.config(text=f"{g.tps:.1f}", fg=C.CYAN)
            ttft = g.ttft_sec*1000 if g.ttft_sec < 10 else g.ttft_sec
            self.fttft.config(text=f"{ttft:.0f}ms"); self.ftime.config(text=f"{g.total_sec:.1f}s")
            self.ftok.config(text=f"{g.total_tokens:,}"); self.fprom.config(text=f"{g.prompt_tokens:,}")
            self.fgen.config(text=f"{g.predicted_tokens:,}"); self.fstp.config(text=g.stop_reason or "—")
            self.fmod.config(text="")
        else:
            self.ftps.config(text="—", fg=C.TEXT_DIM)
            for l in [self.fttft,self.ftime,self.ftok,self.fprom,self.fgen,self.fstp]: l.config(text="—")
            self.fmod.config(text="no history" if m else "")
        # History for THIS model
        if td:
            self.hi.config(text=f"{len(td)} generations")
            self.hs.config(text=f"avg {sum(td)/len(td):.1f}  peak {max(td):.1f}")
        else: self.hi.config(text="No data for this model"); self.hs.config(text="")
        self._dspk(self.spk, td, dots=True)
        # Session for THIS model
        self.sg.config(text=str(mg)); self.st.config(text=f"{mt:,}")
        self.sq.config(text=str(s.queued_requests), fg=C.AMBER if s.queued_requests>0 else C.TEXT)


# ── Entry ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if args: BASE_URL = args[0].rstrip("/")
    find_lms(); load_history(); atexit.register(save_history)
    if DEBUG:
        print(f"URL: {BASE_URL}\nlms: {_lms_path or 'NOT FOUND'}\nDPI: {DPI_SCALE:.2f}x ({int(DPI_SCALE*96)} dpi)\nHistory: {len(state.model_history)} models, {state.all_gens} total gens")
    app = App(); app.mainloop()
