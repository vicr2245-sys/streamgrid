#!/usr/bin/env python3
"""
Spotify Android Automation — Web UI
Run:  python app.py
Then: http://localhost:5050
"""

import subprocess, time, sys, os, threading, logging, json, shutil, random
from flask import Flask, jsonify, request, Response
import queue

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = Flask(__name__)

# ── Global state ─────────────────────────────
state = {
    "devices": [],
    "selected": None,
    "proxy_active": False,
    "scrcpy_procs": {},   # serial → Popen
    "proxy_proc": None,
    "appium_driver": None,
    "loop_active": False,
    "loop_thread": None,
    "loop_next_restart": None,   # epoch seconds of next restart
}
log_queue = queue.Queue()

SPOTIFY_PACKAGE  = "com.spotify.music"
SPOTIFY_ACTIVITY = "com.spotify.music.MainActivity"
APPIUM_SERVER    = "http://127.0.0.1:4723"

# ── Logging helper ───────────────────────────
def emit(msg, level="info"):
    log_queue.put({"msg": msg, "level": level})
    getattr(log, level)(msg)

# ── ADB helpers ──────────────────────────────
def adb(*args, serial=None):
    prefix = ["-s", serial] if serial else []
    cmd = ["adb"] + prefix + list(args)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return r.stdout.strip(), r.returncode
    except FileNotFoundError:
        emit("adb not found — install Android Platform Tools", "error")
        return "", 1
    except subprocess.TimeoutExpired:
        emit("adb command timed out", "error")
        return "", 1

def refresh_devices():
    out, _ = adb("devices")
    devs = []
    for line in out.splitlines()[1:]:
        if "\tdevice" in line:
            serial = line.split("\t")[0]
            # grab model name
            model, _ = adb("shell", "getprop", "ro.product.model", serial=serial)
            android, _ = adb("shell", "getprop", "ro.build.version.release", serial=serial)
            devs.append({"serial": serial, "model": model or serial, "android": android or "?"})
    state["devices"] = devs
    return devs

# ── Proxy helpers ────────────────────────────
def set_device_proxy(serial, host, port):
    adb("shell", "settings", "put", "global", "http_proxy", f"{host}:{port}", serial=serial)
    emit(f"Proxy set on device → {host}:{port}")

def clear_device_proxy(serial):
    adb("shell", "settings", "put", "global", "http_proxy", ":0", serial=serial)
    emit("Device proxy cleared")

def start_mitm_tunnel(proxy_host, proxy_port, proxy_user, proxy_pass, local_port=8118):
    """Start mitmproxy as an upstream-auth tunnel on localhost."""
    if not shutil.which("mitmdump"):
        emit("mitmdump not found — install mitmproxy>=10.0.0", "error")
        return None
    cmd = [
        "mitmdump",
        "--mode", f"upstream:http://{proxy_host}:{proxy_port}",
        "--upstream-auth", f"{proxy_user}:{proxy_pass}",
        "--listen-port", str(local_port),
        "--quiet",
    ]
    proc = subprocess.Popen(cmd)
    time.sleep(2)
    emit(f"mitmproxy tunnel started on port {local_port}")
    return proc

# ── scrcpy helpers ───────────────────────────
def launch_scrcpy_for(serial, x=0, y=0, max_size=800):
    if not shutil.which("scrcpy"):
        emit("scrcpy not found — install it from https://github.com/Genymobile/scrcpy", "error")
        return None
    cmd = [
        "scrcpy", "--serial", serial,
        "--max-size", str(max_size),
        "--window-title", f"Android [{serial}]",
        "--window-x", str(x),
        "--window-y", str(y),
        "--stay-awake",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    emit(f"scrcpy launched for {serial}")
    return proc

# ── Appium helpers ───────────────────────────
def connect_appium(serial):
    try:
        from appium import webdriver
    except ImportError:
        emit("Appium-Python-Client not installed: pip install Appium-Python-Client", "error")
        return None
    # Appium Python Client v4+ uses UiAutomator2Options
    from appium.options.android import UiAutomator2Options
    opts = UiAutomator2Options()
    opts.platform_name          = "Android"
    opts.device_name            = serial
    opts.udid                   = serial
    opts.app_package            = SPOTIFY_PACKAGE
    opts.app_activity           = SPOTIFY_ACTIVITY
    opts.no_reset               = True
    opts.auto_grant_permissions = True
    opts.new_command_timeout    = 3600   # keep session alive when idle
    opts.set_capability("appium:appiumVersion", "")  # strip appium user-agent header
    # Appium v3 base path is / not /wd/hub
    server_url = APPIUM_SERVER.rstrip("/")
    try:
        driver = webdriver.Remote(server_url, options=opts)
        emit("Appium driver connected ✓")
        return driver
    except Exception as e:
        emit(f"Appium connection failed: {e}", "error")
        return None

# ── Playlist automation helpers ─────────────

def appium_find_and_open_playlist(driver, query: str) -> bool:
    """Search for a playlist and open the first result."""
    from appium.webdriver.common.appiumby import AppiumBy
    try:
        # Tap the Search tab with a human-like delay
        time.sleep(random.uniform(0.8, 1.8))
        driver.find_element(AppiumBy.ACCESSIBILITY_ID, "Search").click()
        time.sleep(random.uniform(1.0, 1.8))

        # Tap the search input bar
        search_bar = driver.find_element(
            AppiumBy.ID, "com.spotify.music:id/search_bar_text_input"
        )
        search_bar.click()
        time.sleep(random.uniform(0.4, 0.9))
        search_bar.clear()

        # Type query character by character like a human
        for char in query:
            search_bar.send_keys(char)
            time.sleep(random.uniform(0.05, 0.18))
        time.sleep(random.uniform(1.5, 2.5))

        # Tap the first playlist result
        results = driver.find_elements(
            AppiumBy.XPATH,
            '//android.widget.TextView[contains(@resource-id,"title")]'
        )
        if not results:
            emit("No results found for playlist query", "warning")
            return False
        time.sleep(random.uniform(0.3, 0.8))
        results[0].click()
        time.sleep(random.uniform(1.5, 2.5))
        emit(f"Opened first result for: {query}")
        return True
    except Exception as e:
        emit(f"Playlist open error: {e}", "error")
        return False


def appium_restart_song(driver):
    """Seek the current track back to position 0:00."""
    from appium.webdriver.common.appiumby import AppiumBy
    try:
        # Try dragging the seek bar to the far left
        seek = driver.find_element(
            AppiumBy.ID, "com.spotify.music:id/seekbar"
        )
        size   = seek.size
        loc    = seek.location
        start_x = loc["x"] + size["width"] - 10
        start_y = loc["y"] + size["height"] // 2
        end_x   = loc["x"] + 2
        end_y   = start_y
        driver.swipe(start_x, start_y, end_x, end_y, duration=300)
        emit("Song restarted (seekbar dragged to 0:00)")
        return True
    except Exception:
        # Fallback: use media keyevent to previous-track twice
        # (first press = restart, second = previous track — so one press restarts if < 3s in)
        try:
            driver.press_keycode(88)   # KEYCODE_MEDIA_PREVIOUS
            time.sleep(0.4)
            emit("Song restarted via keycode")
            return True
        except Exception as e2:
            emit(f"Restart failed: {e2}", "error")
            return False


def appium_ensure_playing(driver):
    """Make sure Spotify is playing (tap play if paused)."""
    from appium.webdriver.common.appiumby import AppiumBy
    try:
        btn = driver.find_element(AppiumBy.ACCESSIBILITY_ID, "Play")
        btn.click()
        emit("Pressed Play")
    except Exception:
        pass   # Already playing — Play button not visible


def _human_delay(min_ms=400, max_ms=2000):
    """Sleep a random human-like duration in milliseconds."""
    time.sleep(random.randint(min_ms, max_ms) / 1000)


def _loop_worker(query: str, min_sec: int, max_sec: int):
    """Background thread: find playlist, play, restart every N seconds."""
    driver = state["appium_driver"]
    if not driver:
        emit("Loop aborted — Appium not connected", "error")
        state["loop_active"] = False
        return

    emit(f"Loop starting \u2192 '{query}'  ({min_sec}\u2013{max_sec}s)")

    # Open the playlist once at the start
    if not appium_find_and_open_playlist(driver, query):
        state["loop_active"] = False
        return

    _human_delay(800, 2000)
    appium_ensure_playing(driver)

    cycle = 0
    full_play_chance = 0.15   # 15% chance to let a song play fully instead of restarting

    while state["loop_active"]:
        cycle += 1

        # Occasionally let the song play all the way through (human behaviour)
        if random.random() < full_play_chance:
            wait = random.randint(max_sec, max_sec + 60)
            emit(f"Cycle {cycle} — letting song play longer ({wait}s)")
        else:
            # Add slight jitter so interval is never perfectly regular
            jitter = random.randint(-5, 5)
            wait = max(min_sec, random.randint(min_sec, max_sec) + jitter)
            emit(f"Cycle {cycle} — next restart in {wait}s")

        state["loop_next_restart"] = time.time() + wait

        # Sleep in 1-second ticks so we can be cancelled cleanly
        for _ in range(wait):
            if not state["loop_active"]:
                break
            time.sleep(1)

        if not state["loop_active"]:
            break

        # Small pre-action pause like a human hesitating
        _human_delay(300, 1200)
        appium_restart_song(driver)
        _human_delay(400, 900)
        appium_ensure_playing(driver)

        # Occasionally nudge volume like a human would
        if random.random() < 0.20:
            keycode = 24 if random.random() > 0.5 else 25  # VOL_UP or VOL_DOWN
            taps = random.randint(1, 3)
            for _ in range(taps):
                driver.press_keycode(keycode)
                time.sleep(random.uniform(0.3, 0.7))
            emit(f"Volume adjusted ({'up' if keycode == 24 else 'down'} x{taps})")

    state["loop_active"] = False
    state["loop_next_restart"] = None
    emit("Loop stopped")


# ═══════════════════════════════════════════
# API ROUTES
# ═══════════════════════════════════════════

@app.route("/api/devices")
def api_devices():
    devs = refresh_devices()
    return jsonify(devs)

@app.route("/api/select", methods=["POST"])
def api_select():
    serial = request.json.get("serial")
    state["selected"] = serial
    emit(f"Selected device: {serial}")
    return jsonify({"ok": True})

@app.route("/api/spotify/launch", methods=["POST"])
def api_launch():
    s = state["selected"]
    if not s:
        return jsonify({"ok": False, "error": "No device selected"})
    out, code = adb("shell", "am", "start", "-n",
                    f"{SPOTIFY_PACKAGE}/{SPOTIFY_ACTIVITY}", serial=s)
    emit(f"Spotify launching on {s}...")
    return jsonify({"ok": code == 0})

@app.route("/api/spotify/stop", methods=["POST"])
def api_stop():
    s = state["selected"]
    if not s:
        return jsonify({"ok": False, "error": "No device selected"})
    adb("shell", "am", "force-stop", SPOTIFY_PACKAGE, serial=s)
    emit(f"Spotify stopped on {s}")
    return jsonify({"ok": True})

@app.route("/api/scrcpy/launch", methods=["POST"])
def api_scrcpy():
    columns = int(request.json.get("columns", 2))
    size    = int(request.json.get("size", 800))
    devs    = state["devices"]
    if not devs:
        return jsonify({"ok": False, "error": "No devices found"})
    for i, d in enumerate(devs):
        serial = d["serial"]
        if serial in state["scrcpy_procs"]:
            continue
        col, row = i % columns, i // columns
        proc = launch_scrcpy_for(serial, x=col*(size+10), y=row*(size+40), max_size=size)
        if proc:
            state["scrcpy_procs"][serial] = proc
    return jsonify({"ok": True, "launched": list(state["scrcpy_procs"].keys())})

@app.route("/api/scrcpy/stop", methods=["POST"])
def api_scrcpy_stop():
    for serial, proc in state["scrcpy_procs"].items():
        proc.terminate()
        emit(f"scrcpy stopped for {serial}")
    state["scrcpy_procs"].clear()
    return jsonify({"ok": True})

@app.route("/api/proxy/set", methods=["POST"])
def api_proxy_set():
    data  = request.json
    host  = data.get("host", "")
    port  = int(data.get("port", 10000))
    user  = data.get("user", "")
    pwd   = data.get("password", "")
    s     = state["selected"]
    if not s:
        return jsonify({"ok": False, "error": "No device selected"})

    actual_host, actual_port = host, port

    if user and pwd:
        # start local mitmproxy tunnel for auth
        proc = start_mitm_tunnel(host, port, user, pwd, local_port=8118)
        if proc:
            state["proxy_proc"] = proc
            actual_host, actual_port = "127.0.0.1", 8118

    set_device_proxy(s, actual_host, actual_port)
    state["proxy_active"] = True
    return jsonify({"ok": True})

@app.route("/api/proxy/clear", methods=["POST"])
def api_proxy_clear():
    s = state["selected"]
    if s:
        clear_device_proxy(s)
    if state["proxy_proc"]:
        state["proxy_proc"].terminate()
        state["proxy_proc"] = None
    state["proxy_active"] = False
    return jsonify({"ok": True})

@app.route("/api/appium/loop/start", methods=["POST"])
def api_loop_start():
    if state["loop_active"]:
        return jsonify({"ok": False, "error": "Loop already running"})
    if not state["appium_driver"]:
        return jsonify({"ok": False, "error": "Connect Appium first"})
    data    = request.json
    query   = data.get("query", "").strip()
    min_sec = int(data.get("min_sec", 40))
    max_sec = int(data.get("max_sec", 60))
    if not query:
        return jsonify({"ok": False, "error": "Playlist query is empty"})
    state["loop_active"] = True
    t = threading.Thread(target=_loop_worker, args=(query, min_sec, max_sec), daemon=True)
    state["loop_thread"] = t
    t.start()
    return jsonify({"ok": True})

@app.route("/api/appium/loop/stop", methods=["POST"])
def api_loop_stop():
    state["loop_active"] = False
    state["loop_next_restart"] = None
    emit("Loop stop requested")
    return jsonify({"ok": True})

@app.route("/api/appium/loop/status")
def api_loop_status():
    nxt = state["loop_next_restart"]
    remaining = max(0, int(nxt - time.time())) if nxt else None
    return jsonify({
        "active":    state["loop_active"],
        "remaining": remaining,
    })


@app.route("/api/appium/connect", methods=["POST"])
def api_appium_connect():
    import traceback
    s = state["selected"]
    if not s:
        return jsonify({"ok": False, "error": "No device selected"})
    try:
        driver = connect_appium(s)
        if driver:
            state["appium_driver"] = driver
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": "Failed — is Appium server running?"})
    except Exception as e:
        print(traceback.format_exc())
        return jsonify({"ok": False, "error": str(e)})

@app.route("/api/appium/search", methods=["POST"])
def api_appium_search():
    from appium.webdriver.common.appiumby import AppiumBy
    driver = state["appium_driver"]
    if not driver:
        return jsonify({"ok": False, "error": "Appium not connected"})
    query = request.json.get("query", "")
    try:
        driver.find_element(AppiumBy.ACCESSIBILITY_ID, "Search").click()
        time.sleep(1)
        box = driver.find_element(AppiumBy.ID, "com.spotify.music:id/query")
        box.send_keys(query)
        emit(f"Searched: {query}")
        return jsonify({"ok": True})
    except Exception as e:
        emit(f"Search error: {e}", "error")
        return jsonify({"ok": False, "error": str(e)})

@app.route("/api/appium/playpause", methods=["POST"])
def api_appium_playpause():
    from appium.webdriver.common.appiumby import AppiumBy
    driver = state["appium_driver"]
    if not driver:
        return jsonify({"ok": False, "error": "Appium not connected"})
    try:
        driver.find_element(AppiumBy.ACCESSIBILITY_ID, "Play").click()
        emit("Play/Pause tapped")
        return jsonify({"ok": True})
    except Exception as e:
        emit(f"Play/Pause error: {e}", "error")
        return jsonify({"ok": False, "error": str(e)})

@app.route("/api/adb/command", methods=["POST"])
def api_adb_raw():
    """Run an arbitrary adb shell command."""
    cmd_str = request.json.get("command", "")
    s = state["selected"]
    if not cmd_str:
        return jsonify({"ok": False, "error": "Empty command"})
    parts = cmd_str.split()
    out, code = adb("shell", *parts, serial=s)
    emit(f"$ adb shell {cmd_str}\n  → {out or '(no output)'}")
    return jsonify({"ok": code == 0, "output": out})

@app.route("/api/logs")
def api_logs():
    """SSE stream of log messages."""
    def generate():
        while True:
            try:
                item = log_queue.get(timeout=30)
                yield f"data: {json.dumps(item)}\n\n"
            except queue.Empty:
                yield "data: {\"msg\":\"\",\"level\":\"ping\"}\n\n"
    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/wireguard/generate", methods=["POST"])
def api_wireguard_generate():
    import base64
    data          = request.json
    server_host   = data.get("host", "")
    server_port   = int(data.get("port", 51820))
    server_pubkey = data.get("server_pubkey", "")
    allowed_ips   = data.get("allowed_ips", "0.0.0.0/0, ::/0")
    dns           = data.get("dns", "1.1.1.1")

    if not server_host or not server_pubkey:
        return jsonify({"ok": False, "error": "Server host and public key are required"})

    try:
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
        from cryptography.hazmat.primitives.serialization import (
            Encoding, PublicFormat, PrivateFormat, NoEncryption
        )
        priv_obj   = X25519PrivateKey.generate()
        priv_bytes = priv_obj.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        pub_bytes  = priv_obj.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        priv_b64   = base64.b64encode(priv_bytes).decode()
        pub_b64    = base64.b64encode(pub_bytes).decode()
    except ImportError:
        return jsonify({"ok": False, "error": "Run: pip install cryptography"})

    lines = [
        "[Interface]",
        "PrivateKey = " + priv_b64,
        "DNS = " + dns,
        "",
        "[Peer]",
        "PublicKey = " + server_pubkey,
        "Endpoint = " + server_host + ":" + str(server_port),
        "AllowedIPs = " + allowed_ips,
        "PersistentKeepalive = 25",
    ]
    config = "\n".join(lines) + "\n"
    emit("WireGuard config generated — client pubkey: " + pub_b64)
    return jsonify({"ok": True, "config": config, "client_pubkey": pub_b64})

@app.route("/api/status")
def api_status():
    return jsonify({
        "selected":     state["selected"],
        "proxy_active": state["proxy_active"],
        "scrcpy_open":  list(state["scrcpy_procs"].keys()),
        "appium":       state["appium_driver"] is not None,
    })

# ── Check tools ──────────────────────────────
@app.route("/api/tools")
def api_tools():
    return jsonify({
        "adb":     bool(shutil.which("adb")),
        "scrcpy":  bool(shutil.which("scrcpy")),
        "appium":  bool(shutil.which("appium")),
        "mitmdump": bool(shutil.which("mitmdump")),
    })

# ── Serve the single-page UI ─────────────────
@app.route("/")
def index():
    return UI_HTML

# ═══════════════════════════════════════════
# EMBEDDED UI
# ═══════════════════════════════════════════
UI_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Droid Spotify Controller</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@300;400;600&family=Space+Grotesk:wght@400;600;700&display=swap" rel="stylesheet">
<style>
  :root {
    --bg:       #0a0c0f;
    --surface:  #111418;
    --surface2: #181c22;
    --border:   #222830;
    --green:    #1db954;
    --green-dim:#0e7a35;
    --red:      #e53e3e;
    --yellow:   #f6c90e;
    --blue:     #4da6ff;
    --text:     #d4dae3;
    --muted:    #5a6475;
    --mono:     'IBM Plex Mono', monospace;
    --sans:     'Space Grotesk', sans-serif;
  }
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--sans);
    min-height: 100vh;
    display: grid;
    grid-template-rows: 56px 1fr;
    grid-template-columns: 280px 1fr 320px;
    grid-template-areas:
      "header header header"
      "sidebar main logs";
  }

  /* ── Header ── */
  header {
    grid-area: header;
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    padding: 0 24px;
    gap: 16px;
  }
  .logo { display:flex; align-items:center; gap:10px; }
  .logo svg { color: var(--green); }
  .logo-text { font-family: var(--mono); font-size: 15px; font-weight: 600; letter-spacing: .04em; }
  .logo-sub { font-family: var(--mono); font-size: 11px; color: var(--muted); margin-left:2px; }

  .tool-chips { display:flex; gap:8px; margin-left:auto; }
  .chip {
    font-family: var(--mono); font-size: 11px;
    padding: 3px 9px; border-radius: 999px;
    border: 1px solid var(--border);
    display:flex; align-items:center; gap:5px;
  }
  .chip .dot { width:6px; height:6px; border-radius:50%; background:var(--muted); }
  .chip.ok .dot  { background: var(--green); box-shadow: 0 0 6px var(--green); }
  .chip.bad .dot { background: var(--red); }

  /* ── Sidebar ── */
  aside {
    grid-area: sidebar;
    background: var(--surface);
    border-right: 1px solid var(--border);
    display: flex; flex-direction: column;
    overflow-y: auto;
  }
  .section-head {
    padding: 14px 18px 8px;
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: .12em;
    color: var(--muted);
    text-transform: uppercase;
    border-bottom: 1px solid var(--border);
  }

  /* Device cards */
  .device-list { padding: 10px; display:flex; flex-direction:column; gap:6px; }
  .device-card {
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 10px 12px;
    cursor: pointer;
    transition: border-color .15s, background .15s;
    display:flex; flex-direction:column; gap:3px;
  }
  .device-card:hover   { border-color: var(--green-dim); }
  .device-card.active  { border-color: var(--green); background: rgba(29,185,84,.06); }
  .device-card .dname  { font-size:13px; font-weight:600; }
  .device-card .dserial{ font-family:var(--mono); font-size:10px; color:var(--muted); }
  .device-card .dandroid{ font-size:11px; color:var(--muted); }
  .no-device { padding:20px 18px; font-size:13px; color:var(--muted); line-height:1.6; }
  .refresh-btn {
    margin:10px; padding:7px 0;
    background:transparent; border:1px solid var(--border); border-radius:6px;
    color:var(--text); font-family:var(--sans); font-size:13px; cursor:pointer;
    transition: border-color .15s, color .15s;
  }
  .refresh-btn:hover { border-color:var(--green); color:var(--green); }

  /* Status badge */
  .status-grid { padding:12px 14px; display:grid; grid-template-columns:1fr 1fr; gap:6px; }
  .status-item {
    background:var(--surface2); border:1px solid var(--border);
    border-radius:6px; padding:8px 10px;
    display:flex; flex-direction:column; gap:2px;
  }
  .status-item .s-label { font-size:10px; color:var(--muted); font-family:var(--mono); }
  .status-item .s-val   { font-size:12px; font-weight:600; }
  .s-val.on  { color:var(--green); }
  .s-val.off { color:var(--muted); }

  /* ── Main ── */
  main {
    grid-area: main;
    overflow-y: auto;
    padding: 20px 24px;
    display: flex; flex-direction: column; gap: 20px;
  }
  .panel {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    overflow: hidden;
  }
  .panel-head {
    padding: 14px 20px;
    border-bottom: 1px solid var(--border);
    display:flex; align-items:center; gap:10px;
  }
  .panel-head h2 { font-size:14px; font-weight:600; }
  .panel-head .tag {
    margin-left:auto;
    font-family:var(--mono); font-size:10px;
    padding:2px 8px; border-radius:999px;
    border:1px solid var(--border); color:var(--muted);
  }
  .panel-body { padding: 18px 20px; display:flex; flex-direction:column; gap:14px; }

  /* Form fields */
  .form-row { display:flex; gap:10px; flex-wrap:wrap; }
  .field { display:flex; flex-direction:column; gap:5px; flex:1; min-width:140px; }
  .field label { font-size:11px; font-family:var(--mono); color:var(--muted); }
  .field input {
    background: var(--surface2); border:1px solid var(--border);
    border-radius:6px; padding:8px 10px;
    color:var(--text); font-family:var(--mono); font-size:13px;
    outline:none; transition:border-color .15s;
  }
  .field input:focus { border-color:var(--green); }

  /* Buttons */
  .btn-row { display:flex; gap:8px; flex-wrap:wrap; }
  .btn {
    padding: 9px 18px; border-radius:7px;
    font-family:var(--sans); font-size:13px; font-weight:600;
    cursor:pointer; border:none; transition:all .15s;
    display:flex; align-items:center; gap:7px;
  }
  .btn-green  { background:var(--green); color:#000; }
  .btn-green:hover  { background:#17a347; }
  .btn-red    { background:rgba(229,62,62,.15); color:var(--red); border:1px solid var(--red); }
  .btn-red:hover    { background:rgba(229,62,62,.25); }
  .btn-ghost  { background:transparent; color:var(--text); border:1px solid var(--border); }
  .btn-ghost:hover  { border-color:var(--green); color:var(--green); }
  .btn-blue   { background:rgba(77,166,255,.15); color:var(--blue); border:1px solid var(--blue); }
  .btn-blue:hover   { background:rgba(77,166,255,.25); }
  .btn:disabled { opacity:.4; cursor:not-allowed; }

  /* scrcpy grid config */
  .grid-preview {
    display:grid; gap:4px; padding:12px;
    background:var(--surface2); border-radius:8px; border:1px solid var(--border);
  }
  .grid-cell {
    background:var(--border); border-radius:4px; height:32px;
    display:flex; align-items:center; justify-content:center;
    font-family:var(--mono); font-size:10px; color:var(--muted);
  }

  /* ADB console */
  .adb-row { display:flex; gap:8px; }
  .adb-input {
    flex:1; background:var(--surface2); border:1px solid var(--border);
    border-radius:6px; padding:9px 12px;
    color:var(--green); font-family:var(--mono); font-size:13px;
    outline:none;
  }
  .adb-input:focus { border-color:var(--green); }
  .adb-input::placeholder { color:var(--muted); }

  /* ── Log panel ── */
  #log-panel {
    grid-area: logs;
    background: var(--surface);
    border-left: 1px solid var(--border);
    display:flex; flex-direction:column;
  }
  .log-head {
    padding: 14px 16px;
    border-bottom: 1px solid var(--border);
    display:flex; align-items:center; gap:8px;
    font-family:var(--mono); font-size:12px; font-weight:600;
  }
  .log-clear { margin-left:auto; font-size:11px; color:var(--muted); cursor:pointer; }
  .log-clear:hover { color:var(--text); }
  #log-output {
    flex:1; overflow-y:auto; padding:12px 14px;
    font-family:var(--mono); font-size:11.5px; line-height:1.7;
    display:flex; flex-direction:column; gap:1px;
  }
  .log-line { display:flex; gap:8px; }
  .log-ts   { color:var(--muted); flex-shrink:0; }
  .log-msg  { word-break:break-all; }
  .log-line.info  .log-msg { color:var(--text); }
  .log-line.error .log-msg { color:var(--red); }
  .log-line.warning .log-msg { color:var(--yellow); }
  .log-line.ping { display:none; }

  /* Animations */
  @keyframes pulse-green {
    0%,100% { opacity:1; } 50% { opacity:.4; }
  }
  .blink { animation: pulse-green 1.8s infinite; }

  /* Scrollbars */
  ::-webkit-scrollbar { width:5px; }
  ::-webkit-scrollbar-track { background:transparent; }
  ::-webkit-scrollbar-thumb { background:var(--border); border-radius:99px; }
</style>
</head>
<body>

<!-- ── Header ── -->
<header>
  <div class="logo">
    <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor">
      <path d="M12 2C6.477 2 2 6.477 2 12s4.477 10 10 10 10-4.477 10-10S17.523 2 12 2zm4.586 14.424a.622.622 0 01-.857.208c-2.348-1.435-5.304-1.76-8.785-.964a.622.622 0 11-.277-1.215c3.809-.87 7.076-.496 9.712 1.115a.623.623 0 01.207.856zm1.223-2.722a.78.78 0 01-1.072.257C14.3 12.267 10.85 11.73 8.085 12.56a.78.78 0 01-.453-1.492c3.1-.942 6.954-.485 9.624 1.063a.78.78 0 01.553 1.571zm.105-2.835C15.012 9.02 9.818 8.847 6.94 9.744A.935.935 0 116.36 7.94c3.29-1.001 8.76-.808 12.212 1.268a.936.936 0 01-1.658.659z"/>
    </svg>
    <div>
      <div class="logo-text">DROID<span style="color:var(--green)">CTRL</span></div>
      <div class="logo-sub">Spotify Android Automation</div>
    </div>
  </div>
  <div class="tool-chips" id="tool-chips"></div>
</header>

<!-- ── Sidebar ── -->
<aside>
  <div class="section-head">Connected Devices</div>
  <div class="device-list" id="device-list">
    <div class="no-device">No devices found.<br>Connect via USB with debugging enabled.</div>
  </div>
  <button class="refresh-btn" onclick="refreshDevices()">⟳ Refresh Devices</button>

  <div class="section-head" style="margin-top:auto">Status</div>
  <div class="status-grid" id="status-grid">
    <div class="status-item"><div class="s-label">Device</div><div class="s-val off" id="st-device">none</div></div>
    <div class="status-item"><div class="s-label">Proxy</div><div class="s-val off" id="st-proxy">off</div></div>
    <div class="status-item"><div class="s-label">scrcpy</div><div class="s-val off" id="st-scrcpy">off</div></div>
    <div class="status-item"><div class="s-label">Appium</div><div class="s-val off" id="st-appium">off</div></div>
  </div>
</aside>

<!-- ── Main ── -->
<main>

  <!-- Spotify Controls -->
  <div class="panel">
    <div class="panel-head">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="var(--green)"><path d="M12 2C6.477 2 2 6.477 2 12s4.477 10 10 10 10-4.477 10-10S17.523 2 12 2z"/></svg>
      <h2>Spotify Controls</h2>
      <span class="tag">adb am start</span>
    </div>
    <div class="panel-body">
      <div class="btn-row">
        <button class="btn btn-green" onclick="api('/api/spotify/launch','POST')">
          ▶ Launch Spotify
        </button>
        <button class="btn btn-red" onclick="api('/api/spotify/stop','POST')">
          ⏹ Force Stop
        </button>
      </div>
    </div>
  </div>

  <!-- Screen Mirror -->
  <div class="panel">
    <div class="panel-head">
      <h2>Screen Mirror — scrcpy Grid</h2>
      <span class="tag">scrcpy</span>
    </div>
    <div class="panel-body">
      <div class="form-row">
        <div class="field">
          <label>Grid Columns</label>
          <input id="scrcpy-cols" type="number" value="2" min="1" max="6">
        </div>
        <div class="field">
          <label>Max Window Size (px)</label>
          <input id="scrcpy-size" type="number" value="800" min="300" max="1920">
        </div>
      </div>
      <div class="grid-preview" id="grid-preview" style="grid-template-columns: repeat(2, 1fr)">
        <div class="grid-cell">device 1</div>
        <div class="grid-cell">device 2</div>
      </div>
      <div class="btn-row">
        <button class="btn btn-blue" onclick="launchScrcpy()">⊞ Launch Grid</button>
        <button class="btn btn-red"  onclick="api('/api/scrcpy/stop','POST')">✕ Close All</button>
      </div>
    </div>
  </div>

  <!-- Proxy -->
  <div class="panel">
    <div class="panel-head">
      <h2>Residential Proxy</h2>
      <span class="tag">mitmproxy tunnel</span>
    </div>
    <div class="panel-body">
      <div class="form-row">
        <div class="field" style="flex:2">
          <label>Proxy Host</label>
          <input id="p-host" type="text" placeholder="gate.smartproxy.com">
        </div>
        <div class="field" style="flex:1">
          <label>Port</label>
          <input id="p-port" type="number" value="10000">
        </div>
      </div>
      <div class="form-row">
        <div class="field">
          <label>Username (optional)</label>
          <input id="p-user" type="text" placeholder="proxy_user">
        </div>
        <div class="field">
          <label>Password (optional)</label>
          <input id="p-pass" type="password" placeholder="••••••">
        </div>
      </div>
      <div class="btn-row">
        <button class="btn btn-green" onclick="setProxy()">⇢ Apply Proxy</button>
        <button class="btn btn-ghost" onclick="api('/api/proxy/clear','POST')">✕ Clear Proxy</button>
      </div>
    </div>
  </div>

  <!-- Appium -->
  <div class="panel">
    <div class="panel-head">
      <h2>Appium Automation</h2>
      <span class="tag">appium server: 4723</span>
    </div>
    <div class="panel-body">
      <div class="btn-row">
        <button class="btn btn-blue" onclick="api('/api/appium/connect','POST')">⚡ Connect Appium</button>
        <button class="btn btn-ghost" onclick="api('/api/appium/playpause','POST')">⏯ Play / Pause</button>
      </div>

      <!-- Playlist Loop -->
      <div style="background:var(--surface2);border:1px solid var(--border);border-radius:10px;padding:14px 16px;display:flex;flex-direction:column;gap:12px;">
        <div style="font-size:12px;font-weight:600;color:var(--green);font-family:var(--mono);letter-spacing:.06em;">PLAYLIST LOOP</div>

        <div class="field">
          <label>Playlist / Search Query</label>
          <input id="loop-query" type="text" placeholder="Lofi Hip Hop Radio">
        </div>

        <div class="form-row">
          <div class="field">
            <label>Min seconds before restart</label>
            <input id="loop-min" type="number" value="40" min="5" max="3600">
          </div>
          <div class="field">
            <label>Max seconds before restart</label>
            <input id="loop-max" type="number" value="60" min="5" max="3600">
          </div>
        </div>

        <!-- Countdown display -->
        <div id="loop-status-bar" style="display:none;background:var(--bg);border:1px solid var(--border);border-radius:7px;padding:10px 14px;">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:7px;">
            <span style="font-family:var(--mono);font-size:11px;color:var(--green);">● LOOP RUNNING</span>
            <span style="font-family:var(--mono);font-size:11px;color:var(--muted);">next restart in</span>
          </div>
          <div style="display:flex;align-items:baseline;gap:6px;">
            <span id="loop-countdown" style="font-family:var(--mono);font-size:28px;font-weight:600;color:var(--text);">--</span>
            <span style="font-family:var(--mono);font-size:13px;color:var(--muted);">seconds</span>
          </div>
          <div style="margin-top:8px;height:3px;background:var(--border);border-radius:99px;overflow:hidden;">
            <div id="loop-progress-bar" style="height:100%;background:var(--green);width:100%;transition:width 1s linear;border-radius:99px;"></div>
          </div>
        </div>

        <div class="btn-row">
          <button id="loop-start-btn" class="btn btn-green" onclick="startLoop()">▶ Start Loop</button>
          <button id="loop-stop-btn"  class="btn btn-red"   onclick="stopLoop()" style="display:none">⏹ Stop Loop</button>
        </div>
      </div>
    </div>
  </div>

  <!-- WireGuard -->
  <div class="panel">
    <div class="panel-head">
      <h2>WireGuard Config Generator</h2>
      <span class="tag">routes all app traffic</span>
    </div>
    <div class="panel-body">
      <div style="font-size:12px;color:var(--muted);line-height:1.6;padding:8px 12px;background:var(--surface2);border-radius:7px;border:1px solid var(--border);">
        Generates a WireGuard config you can import directly into the <strong>WireGuard Android app</strong>.
        Routes all device traffic (including Spotify) through your proxy at the network level.
      </div>
      <div class="form-row">
        <div class="field" style="flex:2">
          <label>Server Host / IP</label>
          <input id="wg-host" type="text" placeholder="vpn.yourprovider.com">
        </div>
        <div class="field" style="flex:1">
          <label>Port</label>
          <input id="wg-port" type="number" value="51820">
        </div>
      </div>
      <div class="field">
        <label>Server Public Key</label>
        <input id="wg-pubkey" type="text" placeholder="base64 public key from your provider">
      </div>
      <div class="form-row">
        <div class="field">
          <label>DNS</label>
          <input id="wg-dns" type="text" value="1.1.1.1">
        </div>
        <div class="field">
          <label>Allowed IPs</label>
          <input id="wg-allowed" type="text" value="0.0.0.0/0, ::/0">
        </div>
      </div>
      <div class="btn-row">
        <button class="btn btn-blue" onclick="generateWireguard()">⚙ Generate Config</button>
      </div>
      <div id="wg-output" style="display:none;flex-direction:column;gap:8px;">
        <div style="font-size:11px;font-family:var(--mono);color:var(--muted);">CONFIG FILE — import this into WireGuard Android app</div>
        <textarea id="wg-config-text" readonly style="background:var(--bg);border:1px solid var(--border);border-radius:7px;padding:10px 12px;color:var(--green);font-family:var(--mono);font-size:11px;resize:vertical;min-height:160px;width:100%;outline:none;"></textarea>
        <div style="font-size:11px;color:var(--muted);font-family:var(--mono);" id="wg-pubkey-out"></div>
        <button class="btn btn-ghost" onclick="copyWgConfig()" style="align-self:flex-start;">Copy Config</button>
      </div>
    </div>
  </div>

  <!-- ADB Console -->
  <div class="panel">
    <div class="panel-head">
      <h2>ADB Shell Console</h2>
      <span class="tag">adb shell</span>
    </div>
    <div class="panel-body">
      <div class="adb-row">
        <input class="adb-input" id="adb-cmd" placeholder="input keyevent 85  /  am broadcast ..." onkeydown="if(event.key==='Enter')runAdb()">
        <button class="btn btn-green" onclick="runAdb()">Run</button>
      </div>
    </div>
  </div>

</main>

<!-- ── Log Panel ── -->
<div id="log-panel">
  <div class="log-head">
    <svg width="12" height="12" viewBox="0 0 12 12" fill="var(--green)"><circle cx="6" cy="6" r="5" class="blink"/></svg>
    LIVE LOG
    <span class="log-clear" onclick="document.getElementById('log-output').innerHTML=''">clear</span>
  </div>
  <div id="log-output"></div>
</div>

<script>
const $ = id => document.getElementById(id);

// ── API helper ─────────────────────────────
async function api(url, method='GET', body=null) {
  try {
    const opts = { method, headers: {'Content-Type':'application/json'} };
    if (body) opts.body = JSON.stringify(body);
    const r = await fetch(url, opts);
    const d = await r.json();
    if (!d.ok && d.error) logLine(d.error, 'error');
    updateStatus();
    return d;
  } catch(e) {
    logLine(`Request failed: ${e.message}`, 'error');
  }
}

// ── Devices ───────────────────────────────
async function refreshDevices() {
  const devs = await api('/api/devices');
  const list = $('device-list');
  if (!devs || !devs.length) {
    list.innerHTML = '<div class="no-device">No devices found.<br>Connect via USB with debugging enabled.</div>';
    return;
  }
  list.innerHTML = devs.map(d => `
    <div class="device-card" id="card-${d.serial}" onclick="selectDevice('${d.serial}')">
      <div class="dname">${d.model}</div>
      <div class="dserial">${d.serial}</div>
      <div class="dandroid">Android ${d.android}</div>
    </div>`).join('');
  updateGridPreview();
}

async function selectDevice(serial) {
  await api('/api/select','POST',{serial});
  document.querySelectorAll('.device-card').forEach(c=>c.classList.remove('active'));
  const card = $('card-'+serial);
  if(card) card.classList.add('active');
}

// ── scrcpy ────────────────────────────────
function updateGridPreview() {
  const cols = parseInt($('scrcpy-cols').value)||2;
  const devs = document.querySelectorAll('.device-card').length || 2;
  const prev = $('grid-preview');
  prev.style.gridTemplateColumns = `repeat(${cols}, 1fr)`;
  prev.innerHTML = Array.from({length:devs}, (_,i)=>`<div class="grid-cell">device ${i+1}</div>`).join('');
}

async function launchScrcpy() {
  await api('/api/scrcpy/launch','POST',{
    columns: parseInt($('scrcpy-cols').value)||2,
    size: parseInt($('scrcpy-size').value)||800
  });
}

// ── Proxy ─────────────────────────────────
async function setProxy() {
  await api('/api/proxy/set','POST',{
    host: $('p-host').value,
    port: parseInt($('p-port').value)||10000,
    user: $('p-user').value,
    password: $('p-pass').value,
  });
}

// ── Appium loop ───────────────────────────
let loopPollInterval = null;
let loopTotalSecs = 60;

async function startLoop() {
  const query  = $('loop-query').value.trim();
  const minSec = parseInt($('loop-min').value) || 40;
  const maxSec = parseInt($('loop-max').value) || 60;
  if (!query) { logLine('Enter a playlist name first', 'warning'); return; }
  loopTotalSecs = maxSec;
  const d = await api('/api/appium/loop/start', 'POST', { query, min_sec: minSec, max_sec: maxSec });
  if (d && d.ok) {
    $('loop-start-btn').style.display = 'none';
    $('loop-stop-btn').style.display  = '';
    $('loop-status-bar').style.display = '';
    startLoopPoll();
  }
}

async function stopLoop() {
  await api('/api/appium/loop/stop', 'POST');
  $('loop-start-btn').style.display = '';
  $('loop-stop-btn').style.display  = 'none';
  $('loop-status-bar').style.display = 'none';
  stopLoopPoll();
}

function startLoopPoll() {
  if (loopPollInterval) clearInterval(loopPollInterval);
  loopPollInterval = setInterval(async () => {
    const s = await fetch('/api/appium/loop/status').then(r=>r.json()).catch(()=>null);
    if (!s) return;
    if (!s.active) { stopLoop(); return; }
    const rem = s.remaining ?? 0;
    $('loop-countdown').textContent = rem;
    // progress bar shrinks from 100% → 0% as time elapses
    const pct = loopTotalSecs > 0 ? (rem / loopTotalSecs) * 100 : 0;
    $('loop-progress-bar').style.width = pct + '%';
  }, 1000);
}

function stopLoopPoll() {
  if (loopPollInterval) { clearInterval(loopPollInterval); loopPollInterval = null; }
}

async function appiumSearch() {
  const q = $('loop-query') ? $('loop-query').value.trim() : '';
  if(!q) return;
  await api('/api/appium/search','POST',{query:q});
}



// ── WireGuard ─────────────────────────────
async function generateWireguard() {
  const d = await api('/api/wireguard/generate', 'POST', {
    host:        $('wg-host').value.trim(),
    port:        parseInt($('wg-port').value) || 51820,
    server_pubkey: $('wg-pubkey').value.trim(),
    dns:         $('wg-dns').value.trim(),
    allowed_ips: $('wg-allowed').value.trim(),
  });
  if (d && d.ok) {
    $('wg-config-text').value = d.config;
    $('wg-pubkey-out').textContent = 'Your client public key (give this to your provider): ' + d.client_pubkey;
    $('wg-output').style.display = 'flex';
  }
}

function copyWgConfig() {
  navigator.clipboard.writeText($('wg-config-text').value);
  logLine('WireGuard config copied to clipboard');
}

// ── ADB console ───────────────────────────
async function runAdb() {
  const cmd = $('adb-cmd').value.trim();
  if(!cmd) return;
  const d = await api('/api/adb/command','POST',{command:cmd});
  $('adb-cmd').value='';
}

// ── Status ────────────────────────────────
async function updateStatus() {
  const s = await api('/api/status');
  if(!s) return;
  const set = (id, val, on) => {
    const el = $(id);
    el.textContent = val;
    el.className = 's-val ' + (on ? 'on' : 'off');
  };
  set('st-device', s.selected ? s.selected.slice(-6) : 'none', !!s.selected);
  set('st-proxy',  s.proxy_active ? 'on' : 'off', s.proxy_active);
  set('st-scrcpy', s.scrcpy_open.length ? s.scrcpy_open.length+'x' : 'off', s.scrcpy_open.length>0);
  set('st-appium', s.appium ? 'on' : 'off', s.appium);
}

async function checkTools() {
  const t = await api('/api/tools');
  if(!t) return;
  const chips = $('tool-chips');
  chips.innerHTML = Object.entries(t).map(([k,v])=>`
    <div class="chip ${v?'ok':'bad'}">
      <div class="dot"></div>${k}
    </div>`).join('');
}

// ── Log SSE ───────────────────────────────
function logLine(msg, level='info') {
  const out = $('log-output');
  const now = new Date().toLocaleTimeString('en',{hour12:false,hour:'2-digit',minute:'2-digit',second:'2-digit'});
  const el = document.createElement('div');
  el.className = `log-line ${level}`;
  el.innerHTML = `<span class="log-ts">${now}</span><span class="log-msg">${msg}</span>`;
  out.appendChild(el);
  out.scrollTop = out.scrollHeight;
}

const evtSrc = new EventSource('/api/logs');
evtSrc.onmessage = e => {
  const d = JSON.parse(e.data);
  if(d.level !== 'ping') logLine(d.msg, d.level);
};

// ── Grid preview inputs ────────────────────
$('scrcpy-cols').addEventListener('input', updateGridPreview);

// ── Init ──────────────────────────────────
(async()=>{
  await checkTools();
  await refreshDevices();
  await updateStatus();
  logLine('DroidCtrl ready', 'info');
})();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    print("\n  DroidCtrl — Spotify Android Automation")
    print("  Open: http://localhost:5050\n")
    app.run(host="127.0.0.1", port=5050, debug=False, threaded=True)
