#!/usr/bin/env python3
"""
Bot Control Server — local HTTP API for BOT webpage.

Runs on port 8099, provides endpoints for bot.html to:
  - Check bot status (GET /status)
  - Start/stop the bot (POST /start, POST /stop)
  - Update config (POST /config)
  - Check Bybit balance (GET /balance)

Usage:
  python3 bot_server.py              # foreground
  nohup python3 bot_server.py &      # daemon
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import subprocess
import os
import signal
import time
import ssl
import urllib.request
import urllib.parse
import hmac
import hashlib
from datetime import datetime, timezone, timedelta

PORT = 8099
TZ = timezone(timedelta(hours=-4))
BOT_DIR = os.path.dirname(os.path.abspath(__file__))
LIVE_BOT = os.path.join(BOT_DIR, "live_bot.py")
PID_FILE = "/tmp/live_bot.pid"
CONFIG_FILE = os.path.join(BOT_DIR, "live_bot_config.json")
PAPER_CONFIG_FILES = {
    "core": os.path.join(BOT_DIR, "paper_bot_config.json"),
    "v2": os.path.join(BOT_DIR, "paper_bot_v2_config.json"),
    "v3": os.path.join(BOT_DIR, "paper_bot_v3_config.json"),
}
PAPER_CONFIG_DEFAULTS = {
    "core": {"capital": 100, "leverage": 10, "min_probability": 60, "tp_range_pct": 70, "sl_pct": 70, "tf_budget_15m": 50, "tf_budget_30m": 30, "tf_budget_1h": 20, "tf_budget_4h": 0},
    "v2":   {"capital": 100, "leverage": 10, "min_probability": 60, "tp_range_pct": 70, "sl_pct": 40, "tf_budget_15m": 50, "tf_budget_30m": 30, "tf_budget_1h": 20, "tf_budget_4h": 0},
    "v3":   {"capital": 100, "leverage": 10, "min_probability": 65, "tp_range_pct": 60, "sl_pct": 40, "tf_budget_15m": 50, "tf_budget_30m": 30, "tf_budget_1h": 20, "tf_budget_4h": 0},
}
CREDS_FILE = os.path.join(BOT_DIR, "bybit_credentials.json")
STATUS_FILE = os.path.join(BOT_DIR, "live_bot_status.json")
TRADES_FILE = os.path.join(BOT_DIR, "live_trades.json")
LOG_FILE = "/tmp/live_bot.log"

BYBIT_BASE = "https://api.bybit.com"

# SSL context for API calls
_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE


# ── Helpers ─────────────────────────────────────────────────────

def log(msg):
    ts = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def load_config():
    """Load config from JSON file, return defaults if missing."""
    defaults = {
        "capital": 100,
        "leverage": 10,
        "min_probability": 60,
        "tp_range_pct": 80,
        "max_open_4h": 3,
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                cfg = json.load(f)
            defaults.update(cfg)
        except Exception as e:
            log(f"Warning: could not load config: {e}")
    return defaults


def save_config(cfg):
    """Write config to JSON file."""
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)
    log(f"Config saved: {cfg}")


def load_paper_config(bot):
    """Load paper bot config override, return defaults if missing."""
    defaults = dict(PAPER_CONFIG_DEFAULTS.get(bot, PAPER_CONFIG_DEFAULTS["core"]))
    cfg_file = PAPER_CONFIG_FILES.get(bot)
    if cfg_file and os.path.exists(cfg_file):
        try:
            with open(cfg_file, "r") as f:
                cfg = json.load(f)
            defaults.update(cfg)
        except Exception as e:
            log(f"Warning: could not load paper config {bot}: {e}")
    return defaults


def save_paper_config(bot, cfg):
    """Write paper bot config override to JSON file."""
    cfg_file = PAPER_CONFIG_FILES.get(bot)
    if not cfg_file:
        return False
    with open(cfg_file, "w") as f:
        json.dump(cfg, f, indent=2)
    log(f"Paper config [{bot}] saved: {cfg}")
    return True


def load_credentials():
    """Load Bybit API credentials."""
    if not os.path.exists(CREDS_FILE):
        return None, None
    try:
        with open(CREDS_FILE, "r") as f:
            creds = json.load(f)
        key = creds.get("api_key", "")
        secret = creds.get("api_secret", "")
        if not key or key == "YOUR_API_KEY_HERE":
            return None, None
        return key, secret
    except Exception:
        return None, None


def bybit_sign(params_str, timestamp, api_key, api_secret, recv_window="5000"):
    """Create HMAC SHA256 signature for Bybit V5 API."""
    param_str = str(timestamp) + api_key + recv_window + params_str
    return hmac.new(api_secret.encode(), param_str.encode(), hashlib.sha256).hexdigest()


def bybit_request(method, endpoint, params, api_key, api_secret):
    """Authenticated Bybit V5 API request."""
    timestamp = str(int(time.time() * 1000))
    recv_window = "5000"

    if method == "GET":
        params_str = urllib.parse.urlencode(params) if params else ""
        url = f"{BYBIT_BASE}{endpoint}"
        if params_str:
            url += f"?{params_str}"
        body = None
    else:
        params_str = json.dumps(params) if params else ""
        url = f"{BYBIT_BASE}{endpoint}"
        body = params_str.encode() if params_str else None

    sig = bybit_sign(params_str, timestamp, api_key, api_secret, recv_window)
    headers = {
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-SIGN": sig,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv_window,
        "Content-Type": "application/json",
    }

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=15, context=_ctx) as resp:
        return json.loads(resp.read().decode())


def get_wallet_balance():
    """Fetch USDT balance from Bybit. Returns (balance_str, api_ok)."""
    api_key, api_secret = load_credentials()
    if not api_key:
        return None, False

    try:
        result = bybit_request("GET", "/v5/account/wallet-balance",
                               {"accountType": "UNIFIED"}, api_key, api_secret)
        if result.get("retCode") != 0:
            return None, False
        coins = result.get("result", {}).get("list", [{}])[0].get("coin", [])
        for c in coins:
            if c.get("coin") == "USDT":
                return c.get("walletBalance", "0"), True
        return "0", True
    except Exception as e:
        log(f"Balance check failed: {e}")
        return None, False


def is_bot_running():
    """Check if the bot process is alive. Returns (running, pid)."""
    if not os.path.exists(PID_FILE):
        return False, None
    try:
        with open(PID_FILE, "r") as f:
            pid = int(f.read().strip())
        # Check if process exists
        os.kill(pid, 0)
        return True, pid
    except (ValueError, ProcessLookupError, PermissionError, OSError):
        # PID file exists but process is dead — clean up
        try:
            os.remove(PID_FILE)
        except OSError:
            pass
        return False, None


def get_bot_mode():
    """Read current mode from status file."""
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, "r") as f:
                data = json.load(f)
            return data.get("mode", "unknown")
        except Exception:
            pass
    return "unknown"


def get_bot_uptime():
    """Calculate uptime from status file start_time."""
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, "r") as f:
                data = json.load(f)
            start = data.get("start_time")
            if start:
                st = datetime.fromisoformat(start)
                now = datetime.now(TZ)
                delta = now - st
                hours = int(delta.total_seconds() // 3600)
                mins = int((delta.total_seconds() % 3600) // 60)
                return f"{hours}h {mins}m"
        except Exception:
            pass
    return None


def kill_bot():
    """Kill the bot process if running."""
    running, pid = is_bot_running()
    if running and pid:
        try:
            os.kill(pid, signal.SIGTERM)
            # Wait briefly for clean shutdown
            for _ in range(10):
                try:
                    os.kill(pid, 0)
                    time.sleep(0.3)
                except ProcessLookupError:
                    break
            # Force kill if still alive
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        except Exception as e:
            log(f"Error killing bot (pid {pid}): {e}")
    # Clean up PID file
    try:
        os.remove(PID_FILE)
    except OSError:
        pass
    # Update status file
    write_status(False, get_bot_mode())


def start_bot(mode="dryrun"):
    """Start the bot process. Returns (ok, pid, error)."""
    # Kill existing if running
    kill_bot()
    time.sleep(0.5)

    cmd = ["python3", LIVE_BOT]
    if mode == "live":
        cmd.append("--live")

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=BOT_DIR,
            stdout=open(LOG_FILE, "a"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        pid = proc.pid
        with open(PID_FILE, "w") as f:
            f.write(str(pid))

        mode_label = "LIVE" if mode == "live" else "DRY-RUN"
        write_status(True, mode_label, pid)
        log(f"Bot started in {mode_label} mode, PID={pid}")
        return True, pid, None
    except Exception as e:
        log(f"Failed to start bot: {e}")
        return False, None, str(e)


def write_status(running, mode, pid=None):
    """Write status file for the dashboard."""
    balance, api_ok = get_wallet_balance()
    now = datetime.now(TZ).isoformat()

    # Read existing status for start_time preservation
    existing = {}
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, "r") as f:
                existing = json.load(f)
        except Exception:
            pass

    status = {
        "running": running,
        "mode": mode,
        "pid": pid,
        "balance": balance or existing.get("balance", "0"),
        "api_ok": api_ok,
        "last_check": now,
    }

    if running and not existing.get("start_time"):
        status["start_time"] = now
    elif running and existing.get("start_time"):
        status["start_time"] = existing["start_time"]

    try:
        with open(STATUS_FILE, "w") as f:
            json.dump(status, f, indent=2)
    except Exception as e:
        log(f"Error writing status: {e}")


def get_bybit_positions():
    """Fetch open positions from Bybit V5 API."""
    api_key, api_secret = load_credentials()
    if not api_key:
        return [], "No API credentials"

    try:
        result = bybit_request("GET", "/v5/position/list",
                               {"category": "linear", "settleCoin": "USDT"},
                               api_key, api_secret)
        if result.get("retCode") != 0:
            return [], result.get("retMsg", "API error")

        positions = []
        for p in result.get("result", {}).get("list", []):
            size = float(p.get("size", 0))
            if size == 0:
                continue
            positions.append({
                "symbol": p.get("symbol", ""),
                "side": p.get("side", ""),
                "size": p.get("size", "0"),
                "avgPrice": p.get("avgPrice", "0"),
                "markPrice": p.get("markPrice", "0"),
                "liqPrice": p.get("liqPrice", "0"),
                "unrealisedPnl": p.get("unrealisedPnl", "0"),
                "curRealisedPnl": p.get("curRealisedPnl", "0"),
                "leverage": p.get("leverage", "0"),
                "takeProfit": p.get("takeProfit", ""),
                "stopLoss": p.get("stopLoss", ""),
            })
        return positions, None
    except Exception as e:
        log(f"Position fetch failed: {e}")
        return [], str(e)


def get_live_trades():
    """Load trades from live_trades.json."""
    if not os.path.exists(TRADES_FILE):
        return []
    try:
        with open(TRADES_FILE, "r") as f:
            data = json.load(f)
        # Combine all TF trade lists
        trades = []
        for key, val in data.items():
            if key.startswith("trades_") and isinstance(val, list):
                trades.extend(val)
        return trades
    except Exception as e:
        log(f"Error loading trades: {e}")
        return []


# ── HTTP Handler ────────────────────────────────────────────────

class BotHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        """Override to use our log function."""
        log(f"HTTP {args[0]}")

    def send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_cors_headers()
        self.end_headers()

    def send_json(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length > 0:
            raw = self.rfile.read(length)
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {}
        return {}

    # ── GET endpoints ──

    def do_GET(self):
        path = self.path.split("?")[0]  # strip query params

        if path == "/status":
            self.handle_status()
        elif path == "/balance":
            self.handle_balance()
        elif path == "/config":
            self.handle_get_config()
        elif path.startswith("/paper-config/"):
            bot = path.split("/")[-1]
            if bot in PAPER_CONFIG_FILES:
                self.send_json(load_paper_config(bot))
            else:
                self.send_json({"error": "unknown bot"}, 400)
        elif path == "/positions":
            self.handle_positions()
        elif path == "/trades":
            self.handle_trades()
        else:
            self.send_json({"error": "not found"}, 404)

    def handle_status(self):
        running, pid = is_bot_running()
        mode = get_bot_mode() if running else "stopped"
        balance, api_ok = get_wallet_balance()
        config = load_config()
        uptime = get_bot_uptime() if running else None

        last_scan = None
        if os.path.exists(STATUS_FILE):
            try:
                with open(STATUS_FILE, "r") as f:
                    st = json.load(f)
                last_scan = st.get("last_scan") or st.get("last_check")
            except Exception:
                pass

        self.send_json({
            "running": running,
            "mode": mode,
            "pid": pid,
            "balance": balance or "0",
            "api_ok": api_ok,
            "config": config,
            "uptime": uptime,
            "last_scan": last_scan,
        })

    def handle_balance(self):
        balance, api_ok = get_wallet_balance()
        self.send_json({
            "balance": balance or "0",
            "api_ok": api_ok,
        })

    def handle_get_config(self):
        self.send_json(load_config())

    def handle_positions(self):
        positions, error = get_bybit_positions()
        self.send_json({
            "positions": positions,
            "error": error,
            "timestamp": datetime.now(TZ).isoformat(),
        })

    def handle_trades(self):
        trades = get_live_trades()
        self.send_json({
            "trades": trades,
            "count": len(trades),
            "timestamp": datetime.now(TZ).isoformat(),
        })

    # ── POST endpoints ──

    def do_POST(self):
        path = self.path.split("?")[0]

        if path == "/start":
            self.handle_start()
        elif path == "/stop":
            self.handle_stop()
        elif path == "/config":
            self.handle_config()
        elif path.startswith("/paper-config/"):
            self.handle_paper_config(path.split("/")[-1])
        elif path.startswith("/paper-control/"):
            self.handle_paper_control(path.split("/")[-1])
        else:
            self.send_json({"error": "not found"}, 404)

    def handle_paper_control(self, bot_name):
        body = self.read_body()
        action = body.get("action", "")
        scripts = {"core": "paper_bot.py", "v2": "paper_bot_v2.py", "v3": "paper_bot_v3.py"}
        pids = {"core": "/tmp/paper_bot.pid", "v2": "/tmp/paper_bot_v2.pid", "v3": "/tmp/paper_bot_v3.pid"}
        logs = {"core": "/tmp/paper_bot.log", "v2": "/tmp/paper_bot_v2.log", "v3": "/tmp/paper_bot_v3.log"}

        if bot_name not in scripts:
            self.send_json({"ok": False, "error": "unknown bot"}, 400)
            return

        script = scripts[bot_name]
        pid_file = pids[bot_name]
        log_file = logs[bot_name]

        if action == "start":
            # Kill if already running
            try:
                with open(pid_file) as f:
                    old_pid = int(f.read().strip())
                os.kill(old_pid, signal.SIGTERM)
                time.sleep(1)
            except:
                pass
            proc = subprocess.Popen(
                ["python3", script],
                cwd=BOT_DIR,
                stdout=open(log_file, "a"),
                stderr=subprocess.STDOUT,
                start_new_session=True
            )
            with open(pid_file, "w") as f:
                f.write(str(proc.pid))
            self.send_json({"ok": True, "action": "start", "pid": proc.pid})

        elif action == "stop":
            try:
                with open(pid_file) as f:
                    pid = int(f.read().strip())
                os.kill(pid, signal.SIGTERM)
                self.send_json({"ok": True, "action": "stop", "pid": pid})
            except:
                self.send_json({"ok": True, "action": "stop", "msg": "was not running"})

        elif action == "pause":
            # Pause = stop (no pause state in Python)
            try:
                with open(pid_file) as f:
                    pid = int(f.read().strip())
                os.kill(pid, signal.SIGTERM)
                self.send_json({"ok": True, "action": "pause", "pid": pid})
            except:
                self.send_json({"ok": True, "action": "pause", "msg": "was not running"})
        else:
            self.send_json({"ok": False, "error": "unknown action"}, 400)

    def handle_start(self):
        body = self.read_body()
        mode = body.get("mode", "dryrun")

        if mode not in ("live", "dryrun"):
            self.send_json({"ok": False, "error": "mode must be 'live' or 'dryrun'"}, 400)
            return

        ok, pid, error = start_bot(mode)
        if ok:
            self.send_json({"ok": True, "pid": pid, "mode": mode})
        else:
            self.send_json({"ok": False, "error": error}, 500)

    def handle_stop(self):
        running, pid = is_bot_running()
        if not running:
            self.send_json({"ok": True, "msg": "bot was not running"})
            return

        kill_bot()
        self.send_json({"ok": True, "killed_pid": pid})

    def handle_config(self):
        body = self.read_body()
        if not body:
            self.send_json({"ok": False, "error": "empty body"}, 400)
            return

        # Merge with existing config
        config = load_config()
        allowed_keys = {"capital", "leverage", "min_probability", "tp_range_pct", "sl_pct", "max_open_4h",
                        "total_budget", "tf_budget_15m", "tf_budget_30m", "tf_budget_1h", "tf_budget_4h"}
        for k, v in body.items():
            if k in allowed_keys:
                # Validate numeric
                try:
                    config[k] = int(v) if isinstance(v, int) or (isinstance(v, str) and v.isdigit()) else float(v)
                except (ValueError, TypeError):
                    pass

        save_config(config)

        # Restart bot if running
        running, _ = is_bot_running()
        restarted = False
        if running:
            mode = get_bot_mode()
            mode_arg = "live" if mode == "LIVE" else "dryrun"
            start_bot(mode_arg)
            restarted = True

        self.send_json({"ok": True, "config": config, "restarted": restarted})

    def handle_paper_config(self, bot):
        if bot not in PAPER_CONFIG_FILES:
            self.send_json({"ok": False, "error": "unknown bot"}, 400)
            return
        body = self.read_body()
        if not body:
            self.send_json({"ok": False, "error": "empty body"}, 400)
            return

        config = load_paper_config(bot)
        allowed_keys = {"capital", "leverage", "min_probability", "tp_range_pct", "sl_pct",
                        "total_budget", "tf_budget_15m", "tf_budget_30m", "tf_budget_1h", "tf_budget_4h"}
        for k, v in body.items():
            if k in allowed_keys:
                try:
                    config[k] = int(v) if isinstance(v, (int, float)) else int(v)
                except (ValueError, TypeError):
                    pass

        save_paper_config(bot, config)
        self.send_json({"ok": True, "config": config})


# ── Main ────────────────────────────────────────────────────────

def main():
    log(f"Bot Control Server starting on port {PORT}")
    log(f"Bot directory: {BOT_DIR}")
    log(f"Config file: {CONFIG_FILE}")
    log(f"PID file: {PID_FILE}")

    server = HTTPServer(("0.0.0.0", PORT), BotHandler)
    log(f"Listening on http://0.0.0.0:{PORT}")
    log("Endpoints: GET /status, /balance, /config, /positions, /trades | POST /start, /stop, /config")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("Shutting down server...")
        server.shutdown()


if __name__ == "__main__":
    main()
