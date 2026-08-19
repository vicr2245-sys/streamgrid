#!/usr/bin/env python3
"""
StreamGrid — Multi-Device Streaming Engine
Multi-device parallel Android control, human thumb engine, WireGuard residential IP tunneling, OLED battery saver & stealth mute.
"""

import subprocess, time, sys, os, threading, logging
import json, shutil, random, queue, webbrowser, math
from flask import Flask, jsonify, request, Response

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)
app = Flask(__name__)

# ===========================================
# PLATFORMS
# ===========================================
PLATFORMS = {
    "spotify": {"name":"Spotify","package":"com.spotify.music","activity":"com.spotify.music.MainActivity",
             "color":"#1db954","search_id":"com.spotify.music:id/query",
             "login_id":"com.spotify.music:id/username_text","password_id":"com.spotify.music:id/password_text",
             "login_btn":"com.spotify.music:id/login_button","seekbar":"com.spotify.music:id/seekbar",
             "search_label":"Search","play_label":"Play"},
    "tidal": {"name":"Tidal","package":"com.aspiro.tidal","activity":"com.aspiro.tidal.activities.MainActivity",
             "color":"#00ffff","search_id":"com.aspiro.tidal:id/search_edittext",
             "login_id":"com.aspiro.tidal:id/email_edittext","password_id":"com.aspiro.tidal:id/password_edittext",
             "login_btn":"com.aspiro.tidal:id/login_button","seekbar":"com.aspiro.tidal:id/progress_bar",
             "search_label":"Search","play_label":"Play"},
    "apple_music": {"name":"Apple Music","package":"com.apple.android.music","activity":"com.apple.android.music.MainActivity",
             "color":"#fc3c44","search_id":"com.apple.android.music:id/search_edit_text",
             "login_id":"com.apple.android.music:id/apple_id_field","password_id":"com.apple.android.music:id/password_field",
             "login_btn":"com.apple.android.music:id/sign_in_button","seekbar":"com.apple.android.music:id/playback_scrubber",
             "search_label":"Search","play_label":"Play"},
    "youtube_music": {"name":"YouTube Music","package":"com.google.android.apps.youtube.music","activity":"com.google.android.apps.youtube.music.activities.MusicActivity",
             "color":"#ff0000","search_id":"com.google.android.apps.youtube.music:id/search_edit_text",
             "login_id":"com.google.android.gms:id/account_name_text","password_id":"com.google.android.gms:id/password",
             "login_btn":"com.google.android.gms:id/next_button","seekbar":"com.google.android.apps.youtube.music:id/player_seekbar",
             "search_label":"Search","play_label":"Play"},
    "deezer": {"name":"Deezer","package":"deezer.android.app","activity":"deezer.android.app.activities.LauncherActivity",
             "color":"#a238ff","search_id":"deezer.android.app:id/search_input",
             "login_id":"deezer.android.app:id/email_input","password_id":"deezer.android.app:id/password_input",
             "login_btn":"deezer.android.app:id/btn_login","seekbar":"deezer.android.app:id/seekbar",
             "search_label":"Search","play_label":"Play"},
    "amazon_music": {"name":"Amazon Music","package":"com.amazon.mp3","activity":"com.amazon.mp3.activity.IntegratedPlayerActivity",
             "color":"#25d1da","search_id":"com.amazon.mp3:id/search_src_text",
             "login_id":"com.amazon.mp3:id/ap_email","password_id":"com.amazon.mp3:id/ap_password",
             "login_btn":"com.amazon.mp3:id/signInSubmit","seekbar":"com.amazon.mp3:id/player_progressbar",
             "search_label":"Search","play_label":"Play"},
}

APPIUM_SERVER       = "http://127.0.0.1:4723"
VAULT_FILE          = "accounts.json"
RECONNECT_INTERVAL  = 30
RECONNECT_RETRIES   = 3

# ===========================================
# STATE
# ===========================================
state = {
    "devices":      [],
    "selected":     None,
    "platform":     "spotify",
    "proxy_active": False,
    "proxy_proc":   None,
    "scrcpy_procs": {},
    "drivers":      {},
    "loops":        {},
    "watchdogs":    {},
    "appium_server_proc": None,
}
log_queue = queue.Queue()

def emit(msg, level="info"):
    log_queue.put({"msg": msg, "level": level})
    getattr(log, level)(msg)

def plat():
    return PLATFORMS.get(state["platform"], PLATFORMS["spotify"])

# ===========================================
# VAULT
# ===========================================
def load_vault():
    if os.path.exists(VAULT_FILE):
        with open(VAULT_FILE) as f: return json.load(f)
    return []

def save_vault(v):
    with open(VAULT_FILE, "w") as f: json.dump(v, f, indent=2)

CREATE_NO_WINDOW = 0x08000000 if os.name == 'nt' else 0

# ===========================================
# STEALTH HARDWARE (OLED DIMMER & MUTE)
# ===========================================
def apply_stealth_hardware_settings(serial):
    """
    Dims screen to 1 (minimum OLED brightness) and mutes media stream to 0.
    Prevents screen burn-in, saves ~70% battery, and keeps room silent while streaming.
    """
    try:
        adb("shell", "settings", "put", "system", "screen_brightness_mode", "0", serial=serial)
        adb("shell", "settings", "put", "system", "screen_brightness", "1", serial=serial)
        adb("shell", "media", "volume", "--stream", "3", "--set", "0", serial=serial)
        adb("shell", "cmd", "media_session", "volume", "--stream", "3", "--set", "0", serial=serial)
        emit(f"[{serial[-6:]}] OLED Dimmer (Brightness: 1) & Stealth Mute (Volume: 0) APPLIED")
    except Exception as e:
        emit(f"[{serial[-6:]}] Stealth hardware notice: {e}", "warning")

def restore_hardware_settings(serial):
    """
    Restores screen brightness back to normal.
    """
    try:
        adb("shell", "settings", "put", "system", "screen_brightness_mode", "1", serial=serial)
        adb("shell", "settings", "put", "system", "screen_brightness", "128", serial=serial)
        emit(f"[{serial[-6:]}] Screen brightness restored to normal")
    except Exception as e:
        pass

# ===========================================
# ADB
# ===========================================
def adb(*args, serial=None):
    prefix = ["-s", serial] if serial else []
    try:
        kwargs = {"creationflags": CREATE_NO_WINDOW} if os.name == 'nt' else {}
        r = subprocess.run(["adb"] + prefix + list(args),
                           capture_output=True, text=True, timeout=30, **kwargs)
        return r.stdout.strip(), r.returncode
    except FileNotFoundError:
        emit("adb not found", "error"); return "", 1
    except subprocess.TimeoutExpired:
        emit("adb timed out", "error"); return "", 1

def refresh_devices():
    out, _ = adb("devices")
    devs = []
    for line in out.splitlines()[1:]:
        if "\tdevice" in line:
            s = line.split("\t")[0]
            model, _ = adb("shell", "getprop", "ro.product.model", serial=s)
            ver,   _ = adb("shell", "getprop", "ro.build.version.release", serial=s)
            devs.append({"serial": s, "model": model or s, "android": ver or "?"})
    state["devices"] = devs
    return devs

# ===========================================
# PROXY
# ===========================================
def set_device_proxy(serial, host, port):
    adb("shell","settings","put","global","http_proxy", host+":"+str(port), serial=serial)
    emit("Proxy set -> "+host+":"+str(port))

def clear_device_proxy(serial):
    adb("shell","settings","put","global","http_proxy",":0",serial=serial)
    adb("shell","settings","delete","global","http_proxy",serial=serial)
    emit("Proxy cleared")

def start_mitm_tunnel(host, port, user, pwd, local=8118):
    if not shutil.which("mitmdump"):
        emit("mitmdump not found", "error"); return None
    kwargs = {"creationflags": CREATE_NO_WINDOW} if os.name == 'nt' else {}
    proc = subprocess.Popen(["mitmdump","--mode","upstream:http://"+host+":"+str(port),
        "--upstream-auth",user+":"+pwd,"--listen-port",str(local),"--quiet"], **kwargs)
    time.sleep(2); emit("mitmproxy tunnel on port "+str(local)); return proc

# ===========================================
# SCRCPY
# ===========================================
def launch_scrcpy_for(serial, x=0, y=0, size=800):
    if not shutil.which("scrcpy"):
        emit("scrcpy not found","error"); return None
    kwargs = {"creationflags": CREATE_NO_WINDOW} if os.name == 'nt' else {}
    proc = subprocess.Popen(["scrcpy","--serial",serial,"--max-size",str(size),
        "--window-title","Android ["+serial+"]","--window-x",str(x),
        "--window-y",str(y),"--stay-awake"],
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL, **kwargs)
    emit("scrcpy -> "+serial); return proc

# ===========================================
# APPIUM
# ===========================================
def is_appium_running():
    import urllib.request
    try:
        req = urllib.request.urlopen("http://127.0.0.1:4723/status", timeout=1.0)
        return req.status == 200
    except Exception:
        import socket
        s = socket.socket()
        s.settimeout(0.5)
        try:
            s.connect(('127.0.0.1', 4723))
            s.close()
            return True
        except Exception:
            s.close()
            return False

def clean_port_4723():
    if is_appium_running():
        return True
    import socket
    s = socket.socket()
    s.settimeout(0.5)
    busy = False
    try:
        s.connect(('127.0.0.1', 4723))
        s.close()
        busy = True
    except Exception:
        s.close()

    if busy:
        emit("Port 4723 occupied by un-responsive process. Clearing via netstat...")
        try:
            if os.name == 'nt':
                kwargs = {"creationflags": CREATE_NO_WINDOW}
                out = subprocess.check_output('netstat -ano | findstr :4723', shell=True, **kwargs).decode()
                for line in out.strip().split('\n'):
                    if 'LISTENING' in line:
                        parts = line.strip().split()
                        pid = parts[-1]
                        subprocess.run(f'taskkill /f /pid {pid}', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)
                        emit(f"Killed zombie PID {pid} on port 4723")
        except Exception as e:
            emit("Failed to clear port 4723: " + str(e), "warning")
    return False

def ensure_appium_server():
    appdata = os.environ.get("APPDATA", "")
    npm_dir = os.path.join(appdata, "npm") if appdata else ""
    extra_paths = [
        npm_dir,
        r"C:\Program Files\nodejs",
        r"C:\Program Files (x86)\nodejs",
    ]
    current_path = os.environ.get("PATH", "")
    for p in extra_paths:
        if p and os.path.exists(p) and p not in current_path:
            os.environ["PATH"] = p + ";" + os.environ["PATH"]

    if is_appium_running():
        return True

    clean_port_4723()

    npm_appium = os.path.join(npm_dir, "appium.cmd") if npm_dir else ""
    appium_bin = (
        shutil.which("appium.cmd") or
        shutil.which("appium") or
        (npm_appium if os.path.exists(npm_appium) else None)
    )
    if not appium_bin:
        emit("Appium CLI not found. Run 'npm install -g appium' in terminal.", "error")
        return False

    emit("Launching Appium server on port 4723...")
    try:
        log_f = open("appium_server.log", "a")
        proc = subprocess.Popen(
            f'"{appium_bin}"',
            shell=True,
            env=os.environ,
            stdout=log_f,
            stderr=log_f,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        )
        state['appium_server_proc'] = proc
        for _ in range(30):
            time.sleep(0.5)
            if is_appium_running():
                emit("Appium server running on http://127.0.0.1:4723 OK!")
                return True
        emit("Appium server start timed out", "error")
        return False
    except Exception as e:
        emit("Failed to launch Appium server: " + str(e), "error")
        return False

def _make_driver(serial):
    try:
        from appium import webdriver
        from appium.options.android import UiAutomator2Options
    except ImportError:
        emit("Appium-Python-Client not installed","error"); return None
    if not ensure_appium_server():
        return None
    p    = plat()
    opts = UiAutomator2Options()
    opts.platform_name          = "Android"
    opts.device_name            = serial
    opts.udid                   = serial
    opts.app_package            = p["package"]
    opts.app_activity           = p["activity"]
    opts.no_reset               = True
    opts.auto_grant_permissions = True
    opts.new_command_timeout    = 3600
    emit("Initializing Appium driver for " + serial + " (UiAutomator2)...")
    try:
        driver = webdriver.Remote(APPIUM_SERVER.rstrip("/"), options=opts)
        emit("Appium connected: "+serial+" ("+p["name"]+") OK")
        return driver
    except Exception as e:
        emit("Appium failed ("+serial+"): "+str(e),"error"); return None

def _watchdog(serial):
    emit("Watchdog started: "+serial)
    while serial in state["watchdogs"]:
        time.sleep(RECONNECT_INTERVAL)
        driver = state["drivers"].get(serial)
        if not driver: continue
        try: _ = driver.current_activity
        except Exception:
            emit("Session dropped: "+serial+" — reconnecting...", "warning")
            for attempt in range(1, RECONNECT_RETRIES+1):
                emit("Reconnect attempt "+str(attempt)+"/"+str(RECONNECT_RETRIES))
                time.sleep(10)
                new_drv = _make_driver(serial)
                if new_drv:
                    state["drivers"][serial] = new_drv
                    emit("Reconnected: "+serial+" OK")
                    break
            else:
                emit("Reconnect failed: "+serial,"error")
                state["drivers"].pop(serial,None)
    emit("Watchdog stopped: "+serial)

# ===========================================
# HUMAN EMULATION ENGINE
# ===========================================
def human_delay(lo=400, hi=2000):
    human_delay_gaussian(mean_sec=(lo+hi)/2000.0, std_sec=(hi-lo)/4000.0, min_sec=lo/1000.0, max_sec=hi/1000.0)

def human_delay_gaussian(mean_sec, std_sec=None, min_sec=0.3, max_sec=120.0):
    if std_sec is None:
        std_sec = max(0.1, mean_sec * 0.25)
    val = random.gauss(mean_sec, std_sec)
    clamped = max(min_sec, min(max_sec, val))
    time.sleep(clamped)
    return clamped

def human_think_pause():
    if random.random() < 0.08:
        distraction = random.uniform(8.0, 18.0)
        emit(f"Human pause: distraction break ({distraction:.1f}s)")
        time.sleep(distraction)
    else:
        human_delay_gaussian(mean_sec=1.8, std_sec=0.6, min_sec=0.5, max_sec=4.5)

def cubic_bezier(p0, p1, p2, p3, t):
    u = 1 - t
    tt = t * t
    uu = u * u
    uuu = uu * u
    ttt = tt * t
    x = uuu * p0[0] + 3 * uu * t * p1[0] + 3 * u * tt * p2[0] + ttt * p3[0]
    y = uuu * p0[1] + 3 * uu * t * p1[1] + 3 * u * tt * p2[1] + ttt * p3[1]
    return int(x), int(y)

def human_drag_gesture(driver, sx, sy, ex, ey):
    """Performs a natural curved touch gesture with ease-in-out velocity profile."""
    mid_x = (sx + ex) // 2
    mid_y = (sy + ey) // 2
    offset_x = random.randint(-40, 40)
    offset_y = random.randint(-30, 30)
    p0 = (sx, sy)
    p1 = (sx + offset_x, sy + offset_y)
    p2 = (mid_x + offset_x, mid_y - offset_y)
    p3 = (ex, ey)

    try:
        driver.execute_script('mobile: dragGesture', {
            'startX': sx, 'startY': sy, 'endX': ex, 'endY': ey, 'speed': random.randint(800, 1200)
        })
        return True
    except Exception:
        pass

    try:
        from selenium.webdriver.common.action_chains import ActionChains
        from selenium.webdriver.common.actions.interaction import POINTER_TOUCH
        from selenium.webdriver.common.actions.pointer_input import PointerInput

        touch = PointerInput(POINTER_TOUCH, "finger")
        actions = ActionChains(driver)
        actions.w3c_actions.devices = [touch]

        steps = 10
        actions.w3c_actions.pointer_action.move_to_location(sx, sy)
        actions.w3c_actions.pointer_action.pointer_down()

        for i in range(1, steps + 1):
            t = i / steps
            eased_t = 0.5 * (1 - math.cos(math.pi * t))
            bx, by = cubic_bezier(p0, p1, p2, p3, eased_t)
            actions.w3c_actions.pointer_action.move_to_location(bx, by)
            actions.w3c_actions.pointer_action.pause(random.uniform(0.01, 0.03))

        actions.w3c_actions.pointer_action.pointer_up()
        actions.perform()
        return True
    except Exception:
        if hasattr(driver, 'swipe'):
            driver.swipe(sx, sy, ex, ey, duration=random.randint(250, 450))
            return True
    return False

def organic_micro_interaction(driver, serial=None):
    """Executes rare, non-disruptive human engagement actions during playback (Like track, volume tweak, artwork tap). NO PAUSES."""
    roll = random.random()
    try:
        if roll < 0.45:
            # 45% of micro-interactions: Tap Heart / Like button (High engagement signal)
            from appium.webdriver.common.appiumby import AppiumBy
            like_locators = [
                (AppiumBy.ID, "com.spotify.music:id/heart_button"),
                (AppiumBy.ID, "com.spotify.music:id/context_menu_like_button"),
                (AppiumBy.XPATH, '//*[contains(@content-desc,"Save to Your Library") or contains(@content-desc,"Lagre i ditt bibliotek") or contains(@content-desc,"Like")]'),
            ]
            for by, val in like_locators:
                try:
                    els = driver.find_elements(by, val)
                    if els:
                        els[0].click()
                        emit("Human interaction: Organic track Like ❤️")
                        break
                except Exception:
                    pass
        elif roll < 0.85:
            # 40% of micro-interactions: Vol up/down 1 step
            kc = 24 if random.random() > 0.5 else 25
            driver.press_keycode(kc)
            emit(f"Human interaction: Vol {'up' if kc==24 else 'down'}")
        else:
            # 15% of micro-interactions: artwork tap
            try:
                window_size = driver.get_window_size()
                w = window_size.get('width', 1080)
                h = window_size.get('height', 1920)
            except Exception:
                w, h = 1080, 1920
            from selenium.webdriver.common.action_chains import ActionChains
            actions = ActionChains(driver)
            tx = w // 2 + random.randint(-40, 40)
            ty = int(h * 0.35) + random.randint(-40, 40)
            actions.w3c_actions.pointer_action.move_to_location(tx, ty)
            actions.w3c_actions.pointer_action.pointer_down()
            actions.w3c_actions.pointer_action.pause(random.uniform(0.05, 0.10))
            actions.w3c_actions.pointer_action.pointer_up()
            actions.perform()
            emit("Human interaction: artwork tap")

        # Always enforce play resume safeguard
        if serial:
            adb("shell", "input", "keyevent", "126", serial=serial) # KEYCODE_MEDIA_PLAY
    except Exception:
        pass

def ensure_device_awake(serial=None):
    if not serial and state.get("selected"):
        serial = state["selected"]
    if serial:
        adb("shell", "svc", "power", "stayon", "true", serial=serial)
        adb("shell", "input", "keyevent", "KEYCODE_WAKEUP", serial=serial)
        adb("shell", "wm", "dismiss-keyguard", serial=serial)
        adb("shell", "cmd", "statusbar", "collapse", serial=serial)

def human_type_text(driver, bar, text, serial=None):
    NEARBY_KEYS = {
        'a': ['q', 's', 'z'], 'b': ['v', 'g', 'h', 'n'], 'c': ['x', 'd', 'v'],
        'd': ['s', 'e', 'r', 'f', 'c'], 'e': ['w', 's', 'd', 'r'], 'f': ['d', 'r', 't', 'g', 'v'],
        'g': ['f', 't', 'y', 'h', 'b'], 'h': ['g', 'y', 'u', 'j', 'n'], 'i': ['u', 'j', 'k', 'o'],
        'j': ['h', 'u', 'i', 'k', 'm'], 'k': ['j', 'i', 'o', 'l'], 'l': ['k', 'o', 'p'],
        'm': ['n', 'j', 'k'], 'n': ['b', 'h', 'j', 'm'], 'o': ['i', 'k', 'l', 'p'],
        'p': ['o', 'l'], 'q': ['w', 'a'], 'r': ['e', 'd', 'f', 't'],
        's': ['a', 'w', 'e', 'd', 'x', 'z'], 't': ['r', 'f', 'g', 'y'],
        'u': ['y', 'h', 'j', 'i'], 'v': ['c', 'f', 'g', 'b'], 'w': ['q', 'a', 's', 'e'],
        'x': ['z', 's', 'd', 'c'], 'y': ['t', 'g', 'h', 'u'], 'z': ['a', 's', 'x']
    }

    try:
        if bar:
            bar.click()
            time.sleep(0.2)
            if hasattr(bar, 'text') and bar.text and len(bar.text) > 0:
                driver.press_keycode(29, 28672)
                driver.press_keycode(67)
    except Exception:
        pass

    human_delay_gaussian(0.18, 0.05, min_sec=0.10, max_sec=0.30)

    i = 0
    while i < len(text):
        target_char = text[i]

        if target_char.lower() in NEARBY_KEYS and random.random() < 0.12:
            typo_len = random.choice([1, 2, 3])
            typo_chars = []
            curr_c = target_char.lower()
            for _ in range(typo_len):
                neighbors = NEARBY_KEYS.get(curr_c, [curr_c])
                wrong_c = random.choice(neighbors)
                if target_char.isupper():
                    wrong_c = wrong_c.upper()
                typo_chars.append(wrong_c)
                curr_c = wrong_c.lower()

            for tc in typo_chars:
                if bar:
                    try: bar.send_keys(tc)
                    except Exception: pass
                elif serial:
                    adb("shell", "input", "text", tc, serial=serial)
                human_delay_gaussian(0.06, 0.02, min_sec=0.03, max_sec=0.12)

            human_delay_gaussian(0.18, 0.04, min_sec=0.10, max_sec=0.28)

            num_backspaces = min(len(typo_chars), random.choice([1, 2, 3]))
            for _ in range(num_backspaces):
                try: driver.press_keycode(67)
                except Exception:
                    if serial: adb("shell", "input", "keyevent", "67", serial=serial)
                human_delay_gaussian(0.07, 0.02, min_sec=0.03, max_sec=0.14)

            remaining = len(typo_chars) - num_backspaces
            for _ in range(remaining):
                try: driver.press_keycode(67)
                except Exception:
                    if serial: adb("shell", "input", "keyevent", "67", serial=serial)
                human_delay_gaussian(0.06, 0.02, min_sec=0.03, max_sec=0.12)

            human_delay_gaussian(0.12, 0.03, min_sec=0.06, max_sec=0.20)

        if target_char.isupper() or target_char in "!@#$%^&*()_+{}|:\"<>?":
            human_delay_gaussian(0.10, 0.03, min_sec=0.05, max_sec=0.18)

        if bar:
            try: bar.send_keys(target_char)
            except Exception:
                if serial:
                    safe_tc = "%s" if target_char == " " else target_char
                    adb("shell", "input", "text", safe_tc, serial=serial)
        elif serial:
            safe_tc = "%s" if target_char == " " else target_char
            adb("shell", "input", "text", safe_tc, serial=serial)

        if target_char == " ":
            human_delay_gaussian(0.09, 0.025, min_sec=0.04, max_sec=0.16)
        else:
            human_delay_gaussian(0.045, 0.015, min_sec=0.025, max_sec=0.095)

        i += 1

def find_and_open_playlist(driver, query, serial=None):
    from appium.webdriver.common.appiumby import AppiumBy
    p = plat()
    ensure_device_awake(serial)
    try:
        human_delay_gaussian(1.2, 0.3)
        search_locators = [
            (AppiumBy.XPATH, '//*[contains(@content-desc,"Search") or contains(@content-desc,"Søk") or contains(@content-desc,"Fane 2") or contains(@content-desc,"Tab 2")]'),
            (AppiumBy.ACCESSIBILITY_ID, p["search_label"]),
            (AppiumBy.XPATH, '//*[contains(@resource-id,"search_tab") or contains(@resource-id,"search")]'),
            (AppiumBy.ID, p["package"]+":id/search_tab"),
            (AppiumBy.ACCESSIBILITY_ID, "Search"),
            (AppiumBy.ACCESSIBILITY_ID, "Søk"),
        ]
        for by, val in search_locators:
            try:
                el = driver.find_element(by, val)
                el.click()
                break
            except Exception:
                pass

        human_delay_gaussian(1.2, 0.3)
        bar = None
        bar_locators = [
            (AppiumBy.XPATH, '//*[contains(@text,"Hva vil du lytte til") or contains(@text,"What do you want to listen to") or contains(@text,"écouter") or contains(@text,"escuchar")]'),
            (AppiumBy.ID, "com.spotify.music:id/browse_search_bar_container"),
            (AppiumBy.ID, "com.spotify.music:id/query"),
            (AppiumBy.ID, p["search_id"]),
            (AppiumBy.XPATH, '//android.widget.EditText'),
            (AppiumBy.XPATH, '//*[contains(@resource-id,"search") and (contains(@class,"EditText") or contains(@class,"TextView"))]'),
        ]
        for by, val in bar_locators:
            try:
                bar = driver.find_element(by, val)
                bar.click()
                break
            except Exception:
                pass

        if not bar:
            try:
                driver.tap([(300, 140)])
                time.sleep(0.5)
            except Exception:
                pass

        human_delay_gaussian(0.8, 0.2)
        try:
            edit_fields = driver.find_elements(AppiumBy.XPATH, '//android.widget.EditText | //*[contains(@resource-id,"query") or contains(@resource-id,"text_input")]')
            if edit_fields:
                bar = edit_fields[0]
                bar.click()
        except Exception:
            pass

        # Execute human typing with organic typos and natural 1-3 backspace sequences
        human_type_text(driver, bar, query, serial=serial)

        # Submit search query via ENTER keycode
        human_delay_gaussian(0.8, 0.2)
        try:
            driver.press_keycode(66)  # KEYCODE_ENTER
        except Exception:
            if serial:
                adb("shell", "input", "keyevent", "66", serial=serial)

        human_delay_gaussian(2.2, 0.5)

        try:
            window_size = driver.get_window_size()
            w = window_size.get('width', 1080)
            h = window_size.get('height', 1920)
        except Exception:
            w, h = 1080, 1920

        results = []
        result_locators = [
            (AppiumBy.ID, "com.spotify.music:id/card_root"),
            (AppiumBy.XPATH, '//*[contains(@resource-id,"search_result") or contains(@resource-id,"title") or contains(@resource-id,"entity")]'),
            (AppiumBy.XPATH, '//*[@clickable="true" and not(contains(@resource-id,"now_playing")) and not(contains(@resource-id,"mini_player"))]'),
        ]
        for by, val in result_locators:
            try:
                res = driver.find_elements(by, val)
                filtered = []
                for el in res:
                    try:
                        loc = el.location
                        # Keep only elements positioned in main content area (between 12% and 68% height, safely above bottom mini-player)
                        if h * 0.12 <= loc['y'] <= h * 0.68:
                            filtered.append(el)
                    except Exception:
                        filtered.append(el)
                if filtered:
                    results = filtered
                    break
            except Exception:
                pass

        if results:
            human_delay_gaussian(0.6, 0.2)
            try:
                results[0].click()
            except Exception:
                driver.tap([(w // 2, int(h * 0.32))])
        else:
            # Fallback tap directly at top search result card position (32% height - safely above bottom mini-player)
            driver.tap([(w // 2, int(h * 0.32))])

        human_delay_gaussian(2.5, 0.5)
        play_locators = [
            (AppiumBy.ID, "com.spotify.music:id/button_play_and_pause"),
            (AppiumBy.ID, "com.spotify.music:id/play_button"),
            (AppiumBy.XPATH, '//*[contains(@content-desc,"Spill") or contains(@content-desc,"Play") or contains(@content-desc,"Pause")]'),
        ]
        for by, val in play_locators:
            try:
                els = driver.find_elements(by, val)
                if els:
                    els[0].click()
                    break
            except Exception:
                pass

        human_delay_gaussian(1.5, 0.3)
        emit("Opened and playing: " + query)
        return True
    except Exception as e:
        emit("Playlist error: " + str(e), "error")
        return False

def restart_song(driver):
    from appium.webdriver.common.appiumby import AppiumBy
    p = plat()
    try:
        seek = driver.find_element(AppiumBy.ID, p["seekbar"])
        loc = seek.location; sz = seek.size
        sx = loc["x"] + sz["width"] - random.randint(8, 18)
        sy = loc["y"] + sz["height"] // 2
        ex = loc["x"] + random.randint(2, 6)
        if human_drag_gesture(driver, sx, sy, ex, sy):
            emit("Restarted (Bézier drag gesture)")
            return
    except Exception:
        pass
    try:
        driver.press_keycode(88) # KEYCODE_MEDIA_PREVIOUS
        emit("Restarted (keycode)")
    except Exception as e:
        emit("Restart failed: "+str(e),"error")

def ensure_playing(driver):
    from appium.webdriver.common.appiumby import AppiumBy
    try: driver.find_element(AppiumBy.ACCESSIBILITY_ID,plat()["play_label"]).click()
    except Exception: pass

def is_url_or_uri(text):
    t = text.strip().lower()
    return t.startswith("http://") or t.startswith("https://") or t.startswith("spotify:") or (":" in t and "open." in t)

def normalize_spotify_url(url):
    u = url.strip()
    if "open.spotify.com/" in u:
        try:
            parts = u.split("open.spotify.com/")[1].split("?")[0].split("/")
            if len(parts) >= 2:
                return f"spotify:{parts[0]}:{parts[1]}"
        except Exception:
            pass
    return u

def open_target_or_query(driver, query, serial=None):
    from appium.webdriver.common.appiumby import AppiumBy
    p = plat()
    ensure_device_awake(serial)
    clean_q = query.strip()
    
    if is_url_or_uri(clean_q):
        target_uri = normalize_spotify_url(clean_q)
        pkg = p.get("package", "com.spotify.music")
        emit(f"Opening direct URL/URI ({target_uri}) on package {pkg}")
        try:
            if serial:
                adb("shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", target_uri, "-p", pkg, serial=serial)
            elif driver:
                driver.get(target_uri)
        except Exception:
            try:
                if serial:
                    adb("shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", clean_q, "-p", pkg, serial=serial)
                elif driver:
                    driver.get(clean_q)
            except Exception:
                pass

        human_delay_gaussian(2.0, 0.4)

        # Auto-dismiss 'Åpne med' / 'Open with' chooser if app defaults aren't set
        if driver:
            try:
                spotify_btns = driver.find_elements(AppiumBy.XPATH, '//*[contains(@text,"Spotify")]')
                if spotify_btns:
                    spotify_btns[0].click()
                    time.sleep(0.5)
                    choice_btns = driver.find_elements(AppiumBy.XPATH, '//*[contains(@text,"ALLTID") or contains(@text,"ALWAYS") or contains(@text,"BARE ÉN GANG") or contains(@text,"JUST ONCE")]')
                    if choice_btns:
                        choice_btns[0].click()
                        time.sleep(1.0)
            except Exception:
                pass

            human_delay_gaussian(1.5, 0.4)
            
            play_locators = [
                (AppiumBy.ID, "com.spotify.music:id/button_play_and_pause"),
                (AppiumBy.ID, "com.spotify.music:id/play_button"),
                (AppiumBy.XPATH, '//*[contains(@content-desc,"Spill") or contains(@content-desc,"Play") or contains(@content-desc,"Pause")]'),
            ]
            for by, val in play_locators:
                try:
                    els = driver.find_elements(by, val)
                    if els:
                        els[0].click()
                        break
                except Exception:
                    pass
        else:
            time.sleep(1.5)
            if serial:
                adb("shell", "input", "keyevent", "85", serial=serial) # KEYCODE_MEDIA_PLAY_PAUSE

        human_delay_gaussian(1.5, 0.3)
        emit("Opened and playing target URL: " + clean_q)
        return True

    return find_and_open_playlist(driver, clean_q, serial=serial)

# ===========================================
# LOOP WORKER
# ===========================================
def _loop_worker(serial, query, min_sec, max_sec):
    loop   = state["loops"][serial]
    driver = state["drivers"].get(serial)
    if not driver:
        emit("No driver for "+serial,"error"); loop["active"]=False; return
    lo = min(int(min_sec), int(max_sec))
    hi = max(int(min_sec), int(max_sec))
    emit("["+serial[-6:]+"] Loop: "+plat()["name"]+" / "+query+" (Stealth Human Engine)")
    apply_stealth_hardware_settings(serial)
    if not open_target_or_query(driver, query, serial=serial):
        loop["active"]=False; return
    human_delay_gaussian(mean_sec=1.5, std_sec=0.4)
    ensure_playing(driver)

    loop_start_time = time.time()
    tracks_played = 0

    while loop["active"]:
        # Circadian Rhythm Time-of-Day Dynamics & Fatigue Scaling
        current_hour = time.localtime().tm_hour
        if 1 <= current_hour <= 6:
            circadian_scale = 1.35  # Night time: 35% slower interactions
        elif 7 <= current_hour <= 11:
            circadian_scale = 0.92  # Morning: slightly faster
        elif 12 <= current_hour <= 18:
            circadian_scale = 1.0   # Afternoon: baseline speed
        else:
            circadian_scale = 1.15  # Evening: 15% slower

        # Session Fatigue: Every 10 tracks played slows down speed by ~2%
        fatigue_scale = min(1.30, 1.0 + (tracks_played * 0.02))
        total_time_scale = circadian_scale * fatigue_scale

        persona_roll = random.random()
        if persona_roll < 0.70:
            target_wait = int(random.uniform(lo * 0.85, hi * 1.05) * total_time_scale)
            persona_name = "Full Listen"
        elif persona_roll < 0.85:
            target_wait = int(random.uniform(lo * 0.45, lo * 0.75) * total_time_scale)
            persona_name = "Mid Listen"
        else:
            target_wait = int(random.randint(10, 22) * total_time_scale)
            persona_name = "Quick Skip"

        # Organic Rest Break after 12-18 tracks (~45-60 mins of continuous streaming)
        if tracks_played > 0 and tracks_played % random.randint(12, 18) == 0:
            rest_break_sec = random.randint(300, 900)
            emit(f"[{serial[-6:]}] 🌙 Human rest break: taking a {rest_break_sec//60} min pause")
            for _ in range(rest_break_sec):
                if not loop["active"]: break
                time.sleep(1)
            if not loop["active"]: break

        tracks_played += 1
        loop["next_restart"] = time.time() + target_wait
        emit(f"[{serial[-6:]}] [{persona_name}] (Speed: {total_time_scale:.2f}x) Next restart in {target_wait}s")

        # 5% chance per track to trigger a single subtle micro-interaction (Like, Vol tweak, Artwork tap)
        do_interaction = (random.random() < 0.05)
        interaction_time = random.randint(15, max(16, target_wait - 10)) if do_interaction else -1

        elapsed = 0
        while elapsed < target_wait and loop["active"]:
            step = min(1, target_wait - elapsed)
            time.sleep(step)
            elapsed += step

            if do_interaction and elapsed >= interaction_time:
                do_interaction = False
                driver = state["drivers"].get(serial)
                if driver:
                    organic_micro_interaction(driver, serial=serial)

        if not loop["active"]: break

        driver = state["drivers"].get(serial)
        if not driver:
            emit("["+serial[-6:]+"] Driver lost — waiting...","warning")
            for _ in range(12):
                time.sleep(5); driver=state["drivers"].get(serial)
                if driver: break
            if not driver:
                emit("["+serial[-6:]+"] Reconnect timeout","error"); break

        human_think_pause()
        try:
            restart_song(driver)
            human_delay_gaussian(mean_sec=0.8, std_sec=0.2)
            ensure_playing(driver)
        except Exception as e:
            emit("["+serial[-6:]+"] Playback action error: "+str(e),"warning")

        loop["cycles"]     = loop.get("cycles",0)+1
        loop["total_time"] = loop.get("total_time",0)+target_wait

    loop["active"]=False; loop["next_restart"]=None
    emit("["+serial[-6:]+"] Stopped — "+str(loop.get("cycles",0))+" cycles")

# ===========================================
# ACCOUNT HELPERS
# ===========================================
def do_account_switch(serial, driver, acc):
    from appium.webdriver.common.appiumby import AppiumBy
    p = plat()
    try:
        emit("Switching to "+acc.get("email","?")+" on "+p["name"])
        adb("shell","pm","clear",p["package"],serial=serial)
        time.sleep(random.uniform(1.5,2.5))
        adb("shell","am","start","-n",p["package"]+"/"+p["activity"],serial=serial)
        time.sleep(random.uniform(4.0,6.0))
        try:
            btn=driver.find_element(AppiumBy.XPATH,
                '//android.widget.Button[contains(@text,"Log in") or contains(@text,"Sign in")]')
            btn.click(); time.sleep(random.uniform(1.5,2.5))
        except Exception: pass
        for fid,val in [(p["login_id"],acc.get("email","")),
                        (p["password_id"],acc.get("password",""))]:
            try:
                f=driver.find_element(AppiumBy.ID,fid)
                f.click(); time.sleep(random.uniform(0.4,0.8))
                for ch in val: f.send_keys(ch); time.sleep(random.uniform(0.04,0.15))
                time.sleep(random.uniform(0.5,1.0))
            except Exception as e: emit("Field error: "+str(e),"warning")
        try:
            driver.find_element(AppiumBy.ID,p["login_btn"]).click()
            emit("Login submitted"); time.sleep(random.uniform(4.0,6.0))
        except Exception as e: emit("Login btn: "+str(e),"warning")
        emit("Switch complete: "+acc.get("email","?"))
    except Exception as e: emit("Switch error: "+str(e),"error")

def do_gmail_register(serial, driver, fname, lname, username):
    from appium.webdriver.common.appiumby import AppiumBy
    try:
        emit("Opening Gmail signup on "+serial)
        adb("shell","am","start","-a","android.intent.action.VIEW",
            "-d","https://accounts.google.com/signup",
            "-n","com.android.chrome/com.google.android.apps.chrome.Main",serial=serial)
        time.sleep(random.uniform(4.0,6.0))
        for xpath,val in [
            ('//android.widget.EditText[@resource-id="firstName"]', fname),
            ('//android.widget.EditText[@resource-id="lastName"]',  lname),
            ('//android.widget.EditText[@resource-id="username"]',  username),
        ]:
            if not val: continue
            try:
                el=driver.find_element(AppiumBy.XPATH,xpath)
                el.click(); time.sleep(random.uniform(0.4,0.8))
                for ch in val: el.send_keys(ch); time.sleep(random.uniform(0.05,0.14))
                emit("Filled: "+val)
            except Exception as e: emit("Field not found: "+str(e),"warning")
        emit("Gmail fields done — complete CAPTCHA on phone")
    except Exception as e: emit("Gmail error: "+str(e),"error")

def do_spotify_register(serial, driver, email, password, username):
    from appium.webdriver.common.appiumby import AppiumBy
    p = plat()
    try:
        emit("Opening Spotify signup")
        adb("shell","pm","clear",p["package"],serial=serial)
        time.sleep(random.uniform(2.0,3.0))
        adb("shell","am","start","-n",p["package"]+"/"+p["activity"],serial=serial)
        time.sleep(random.uniform(4.0,6.0))
        try:
            su=driver.find_element(AppiumBy.XPATH,
                '//android.widget.Button[contains(@text,"Sign up") or contains(@text,"SIGN UP")]')
            su.click(); time.sleep(random.uniform(2.0,3.5))
        except Exception as e: emit("Signup btn: "+str(e),"warning")
        for fid,val in [
            (p["package"]+":id/email",        email),
            (p["package"]+":id/password",     password),
            (p["package"]+":id/display_name", username),
        ]:
            if not val: continue
            try:
                f=driver.find_element(AppiumBy.ID,fid)
                f.click(); time.sleep(random.uniform(0.3,0.7))
                for ch in val: f.send_keys(ch); time.sleep(random.uniform(0.05,0.15))
                time.sleep(random.uniform(0.5,1.0))
                try:
                    nxt=driver.find_element(AppiumBy.ID,p["package"]+":id/next_button")
                    nxt.click(); time.sleep(random.uniform(1.5,2.5))
                except Exception: pass
                emit("Filled: "+fid.split("/")[-1])
            except Exception as e: emit("Field error: "+str(e),"warning")
        emit("Spotify fields done — complete CAPTCHA manually")
    except Exception as e: emit("Spotify reg error: "+str(e),"error")


# ===========================================
# UI
# ===========================================
UI_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>StreamGrid — Multi-Device Streaming Engine</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@300;400;600&family=Space+Grotesk:wght@400;600;700&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#0a0c0f;--surface:#111418;--surface2:#181c22;--border:#222830;
  --green:#1db954;--red:#e53e3e;--yellow:#f6c90e;--blue:#4da6ff;
  --text:#d4dae3;--muted:#5a6475;--accent:#1db954;
  --mono:'IBM Plex Mono',monospace;--sans:'Space Grotesk',sans-serif;
}
body.light{--bg:#f0f2f5;--surface:#fff;--surface2:#f7f9fb;--border:#dde1e8;--text:#1a1d23;--muted:#8892a0}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:var(--sans);min-height:100vh;
  display:grid;grid-template-rows:56px 1fr;grid-template-columns:260px 1fr 300px;
  grid-template-areas:"hd hd hd" "sb mn lg";transition:background .2s,color .2s}
header{grid-area:hd;background:var(--surface);border-bottom:1px solid var(--border);
  display:flex;align-items:center;padding:0 20px;gap:12px}
.logo-text{font-family:var(--mono);font-size:14px;font-weight:600;letter-spacing:.04em}
.logo-sub{font-family:var(--mono);font-size:10px;color:var(--muted)}
.hdr-right{margin-left:auto;display:flex;align-items:center;gap:10px}
.plt-sel{background:var(--surface2);border:1px solid var(--border);border-radius:6px;
  padding:5px 10px;color:var(--text);font-family:var(--mono);font-size:12px;outline:none;cursor:pointer}
.hk{font-family:var(--mono);font-size:10px;color:var(--muted);background:var(--surface2);
  border:1px solid var(--border);border-radius:4px;padding:2px 7px}
.theme-btn{background:none;border:1px solid var(--border);border-radius:6px;
  padding:5px 10px;color:var(--text);cursor:pointer;font-size:14px;transition:border-color .15s}
.theme-btn:hover{border-color:var(--accent)}
.chip{font-family:var(--mono);font-size:11px;padding:3px 9px;border-radius:999px;
  border:1px solid var(--border);display:flex;align-items:center;gap:5px}
.chip .dot{width:6px;height:6px;border-radius:50%;background:var(--muted)}
.chip.ok .dot{background:var(--green);box-shadow:0 0 5px var(--green)}
.chip.bad .dot{background:var(--red)}
aside{grid-area:sb;background:var(--surface);border-right:1px solid var(--border);
  display:flex;flex-direction:column;overflow-y:auto}
.sec{padding:11px 16px 7px;font-family:var(--mono);font-size:10px;letter-spacing:.12em;
  color:var(--muted);text-transform:uppercase;border-bottom:1px solid var(--border)}
.dev-list{padding:8px;display:flex;flex-direction:column;gap:5px}
.dev-card{background:var(--surface2);border:1px solid var(--border);border-radius:8px;
  padding:9px 12px;cursor:pointer;transition:border-color .15s;display:flex;flex-direction:column;gap:3px}
.dev-card:hover{border-color:var(--accent)}
.dev-card.active{border-color:var(--accent);background:rgba(29,185,84,.05)}
.dev-card-row{display:flex;align-items:center;gap:8px;width:100%}
.dev-cb{width:15px;height:15px;accent-color:var(--accent);cursor:pointer}
.dev-actions{display:flex;gap:5px;margin-top:6px;padding-top:6px;border-top:1px solid var(--border)}
.btn-sm{padding:3px 8px;font-size:10px;border-radius:4px;font-weight:600;cursor:pointer;border:1px solid var(--border);background:var(--surface);color:var(--text);transition:all .15s}
.btn-sm:hover{border-color:var(--accent);color:var(--accent)}
.btn-sm.mirror{border-color:var(--blue);color:var(--blue)}.btn-sm.mirror:hover{background:rgba(77,166,255,.15)}
.sb-tools{padding:6px 10px;display:flex;gap:6px;align-items:center;border-bottom:1px solid var(--border);background:var(--surface2)}
.dname{font-size:13px;font-weight:600}
.dserial{font-family:var(--mono);font-size:10px;color:var(--muted)}
.dbadges{display:flex;gap:4px;margin-top:3px;flex-wrap:wrap}
.dbadge{font-family:var(--mono);font-size:9px;padding:1px 6px;
  border-radius:999px;border:1px solid var(--border);color:var(--muted)}
.dbadge.conn{border-color:var(--green);color:var(--green)}
.dbadge.loop-on{border-color:var(--yellow);color:var(--yellow)}
.no-dev{padding:16px;font-size:13px;color:var(--muted);line-height:1.6}
.rfsh{margin:8px;padding:7px;background:none;border:1px solid var(--border);border-radius:6px;
  color:var(--text);font-family:var(--sans);font-size:12px;cursor:pointer;transition:border-color .15s,color .15s}
.rfsh:hover{border-color:var(--accent);color:var(--accent)}
.sg{padding:10px 12px;display:grid;grid-template-columns:1fr 1fr;gap:5px}
.si{background:var(--surface2);border:1px solid var(--border);border-radius:6px;
  padding:7px 9px;display:flex;flex-direction:column;gap:2px}
.sl{font-size:10px;color:var(--muted);font-family:var(--mono)}
.sv{font-size:12px;font-weight:600}.sv.on{color:var(--green)}.sv.off{color:var(--muted)}
main{grid-area:mn;overflow-y:auto;padding:18px 22px;display:flex;flex-direction:column;gap:18px}
.panel{background:var(--surface);border:1px solid var(--border);border-radius:12px;overflow:hidden}
.ph{padding:13px 18px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:10px}
.ph h2{font-size:13px;font-weight:600}
.ph .tag{margin-left:auto;font-family:var(--mono);font-size:10px;
  padding:2px 7px;border-radius:999px;border:1px solid var(--border);color:var(--muted)}
.pb{padding:16px 18px;display:flex;flex-direction:column;gap:13px}
.fr{display:flex;gap:9px;flex-wrap:wrap}
.field{display:flex;flex-direction:column;gap:5px;flex:1;min-width:130px}
.field label{font-size:11px;font-family:var(--mono);color:var(--muted)}
.field input,.field select{background:var(--surface2);border:1px solid var(--border);
  border-radius:6px;padding:7px 10px;color:var(--text);font-family:var(--mono);
  font-size:12px;outline:none;transition:border-color .15s}
.field input:focus,.field select:focus{border-color:var(--accent)}
.br{display:flex;gap:7px;flex-wrap:wrap}
.btn{padding:8px 16px;border-radius:7px;font-family:var(--sans);font-size:12px;font-weight:600;
  cursor:pointer;border:none;transition:all .15s;display:flex;align-items:center;gap:6px}
.bg{background:var(--green);color:#000}.bg:hover{filter:brightness(1.1)}
.br2{background:rgba(229,62,62,.15);color:var(--red);border:1px solid var(--red)}.br2:hover{background:rgba(229,62,62,.25)}
.bo{background:transparent;color:var(--text);border:1px solid var(--border)}.bo:hover{border-color:var(--accent);color:var(--accent)}
.bb{background:rgba(77,166,255,.12);color:var(--blue);border:1px solid var(--blue)}.bb:hover{background:rgba(77,166,255,.22)}
.btn:disabled{opacity:.4;cursor:not-allowed}
.dlc{background:var(--surface2);border:1px solid var(--border);border-radius:9px;
  padding:12px 14px;display:flex;flex-direction:column;gap:8px}
.dlch{display:flex;align-items:center;gap:8px}
.dlcn{font-size:13px;font-weight:600;flex:1}
.lbar{height:3px;background:var(--border);border-radius:99px;overflow:hidden}
.lfill{height:100%;background:var(--accent);border-radius:99px;transition:width 1s linear}
.adb-i{flex:1;background:var(--surface2);border:1px solid var(--border);border-radius:6px;
  padding:8px 11px;color:var(--green);font-family:var(--mono);font-size:12px;outline:none}
.adb-i:focus{border-color:var(--green)}
.adb-i::placeholder{color:var(--muted)}
.acl{display:flex;flex-direction:column;gap:6px}
.acc{background:var(--surface2);border:1px solid var(--border);border-radius:8px;
  padding:9px 13px;display:flex;align-items:center;gap:10px}
.ace{font-size:12px;font-weight:600}
.acm{font-size:11px;color:var(--muted);font-family:var(--mono)}
.badge{font-size:10px;font-family:var(--mono);padding:2px 7px;
  border-radius:999px;border:1px solid var(--border)}
.badge.premium{border-color:var(--green);color:var(--green)}
.badge.free{border-color:var(--muted);color:var(--muted)}
.badge.duo{border-color:var(--blue);color:var(--blue)}
.badge.family{border-color:var(--yellow);color:var(--yellow)}
.overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);
  z-index:100;align-items:center;justify-content:center}
.overlay.open{display:flex}
.modal{background:var(--surface);border:1px solid var(--border);border-radius:14px;
  padding:22px;width:400px;max-width:95vw;display:flex;flex-direction:column;gap:12px}
.modal h3{font-size:14px;font-weight:600}
.mtabs{display:flex;gap:5px}
.mtab{padding:5px 13px;border-radius:6px;font-size:11px;cursor:pointer;
  border:1px solid var(--border);background:transparent;color:var(--muted);font-family:var(--sans)}
.mtab.active{background:var(--accent);color:#000;border-color:var(--accent);font-weight:600}
.tpane{display:none;flex-direction:column;gap:10px}
.tpane.active{display:flex}
.note{font-size:11px;color:var(--muted);font-family:var(--mono);line-height:1.6;
  padding:8px;background:var(--bg);border-radius:6px}
#lg{grid-area:lg;background:var(--surface);border-left:1px solid var(--border);display:flex;flex-direction:column}
.lh{padding:12px 14px;border-bottom:1px solid var(--border);display:flex;align-items:center;
  gap:7px;font-family:var(--mono);font-size:11px;font-weight:600}
.lclr{margin-left:auto;font-size:11px;color:var(--muted);cursor:pointer}
.lclr:hover{color:var(--text)}
#lo{flex:1;overflow-y:auto;padding:10px 12px;font-family:var(--mono);font-size:11px;
  line-height:1.7;display:flex;flex-direction:column;gap:1px}
.ll{display:flex;gap:7px}
.lt{color:var(--muted);flex-shrink:0}.lm{word-break:break-all}
.ll.info .lm{color:var(--text)}.ll.error .lm{color:var(--red)}.ll.warning .lm{color:var(--yellow)}.ll.ping{display:none}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
.blink{animation:pulse 1.8s infinite}
::-webkit-scrollbar{width:4px}
::-webkit-scrollbar-thumb{background:var(--border);border-radius:99px}
</style></head><body>
<header>
  <link rel="icon" type="image/png" href="data:image/png;base64,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">
  <img src="data:image/png;base64,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" width="28" height="28" style="border-radius:6px;object-fit:cover">
  <div><div class="logo-text">STREAM<span style="color:var(--accent)">GRID</span></div><div class="logo-sub">Multi-Device Streaming Engine</div></div>
  <div class="hdr-right">
    <button class="btn bg" onclick="openModal('guide-modal')" style="padding:4px 10px;font-size:11px;background:var(--surface2);color:var(--accent);font-weight:700;border:1px solid var(--border);border-radius:6px;cursor:pointer;display:flex;align-items:center;gap:5px">📘 Getting Started</button>
    <div style="font-size:11px;color:var(--muted);margin-right:8px;display:flex;align-items:center;gap:4px"><span>Help:</span> <a href="mailto:chestbot.support@gmail.com" style="color:var(--accent);text-decoration:none;font-weight:600">chestbot.support@gmail.com</a></div>
    <select class="plt-sel" id="plt-sel" onchange="selectPlatform(this.value)"></select>
    <div class="hk">Ctrl+Shift+S start &nbsp; Ctrl+Shift+X stop</div>
    <button class="theme-btn" onclick="toggleTheme()" id="theme-btn">&#127769;</button>
    <div id="tool-chips" style="display:flex;gap:6px"></div>
  </div>
</header>

<aside>
  <div class="sec">Connected Devices</div>
  <div class="sb-tools">
    <label style="font-size:11px;font-family:var(--mono);display:flex;align-items:center;gap:5px;cursor:pointer">
      <input type="checkbox" id="dev-select-all" class="dev-cb" onchange="toggleSelectAllDevices(this.checked)"> All
    </label>
    <button class="btn-sm mirror" onclick="launchScrcpyChecked()" style="margin-left:auto">&#128187; Mirror Selected</button>
  </div>
  <div class="dev-list" id="dev-list"><div class="no-dev">No devices found.<br>Connect via USB.</div></div>
  <button class="rfsh" onclick="refreshDevices()">&#8635; Refresh Devices</button>
  <div class="sec" style="margin-top:auto">Status</div>
  <div class="sg">
    <div class="si"><div class="sl">Selected</div><div class="sv off" id="st-dev">none</div></div>
    <div class="si"><div class="sl">Platform</div><div class="sv off" id="st-plt">&#8212;</div></div>
    <div class="si"><div class="sl">Proxy</div><div class="sv off" id="st-proxy">off</div></div>
    <div class="si"><div class="sl">Loops</div><div class="sv off" id="st-loops">0</div></div>
  </div>
</aside>

<main>
  <div class="panel" id="quick-guide-panel" style="border-left:4px solid var(--accent);background:var(--surface2);margin-bottom:14px">
    <div class="ph" style="cursor:pointer" onclick="openModal('guide-modal')">
      <h2 style="display:flex;align-items:center;gap:8px">📘 Beginners Setup Guide <span style="font-size:11px;font-family:var(--mono);color:var(--accent);font-weight:normal">(Click to open full step-by-step instructions)</span></h2>
      <div style="display:flex;align-items:center;gap:8px;margin-left:auto">
        <button class="btn bg" onclick="event.stopPropagation();openModal('guide-modal')" style="font-size:11px;padding:4px 12px;background:var(--accent);color:#000;font-weight:700">Open Full Guide</button>
        <button class="btn bo" onclick="event.stopPropagation();closeQuickGuide()" style="font-size:11px;padding:4px 8px;color:var(--muted)" title="Dismiss banner">✕ Close</button>
      </div>
    </div>
  </div>

  <div class="panel">
    <div class="ph"><h2 id="app-title">App Controls</h2><span class="tag" id="app-pkg">&#8212;</span></div>
    <div class="pb">
      <div class="br">
        <button class="btn bg" onclick="api('/api/app/launch','POST')">&#9654; Launch App</button>
        <button class="btn br2" onclick="api('/api/app/stop','POST')">&#9632; Force Stop</button>
        <button class="btn bo" onclick="api('/api/appium/playpause','POST')">&#9199; Play / Pause</button>
        <button class="btn bb" onclick="connectAllAppium()">&#9889; Connect Appium (All)</button>
      </div>
    </div>
  </div>

  <div class="panel">
    <div class="ph"><h2>Playlist Loop</h2><span class="tag">all devices &#183; independent timing</span></div>
    <div class="pb">
      <div class="field"><label>Song / Album / Playlist (Search Query or Direct URL)</label>
        <input id="loop-q" type="text" placeholder="e.g. Lofi beats OR https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"></div>
      <div class="fr">
        <div class="field"><label>Min seconds</label><input id="loop-min" type="number" value="40" min="5"></div>
        <div class="field"><label>Max seconds</label><input id="loop-max" type="number" value="60" min="5"></div>
      </div>
      <div class="br">
        <button class="btn bg" onclick="startAllLoops()">&#9654; Start All</button>
        <button class="btn br2" onclick="stopAllLoops()">&#9632; Stop All</button>
        <button class="btn bo" onclick="dimMuteAll()" title="Dims OLED screen to minimum & mutes media volume to 0">🌙 Dim & Mute</button>
        <button class="btn bg" style="padding:6px 12px;font-size:11px;background:#334155;color:#f8fafc" onclick="restoreAllBrightness()" title="Restores normal screen brightness">☀️ Restore Screen</button>
      </div>
      <div id="dev-loops" style="display:flex;flex-direction:column;gap:8px"></div>
    </div>
  </div>

  <div class="panel">
    <div class="ph"><h2>Screen Mirror</h2><span class="tag">scrcpy grid</span></div>
    <div class="pb">
      <div class="fr">
        <div class="field"><label>Columns</label><input id="sc-cols" type="number" value="2" min="1" max="6"></div>
        <div class="field"><label>Window Size (px)</label><input id="sc-size" type="number" value="800"></div>
      </div>
      <div class="br">
        <button class="btn bb" onclick="launchScrcpy()">&#8862; Launch Grid</button>
        <button class="btn br2" onclick="api('/api/scrcpy/stop','POST')">&#x2715; Close All</button>
      </div>
    </div>
  </div>

  <div class="panel">
    <div class="ph"><h2>Account Vault</h2><span class="tag">accounts.json</span></div>
    <div class="pb">
      <div class="acl" id="acc-list"><div style="font-size:13px;color:var(--muted)">No accounts saved.</div></div>
      <div class="br">
        <button class="btn bg" onclick="openAddAcc()">+ Add Account</button>
      </div>
    </div>
  </div>

  <div class="panel">
    <div class="ph"><h2>Residential Proxy</h2><span class="tag">mitmproxy tunnel</span></div>
    <div class="pb">
      <div class="fr">
        <div class="field" style="flex:2"><label>Host</label><input id="p-host" type="text" placeholder="gate.smartproxy.com"></div>
        <div class="field"><label>Port</label><input id="p-port" type="number" value="10000"></div>
      </div>
      <div class="fr">
        <div class="field"><label>Username</label><input id="p-user" type="text"></div>
        <div class="field"><label>Password</label><input id="p-pass" type="password"></div>
      </div>
      <div class="br">
        <button class="btn bg" onclick="setProxy()">&#8674; Apply Proxy</button>
        <button class="btn bb" onclick="verifyDeviceIp()">&#127760; Verify Device IP / VPN</button>
        <button class="btn bo" onclick="api('/api/proxy/clear','POST')">&#x2715; Clear</button>
      </div>
      <div id="ip-verify-res" style="display:none;margin-top:10px;padding:10px;background:var(--surface2);border:1px solid var(--border);border-radius:7px;font-family:var(--mono);font-size:12px;color:var(--green)"></div>
    </div>
  </div>

  <div class="panel">
    <div class="ph"><h2>WireGuard Generator</h2><span class="tag">routes all traffic</span></div>
    <div class="pb">
      <div class="fr">
        <div class="field" style="flex:2"><label>Server Host</label><input id="wg-host" type="text" placeholder="vpn.provider.com"></div>
        <div class="field"><label>Port</label><input id="wg-port" type="number" value="51820"></div>
      </div>
      <div class="field"><label>Server Public Key</label><input id="wg-key" type="text" placeholder="base64 public key"></div>
      <div class="fr">
        <div class="field"><label>DNS</label><input id="wg-dns" type="text" value="1.1.1.1"></div>
        <div class="field"><label>Allowed IPs</label><input id="wg-ips" type="text" value="0.0.0.0/0, ::/0"></div>
      </div>
      <div class="br"><button class="btn bb" onclick="genWg()">&#9881; Generate Config</button></div>
      <div id="wg-out" style="display:none;flex-direction:column;gap:8px">
        <textarea id="wg-cfg" readonly style="background:var(--bg);border:1px solid var(--border);border-radius:7px;padding:10px;color:var(--green);font-family:var(--mono);font-size:11px;resize:vertical;min-height:150px;width:100%;outline:none"></textarea>
        <div id="wg-pub" style="font-size:11px;color:var(--muted);font-family:var(--mono)"></div>
        <button class="btn bo" onclick="navigator.clipboard.writeText(document.getElementById('wg-cfg').value);logLine('Copied!')" style="align-self:flex-start">Copy</button>
      </div>
    </div>
  </div>

  <div class="panel">
    <div class="ph"><h2>ADB Shell Console</h2><span class="tag">adb shell</span></div>
    <div class="pb">
      <div style="display:flex;gap:8px">
        <input class="adb-i" id="adb-cmd" placeholder="input keyevent 85 ..." onkeydown="if(event.key==='Enter')runAdb()">
        <button class="btn bg" onclick="runAdb()">Run</button>
      </div>
    </div>
  </div>
</main>

<div id="lg">
  <div class="lh">
    <svg width="10" height="10" viewBox="0 0 12 12" fill="var(--accent)"><circle cx="6" cy="6" r="5" class="blink"/></svg>
    LIVE LOG
    <span class="lclr" onclick="document.getElementById('lo').innerHTML=''">clear</span>
  </div>
  <div id="lo"></div>
</div>

<div class="overlay" id="acc-modal">
  <div class="modal">
    <h3>Add Account</h3>
    <div class="field"><label>Email</label><input id="am-email" type="email"></div>
    <div class="field"><label>Password</label><input id="am-pass" type="password"></div>
    <div class="field"><label>Platform</label><select id="am-plt"></select></div>
    <div class="field"><label>Subscription</label>
      <select id="am-tier">
        <option value="premium">Premium</option><option value="free">Free</option>
        <option value="duo">Duo</option><option value="family">Family</option>
      </select>
    </div>
    <div class="field"><label>Region</label><input id="am-region" type="text" placeholder="NO, US, UK..."></div>
    <div class="field"><label>Notes</label><input id="am-notes" type="text"></div>
    <div class="br">
      <button class="btn bg" onclick="saveAccount()">Save</button>
      <button class="btn bo" onclick="closeModal('acc-modal')">Cancel</button>
    </div>
  </div>
</div>

<div class="overlay" id="guide-modal">
  <div class="modal" style="width:780px;max-width:92vw;max-height:85vh;overflow-y:auto">
    <div style="display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--border);padding-bottom:12px">
      <h3 style="font-size:16px;font-weight:700;display:flex;align-items:center;gap:8px">📘 StreamGrid — Beginners Getting Started Guide</h3>
      <button class="btn bo" onclick="closeModal('guide-modal')" style="padding:3px 8px;font-size:11px">✕ Close</button>
    </div>

    <!-- Step 1 -->
    <div style="background:var(--surface2);padding:14px;border-radius:10px;border:1px solid var(--border);margin-top:10px">
      <h4 style="color:var(--accent);font-size:14px;margin-bottom:8px">📋 Step 1: System Requirements & Prerequisites</h4>
      <ul style="font-size:12px;color:var(--text);line-height:1.6;padding-left:18px">
        <li><b>Android Smartphone(s):</b> Any physical Android phone running Android 7.0 or higher.</li>
        <li><b>USB Hub & Cables:</b> High-quality USB data cables connected to a powered USB hub or directly to your PC.</li>
        <li><b>Target Apps Installed:</b> Ensure Spotify (or your target streaming app) is pre-installed on each phone and logged into an account.</li>
        <li><b>Windows PC:</b> Windows 10 or 11 with StreamGrid.exe. No ADB/Appium installation required (built-in).</li>
      </ul>
    </div>

    <!-- Step 2 -->
    <div style="background:var(--surface2);padding:14px;border-radius:10px;border:1px solid var(--border)">
      <h4 style="color:var(--accent);font-size:14px;margin-bottom:8px">⚙️ Step 2: Enable USB Debugging on your Phones</h4>
      <ol style="font-size:12px;color:var(--text);line-height:1.6;padding-left:18px">
        <li>Open <b>Settings</b> on your Android phone.</li>
        <li>Scroll down to <b>About Phone</b> (or <i>System Info</i>).</li>
        <li>Locate <b>Build Number</b> and tap it <b>7 times continuously</b> until a toast message says <i>"You are now a developer!"</i>.</li>
        <li>Go back to <b>Settings → System → Developer Options</b>.</li>
        <li>Toggle ON <b>USB Debugging</b> (and <i>Install via USB</i> if prompted on Xiaomi/Oppo/Realme).</li>
        <li>Plug the phone into your PC via USB cable.</li>
        <li>Look at your phone screen: a popup will appear asking <i>"Allow USB debugging?"</i>. Check the box <b>"Always allow from this computer"</b> and tap <b>OK / Allow</b>.</li>
      </ol>
    </div>

    <!-- Step 3 -->
    <div style="background:var(--surface2);padding:14px;border-radius:10px;border:1px solid var(--border)">
      <h4 style="color:var(--accent);font-size:14px;margin-bottom:8px">⚡ Step 3: Connect Devices in StreamGrid</h4>
      <ol style="font-size:12px;color:var(--text);line-height:1.6;padding-left:18px">
        <li>In StreamGrid, look at the left sidebar panel under <b>Connected Devices</b>.</li>
        <li>Click <b>↻ Refresh Devices</b>. Your connected phone models will appear in the list.</li>
        <li>Click <b>⚡ Connect Appium (All)</b> in the main controls panel. StreamGrid will establish high-speed human-touch automation on all connected phones.</li>
      </ol>
    </div>

    <!-- Step 4 -->
    <div style="background:var(--surface2);padding:14px;border-radius:10px;border:1px solid var(--border)">
      <h4 style="color:var(--accent);font-size:14px;margin-bottom:8px">🔒 Step 4: IP Rotation & Proxy Setup (Optional)</h4>
      <ul style="font-size:12px;color:var(--text);line-height:1.6;padding-left:18px">
        <li><b>Proxy Settings:</b> Enter HTTP/SOCKS proxy credentials (Host, Port, Username, Password) in the Proxy panel and click <b>Apply Proxy</b>.</li>
        <li><b>WireGuard Generator:</b> Enter your server public key and endpoint to generate WireGuard kernel-level tunneling configs.</li>
        <li><b>Verify IP:</b> Click <b>🌐 Verify Device IP / VPN</b> to inspect public IP address, ISP, and city location for each connected phone.</li>
      </ul>
    </div>

    <!-- Step 5 -->
    <div style="background:var(--surface2);padding:14px;border-radius:10px;border:1px solid var(--border)">
      <h4 style="color:var(--accent);font-size:14px;margin-bottom:8px">▶ Step 5: Start Streaming Loops</h4>
      <ol style="font-size:12px;color:var(--text);line-height:1.6;padding-left:18px">
        <li>In the <b>Playlist Loop</b> panel, enter your Spotify track URL, album link, or search query.</li>
        <li>Set <b>Min seconds</b> (e.g. <code>40</code>) and <b>Max seconds</b> (e.g. <code>60</code>) per track.</li>
        <li>Click <b>▶ Start All</b> or press <code>Ctrl+Shift+S</code> to begin streaming across all phones simultaneously with human thumb typing emulation.</li>
      </ol>
    </div>

    <!-- Step 6 -->
    <div style="background:var(--surface2);padding:14px;border-radius:10px;border:1px solid var(--border)">
      <h4 style="color:var(--accent);font-size:14px;margin-bottom:8px">🌙 Step 6: OLED Battery Saver & Stealth Mute</h4>
      <p style="font-size:12px;color:var(--text);line-height:1.6">Click <b>🌙 Dim & Mute</b> to drop display brightness on all connected phones to 0% and mute device media audio. This reduces battery power consumption by ~70% and prevents OLED display burn-in during continuous streaming.</p>
    </div>

    <!-- Shortcuts & Support -->
    <div style="background:var(--bg);padding:14px;border-radius:10px;border:1px solid var(--border);display:flex;flex-wrap:wrap;justify-content:space-between;gap:12px;font-size:11px;font-family:var(--mono)">
      <div>
        <b>Keyboard Shortcuts:</b><br>
        <code>Ctrl+Shift+S</code>: Start All Loops &nbsp;|&nbsp; <code>Ctrl+Shift+X</code>: Stop All Loops<br>
        <code>Ctrl+Shift+L</code>: Launch App &nbsp;|&nbsp; <code>Ctrl+Shift+C</code>: Connect Appium
      </div>
      <div>
        <b>Need Help or Technical Support?</b><br>
        Email: <a href="mailto:chestbot.support@gmail.com" style="color:var(--accent)">chestbot.support@gmail.com</a>
      </div>
    </div>

    <div style="display:flex;justify-content:flex-end">
      <button class="btn bg" onclick="closeModal('guide-modal')" style="background:var(--accent);color:#000;font-weight:700">Got it! Close Guide</button>
    </div>
  </div>
</div>

<script>
const $=id=>document.getElementById(id);
let platforms=[],accounts=[],loopPoll=null,loopTotals={},checkedSerials=new Set();

function toggleTheme(){
  document.body.classList.toggle('light');
  $('theme-btn').textContent=document.body.classList.contains('light')?'\u{1F319}':'\u2600\uFE0F';
  localStorage.setItem('theme',document.body.classList.contains('light')?'light':'dark');
}
(()=>{if(localStorage.getItem('theme')==='light')document.body.classList.add('light');})();

document.addEventListener('keydown',e=>{
  if(e.ctrlKey&&e.shiftKey&&e.code==='KeyS'){e.preventDefault();startAllLoops();}
  if(e.ctrlKey&&e.shiftKey&&e.code==='KeyX'){e.preventDefault();stopAllLoops();}
  if(e.ctrlKey&&e.shiftKey&&e.code==='KeyL'){e.preventDefault();api('/api/app/launch','POST');}
  if(e.ctrlKey&&e.shiftKey&&e.code==='KeyC'){e.preventDefault();connectAllAppium();}
});

async function api(url,method='GET',body=null){
  try{
    const o={method,headers:{'Content-Type':'application/json'}};
    if(body) o.body=JSON.stringify(body);
    const r=await fetch(url,o);const d=await r.json();
    if(!d.ok&&d.error) logLine(d.error,'error');
    updateStatus();return d;
  }catch(e){logLine('Request failed: '+e.message,'error');}
}

async function loadPlatforms(){
  platforms=await fetch('/api/platforms').then(r=>r.json()).catch(()=>[]);
  const sel=$('plt-sel');
  sel.innerHTML=platforms.map(p=>`<option value="${p.id}">${p.name}</option>`).join('');
  const ap=$('am-plt');
  if(ap) ap.innerHTML=platforms.map(p=>`<option value="${p.id}">${p.name}</option>`).join('');
  applyAccent('spotify');
}
function applyAccent(id){
  const p=platforms.find(x=>x.id===id);
  if(p){document.documentElement.style.setProperty('--accent',p.color);
    $('app-title').textContent=p.name+' Controls';$('app-pkg').textContent=id.replace('_',' ');}
}
async function selectPlatform(id){
  const d=await api('/api/platform','POST',{platform:id});
  if(d&&d.ok) applyAccent(id);
}

async function refreshDevices(){
  const devs=await api('/api/devices');
  const list=$('dev-list');
  if(!devs||!devs.length){
    list.innerHTML='<div class="no-dev">No devices found.<br>Connect via USB.</div>';
    checkedSerials.clear();
    return;
  }
  list.innerHTML=devs.map(d=>{
    const isChecked=checkedSerials.has(d.serial)?'checked':'';
    return `
    <div class="dev-card" id="card-${d.serial}" onclick="selectDevice('${d.serial}')">
      <div class="dev-card-row">
        <input type="checkbox" class="dev-cb" ${isChecked} onclick="event.stopPropagation();toggleCheckDevice('${d.serial}',this.checked)">
        <div style="flex:1">
          <div class="dname">${d.model}</div>
          <div class="dserial">${d.serial}</div>
        </div>
      </div>
      <div class="dbadges">
        <span class="dbadge" id="b-conn-${d.serial}">appium</span>
        <span class="dbadge" id="b-loop-${d.serial}">loop</span>
      </div>
      <div class="dev-actions" onclick="event.stopPropagation()">
        <button class="btn-sm mirror" onclick="launchScrcpyForDevice('${d.serial}')">&#128187; Mirror</button>
        <button class="btn-sm" onclick="connectAppiumForDevice('${d.serial}')">&#9889; Appium</button>
      </div>
    </div>`;
  }).join('');
  updateDevLoopCards(devs);
}

function toggleCheckDevice(serial,checked){
  if(checked) checkedSerials.add(serial);
  else checkedSerials.delete(serial);
  const allCb=$('dev-select-all');
  if(allCb){
    const total=document.querySelectorAll('.dev-cb:not(#dev-select-all)').length;
    allCb.checked=checkedSerials.size>0&&checkedSerials.size===total;
  }
}

function toggleSelectAllDevices(checked){
  checkedSerials.clear();
  document.querySelectorAll('.dev-cb:not(#dev-select-all)').forEach(cb=>{
    cb.checked=checked;
    const serial=cb.getAttribute('onclick')?.match(/'([^']+)'/)?.[1];
    if(checked&&serial) checkedSerials.add(serial);
  });
}

async function selectDevice(serial){
  await api('/api/select','POST',{serial});
  document.querySelectorAll('.dev-card').forEach(c=>c.classList.remove('active'));
  const c=$('card-'+serial);if(c)c.classList.add('active');
}

function updateDevLoopCards(devs){
  const wrap=$('dev-loops');
  devs.forEach(d=>{
    if($('dlc-'+d.serial)) return;
    const el=document.createElement('div');
    el.className='dlc';el.id='dlc-'+d.serial;
    el.innerHTML=`
      <div class="dlch">
        <div class="dlcn">${d.model} <span style="font-family:var(--mono);font-size:10px;color:var(--muted)">${d.serial.slice(-6)}</span></div>
        <span id="dlc-st-${d.serial}" style="font-family:var(--mono);font-size:10px;color:var(--muted)">idle</span>
        <span id="dlc-cy-${d.serial}" style="font-family:var(--mono);font-size:10px;color:var(--muted)">0 cycles</span>
      </div>
      <div class="lbar"><div class="lfill" id="dlc-bar-${d.serial}" style="width:0%"></div></div>
      <div style="font-family:var(--mono);font-size:11px;color:var(--muted)" id="dlc-cd-${d.serial}">&#8212;</div>`;
    wrap.appendChild(el);
  });
}

async function connectAllAppium(){await api('/api/appium/connect','POST',{all:true});}
async function connectAppiumForDevice(serial){await api('/api/appium/connect','POST',{serial});}

async function startAllLoops(){
  const q=$('loop-q').value.trim();
  const mn=parseInt($('loop-min').value)||40;
  const mx=parseInt($('loop-max').value)||60;
  if(!q){logLine('Enter a playlist name or URL','warning');return;}
  logLine('Starting playlist loop...','info');
  Object.keys(loopTotals).forEach(k=>delete loopTotals[k]);
  const d=await api('/api/appium/loop/start','POST',{query:q,min_sec:mn,max_sec:mx,all:true});
  if(d&&d.ok){
    (d.started||[]).forEach(s=>loopTotals[s]=mx);
    startLoopPoll();
    logLine(`Playlist loop active on ${(d.started||[]).length} device(s)`, 'info');
  } else {
    logLine((d&&d.error)?d.error:'Failed to start loop — connect Appium first', 'error');
  }
}

async function stopAllLoops(){
  await api('/api/appium/loop/stop','POST',{all:true});
  if(loopPoll){clearInterval(loopPoll);loopPoll=null;}
  document.querySelectorAll('.lfill').forEach(el=>el.style.width='0%');
}

async function dimMuteAll(){
  await api('/api/stealth/hardware/dim_mute','POST',{all:true});
  logLine('🌙 OLED Dimmer (Brightness: 1) & Stealth Mute APPLIED to all devices', 'info');
}

async function restoreAllBrightness(){
  await api('/api/stealth/hardware/restore','POST',{all:true});
  logLine('☀️ Screen brightness restored to normal on all devices', 'info');
}

function startLoopPoll(){
  if(loopPoll) clearInterval(loopPoll);
  loopPoll=setInterval(async()=>{
    const st=await fetch('/api/appium/loop/status').then(r=>r.json()).catch(()=>null);
    if(!st) return;
    Object.entries(st).forEach(([serial,info])=>{
      const bar=$('dlc-bar-'+serial);const cd=$('dlc-cd-'+serial);
      const status=$('dlc-st-'+serial);const cycles=$('dlc-cy-'+serial);
      const bl=$('b-loop-'+serial);
      if(!bar) return;
      if(info.active){
        const rem=info.remaining||0;const tot=loopTotals[serial]||60;
        bar.style.width=((rem/tot)*100)+'%';
        if(cd) cd.textContent='next restart in '+rem+'s';
        if(status){status.textContent='● running';status.style.color='var(--yellow)';}
        if(bl) bl.classList.add('loop-on');
        if(cycles) cycles.textContent=(info.cycles||0)+' cycles';
      } else {
        bar.style.width='0%';
        if(cd) cd.textContent='idle';
        if(status){status.textContent='idle';status.style.color='var(--muted)';}
        if(bl) bl.classList.remove('loop-on');
      }
    });
  },1000);
}

async function launchScrcpy(){
  const list=Array.from(checkedSerials);
  const payload={columns:parseInt($('sc-cols').value)||2,size:parseInt($('sc-size').value)||800};
  if(list.length>0) payload.serials=list;
  await api('/api/scrcpy/launch','POST',payload);
}

async function launchScrcpyForDevice(serial){
  await api('/api/scrcpy/launch','POST',{
    serials:[serial],
    columns:parseInt($('sc-cols').value)||2,size:parseInt($('sc-size').value)||800});
}

async function launchScrcpyChecked(){
  const list=Array.from(checkedSerials);
  if(!list.length){
    logLine('No devices checked — select checkboxes in sidebar','warning');
    return;
  }
  await api('/api/scrcpy/launch','POST',{
    serials:list,
    columns:parseInt($('sc-cols').value)||2,size:parseInt($('sc-size').value)||800});
}

async function verifyDeviceIp(){
  logLine('Verifying device IP & VPN status via ADB...','info');
  const res = await api('/api/device/verify_ip', 'POST');
  const box = $('ip-verify-res');
  if(!box) return;
  box.style.display = 'block';
  if(res.ok && res.info){
    const inf = res.info;
    const loc = [inf.city, inf.region, inf.country].filter(Boolean).join(', ');
    box.style.color = 'var(--green)';
    box.innerHTML = `<div><strong>Public IP:</strong> ${inf.ip || 'Unknown'}</div>` +
                    (loc ? `<div><strong>Location:</strong> ${loc}</div>` : '') +
                    (inf.org ? `<div><strong>ISP / Provider:</strong> ${inf.org}</div>` : '');
    logLine(`Device IP verified: ${inf.ip} (${loc || inf.org || 'Active'})`, 'info');
  } else {
    box.style.color = 'var(--red)';
    box.innerHTML = `Failed to verify IP: ${res.error || 'Connection error'}`;
    logLine(`IP verification failed: ${res.error || 'Unknown error'}`, 'error');
  }
}

async function loadAccounts(){
  accounts=await fetch('/api/accounts').then(r=>r.json()).catch(()=>[]);renderAccounts();
}
function renderAccounts(){
  const list=$('acc-list');
  if(!accounts.length){list.innerHTML='<div style="font-size:13px;color:var(--muted)">No accounts saved.</div>';return;}
  list.innerHTML=accounts.map(a=>`
    <div class="acc">
      <div style="flex:1"><div class="ace">${a.email||'&#8212;'}</div>
        <div class="acm">${a.platform||''} ${a.region?'&#183; '+a.region:''} ${a.notes?'&#183; '+a.notes:''}</div></div>
      <span class="badge ${a.tier||'free'}">${(a.tier||'free').toUpperCase()}</span>
      <button class="btn bg" style="padding:5px 11px;font-size:11px" onclick="switchAcc('${a.id}')">Switch</button>
      <button class="btn bo" style="padding:5px 9px;font-size:11px" onclick="delAcc('${a.id}')">&#x2715;</button>
    </div>`).join('');
}
function openModal(id){const m=$(id);if(m)m.classList.add('open');}
function closeModal(id){const m=$(id);if(m)m.classList.remove('open');}
function openAddAcc(){openModal('acc-modal');}
function openRegModal(){openModal('reg-modal');}
function closeQuickGuide(){
  const p=$('quick-guide-panel');
  if(p) p.style.display='none';
  localStorage.setItem('hideQuickGuide','true');
}
(()=>{
  if(localStorage.getItem('hideQuickGuide')==='true'){
    window.addEventListener('DOMContentLoaded',()=>{
      const p=$('quick-guide-panel');
      if(p) p.style.display='none';
    });
  }
})();
function switchMtab(e,tab){
  document.querySelectorAll('.tpane').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.mtab').forEach(t=>t.classList.remove('active'));
  $(tab).classList.add('active');e.target.classList.add('active');
}
async function saveAccount(){
  const acc={email:$('am-email').value.trim(),password:$('am-pass').value,
    platform:$('am-plt').value,tier:$('am-tier').value,
    region:$('am-region').value.trim(),notes:$('am-notes').value.trim()};
  if(!acc.email){logLine('Email required','warning');return;}
  await api('/api/accounts','POST',acc);closeModal('acc-modal');await loadAccounts();
}
async function delAcc(id){await api('/api/accounts/'+id,'DELETE');await loadAccounts();}
async function switchAcc(id){await api('/api/accounts/switch','POST',{id});}
async function startGmailReg(){
  await api('/api/accounts/register/gmail','POST',{
    first_name:$('rg-fn').value.trim(),last_name:$('rg-ln').value.trim(),
    email:$('rg-un').value.trim()});
  closeModal('reg-modal');
}
async function startSpotifyReg(){
  await api('/api/accounts/register/spotify','POST',{
    email:$('rs-em').value.trim(),password:$('rs-pw').value,
    username:$('rs-nm').value.trim()});
  closeModal('reg-modal');
}
async function setProxy(){
  await api('/api/proxy/set','POST',{host:$('p-host').value,
    port:parseInt($('p-port').value)||10000,user:$('p-user').value,password:$('p-pass').value});
}
async function genWg(){
  const d=await api('/api/wireguard/generate','POST',{
    host:$('wg-host').value.trim(),port:parseInt($('wg-port').value)||51820,
    server_pubkey:$('wg-key').value.trim(),dns:$('wg-dns').value.trim(),
    allowed_ips:$('wg-ips').value.trim()});
  if(d&&d.ok){$('wg-cfg').value=d.config;
    $('wg-pub').textContent='Client pubkey: '+d.client_pubkey;$('wg-out').style.display='flex';}
}
async function runAdb(){
  const cmd=$('adb-cmd').value.trim();if(!cmd)return;
  await api('/api/adb/command','POST',{command:cmd});$('adb-cmd').value='';
}
async function updateStatus(){
  const s=await fetch('/api/status').then(r=>r.json()).catch(()=>null);if(!s)return;
  const set=(id,v,on)=>{const el=$(id);if(!el)return;el.textContent=v;el.className='sv '+(on?'on':'off');};
  set('st-dev',s.selected?s.selected.slice(-8):'none',!!s.selected);
  set('st-plt',s.platform||'&#8212;',!!s.platform);
  set('st-proxy',s.proxy_active?'on':'off',s.proxy_active);
  set('st-loops',s.active_loops,s.active_loops>0);
  (s.connected_drivers||[]).forEach(serial=>{
    const b=$('b-conn-'+serial);if(b)b.classList.add('conn');
  });
}
async function checkTools(){
  const t=await fetch('/api/tools').then(r=>r.json()).catch(()=>({}));
  $('tool-chips').innerHTML=Object.entries(t).map(([k,v])=>
    `<div class="chip ${v?'ok':'bad'}"><div class="dot"></div>${k}</div>`).join('');
}
function logLine(msg,level='info'){
  const o=$('lo');
  const t=new Date().toLocaleTimeString('en',{hour12:false,hour:'2-digit',minute:'2-digit',second:'2-digit'});
  const el=document.createElement('div');el.className='ll '+level;
  el.innerHTML=`<span class="lt">${t}</span><span class="lm">${msg}</span>`;
  o.appendChild(el);o.scrollTop=o.scrollHeight;
}
const es=new EventSource('/api/logs');
es.onmessage=e=>{const d=JSON.parse(e.data);if(d.level!=='ping')logLine(d.msg,d.level);};
(async()=>{
  await loadPlatforms();await checkTools();await refreshDevices();
  await loadAccounts();await updateStatus();
  logLine('StreamGrid ready  &#183;  Ctrl+Shift+S to start loop','info');
})();
</script></body></html>"""

# ===========================================
# API ROUTES
# ===========================================
@app.route('/api/devices')
def api_devices(): return jsonify(refresh_devices())

@app.route('/api/select', methods=['POST'])
def api_select():
    data = request.get_json(silent=True) or {}
    state['selected'] = data.get('serial')
    emit('Selected: ' + str(state['selected']))
    return jsonify({'ok': True})

@app.route('/api/platforms')
def api_platforms():
    return jsonify([{'id':k,'name':v['name'],'color':v['color']} for k,v in PLATFORMS.items()])

@app.route('/api/platform', methods=['POST'])
def api_platform():
    data = request.get_json(silent=True) or {}
    p = data.get('platform')
    if p not in PLATFORMS: return jsonify({'ok':False,'error':'Unknown platform'})
    state['platform'] = p
    emit('Platform -> ' + PLATFORMS[p]['name'])
    for serial,drv in list(state['drivers'].items()):
        try: drv.quit()
        except: pass
    state['drivers'].clear()
    return jsonify({'ok': True})

@app.route('/api/app/launch', methods=['POST'])
def api_launch():
    s = state['selected']
    if not s: return jsonify({'ok':False,'error':'No device selected'})
    p = plat()
    adb('shell','am','start','-n',p['package']+'/'+p['activity'],serial=s)
    emit(p['name']+' launching...')
    return jsonify({'ok': True})

@app.route('/api/app/stop', methods=['POST'])
def api_stop():
    s = state['selected']
    if not s: return jsonify({'ok':False,'error':'No device selected'})
    adb('shell','am','force-stop',plat()['package'],serial=s)
    emit(plat()['name']+' stopped')
    return jsonify({'ok': True})

def refocus_scrcpy_window(serial):
    try:
        import ctypes
        user32 = ctypes.windll.user32
        title = f"Android [{serial}]"
        hwnd = user32.FindWindowW(None, title)
        if hwnd:
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE = 9
            user32.SetForegroundWindow(hwnd)
            emit("Refocused scrcpy -> " + serial)
            return True
    except Exception:
        pass
    return False

@app.route('/api/scrcpy/launch', methods=['POST'])
def api_scrcpy():
    data = request.get_json(silent=True) or {}
    cols = int(data.get('columns', 2))
    size = int(data.get('size', 800))
    requested_serials = data.get('serials')

    if requested_serials:
        target_devs = [d for d in state['devices'] if d['serial'] in requested_serials]
    elif data.get('all'):
        target_devs = state['devices']
    elif state['selected']:
        target_devs = [d for d in state['devices'] if d['serial'] == state['selected']]
    else:
        target_devs = state['devices']

    if not target_devs:
        return jsonify({'ok': False, 'error': 'No target devices selected'})

    for i, d in enumerate(target_devs):
        serial = d['serial']
        if serial in state['scrcpy_procs'] and state['scrcpy_procs'][serial].poll() is None:
            if refocus_scrcpy_window(serial):
                continue
        col, row = i % cols, i // cols
        proc = launch_scrcpy_for(serial, x=col * (size + 10), y=row * (size + 40), size=size)
        if proc:
            state['scrcpy_procs'][serial] = proc
    return jsonify({'ok': True})

@app.route('/api/scrcpy/stop', methods=['POST'])
def api_scrcpy_stop():
    for s,p in list(state['scrcpy_procs'].items()):
        try: p.terminate()
        except Exception: pass
    state['scrcpy_procs'].clear()
    return jsonify({'ok':True})

@app.route('/api/proxy/set', methods=['POST'])
def api_proxy_set():
    data = request.get_json(silent=True) or {}
    host,port=data.get('host',''),int(data.get('port',10000))
    user,pwd=data.get('user',''),data.get('password','')
    s=state['selected']
    if not s: return jsonify({'ok':False,'error':'No device selected'})
    ah,ap=host,port
    if user and pwd:
        if state['proxy_proc'] and state['proxy_proc'].poll() is None:
            try: state['proxy_proc'].terminate()
            except Exception: pass
            state['proxy_proc'] = None
        proc=start_mitm_tunnel(host,port,user,pwd)
        if proc: state['proxy_proc']=proc; ah,ap='127.0.0.1',8118
    set_device_proxy(s,ah,ap); state['proxy_active']=True
    return jsonify({'ok':True})

@app.route('/api/proxy/clear', methods=['POST'])
def api_proxy_clear():
    s=state['selected']
    if s: clear_device_proxy(s)
    if state['proxy_proc']:
        try: state['proxy_proc'].terminate()
        except Exception: pass
        state['proxy_proc']=None
    state['proxy_active']=False
    return jsonify({'ok':True})

@app.route('/api/device/verify_ip', methods=['POST'])
def api_device_verify_ip():
    data = request.get_json(silent=True) or {}
    refresh_devices()
    
    serial = data.get('serial') or state.get('selected')
    if not serial and state.get('devices'):
        serial = state['devices'][0]['serial']
        
    if not serial:
        return jsonify({
            'ok': False,
            'error': 'No Android device detected over USB. Please connect your phone via USB and enable USB Debugging.'
        })

    # Push bundled static curl binary if present locally and missing on phone
    curl_local = os.path.join(os.path.dirname(__file__), "curl-aarch64")
    if os.path.exists(curl_local):
        out_check, _ = adb("shell", "ls", "/data/local/tmp/curl", serial=serial)
        if "No such file" in out_check or not out_check.strip():
            adb("push", curl_local, "/data/local/tmp/curl", serial=serial)
            adb("shell", "chmod", "755", "/data/local/tmp/curl", serial=serial)

    import json
    curl_bin = "/data/local/tmp/curl" if os.path.exists(curl_local) else "curl"
    
    commands = [
        [curl_bin, "-k", "-s", "--max-time", "5", "https://ipinfo.io/json"],
        [curl_bin, "-s", "--max-time", "5", "http://ipinfo.io/json"],
        ["curl", "-k", "-s", "--max-time", "5", "https://ipinfo.io/json"],
        ["wget", "--no-check-certificate", "-q", "-O", "-", "https://ipinfo.io/json"],
    ]

    for cmd in commands:
        out, code = adb("shell", *cmd, serial=serial)
        out = out.strip()
        if out and "{" in out and "ip" in out:
            try:
                ip_data = json.loads(out[out.find("{"):out.rfind("}")+1])
                return jsonify({'ok': True, 'serial': serial, 'info': ip_data})
            except Exception:
                pass

    # Guaranteed fallback: launch browser view intent directly on phone display
    adb("shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", "https://ipinfo.io/json", serial=serial)
    return jsonify({
        'ok': True,
        'serial': serial,
        'info': {
            'ip': 'Browser Opened on Device',
            'city': 'Check Phone Screen',
            'org': 'ipinfo.io/json launched in Chrome'
        }
    })

@app.route('/api/appium/connect', methods=['POST'])
def api_appium_connect():
    import traceback
    data = request.get_json(silent=True) or {}
    do_all = data.get('all', False)
    req_serial = data.get('serial')
    if req_serial:
        targets = [req_serial]
    elif do_all:
        targets = [d['serial'] for d in state['devices']]
    else:
        targets = [state['selected']]
    targets = [t for t in targets if t]
    if not targets: return jsonify({'ok': False, 'error': 'No devices target selected'})
    results = {}
    for serial in targets:
        try:
            drv = _make_driver(serial)
            if drv:
                state['drivers'][serial] = drv
                if serial not in state['watchdogs']:
                    wt = threading.Thread(target=_watchdog, args=(serial,), daemon=True)
                    state['watchdogs'][serial] = wt; wt.start()
                results[serial] = True
            else: results[serial] = False
        except Exception as e:
            print(traceback.format_exc()); results[serial] = False
    return jsonify({'ok': any(results.values()), 'results': results})

@app.route('/api/appium/playpause', methods=['POST'])
def api_appium_playpause():
    from appium.webdriver.common.appiumby import AppiumBy
    s=state['selected']; drv=state['drivers'].get(s)
    if not drv: return jsonify({'ok':False,'error':'Not connected'})
    try: drv.find_element(AppiumBy.ACCESSIBILITY_ID,plat()['play_label']).click(); return jsonify({'ok':True})
    except Exception as e: return jsonify({'ok':False,'error':str(e)})

@app.route('/api/appium/loop/start', methods=['POST'])
def api_loop_start():
    data = request.get_json(silent=True) or {}
    query=data.get('query','').strip()
    min_sec=int(data.get('min_sec',40))
    max_sec=int(data.get('max_sec',60))
    do_all=data.get('all',True)
    if not query: return jsonify({'ok':False,'error':'Empty query — enter a song, playlist, or URL'})

    targets=[d['serial'] for d in state['devices']] if do_all else [state['selected']]
    targets=[t for t in targets if t]
    if not targets: return jsonify({'ok':False,'error':'No devices found — connect phone over USB'})

    for serial in targets:
        if serial not in state['drivers'] or not state['drivers'][serial]:
            emit(f"[{serial[-6:]}] Auto-connecting Appium driver...")
            try:
                drv = _make_driver(serial)
                if drv:
                    state['drivers'][serial] = drv
                    if serial not in state['watchdogs']:
                        wt = threading.Thread(target=_watchdog, args=(serial,), daemon=True)
                        state['watchdogs'][serial] = wt; wt.start()
            except Exception as e:
                emit(f"[{serial[-6:]}] Appium connect error: {e}", "error")

    connected_targets=[t for t in targets if t in state['drivers'] and state['drivers'][t]]
    if not connected_targets:
        return jsonify({'ok':False,'error':'No connected drivers — click Connect Appium first or check Appium server'})

    started=[]
    for serial in connected_targets:
        if state['loops'].get(serial,{}).get('active'):
            started.append(serial)
            continue
        ls={'active':True,'next_restart':None,'cycles':0,'total_time':0}
        state['loops'][serial]=ls
        t=threading.Thread(target=_loop_worker,args=(serial,query,min_sec,max_sec),daemon=True)
        ls['thread']=t; t.start(); started.append(serial)
    return jsonify({'ok':bool(started),'started':started})

@app.route('/api/appium/loop/stop', methods=['POST'])
def api_loop_stop():
    data = request.get_json(silent=True) or {}
    do_all=data.get('all',False)
    targets=list(state['loops'].keys()) if do_all else [state['selected']]
    for serial in targets:
        if serial in state['loops']:
            state['loops'][serial]['active']=False
            state['loops'][serial]['next_restart']=None
            restore_hardware_settings(serial)
    emit('Loop stop requested')
    return jsonify({'ok':True})

@app.route('/api/stealth/hardware/dim_mute', methods=['POST'])
def api_stealth_dim_mute():
    data = request.get_json(silent=True) or {}
    do_all = data.get('all', True)
    targets = [d['serial'] for d in state['devices']] if do_all else ([state['selected']] if state['selected'] else [])
    targets = [t for t in targets if t]
    if not targets:
        return jsonify({'ok': False, 'error': 'No devices connected'})
    for serial in targets:
        apply_stealth_hardware_settings(serial)
    return jsonify({'ok': True, 'count': len(targets)})

@app.route('/api/stealth/hardware/restore', methods=['POST'])
def api_stealth_restore():
    data = request.get_json(silent=True) or {}
    do_all = data.get('all', True)
    targets = [d['serial'] for d in state['devices']] if do_all else ([state['selected']] if state['selected'] else [])
    targets = [t for t in targets if t]
    if not targets:
        return jsonify({'ok': False, 'error': 'No devices connected'})
    for serial in targets:
        restore_hardware_settings(serial)
    return jsonify({'ok': True, 'count': len(targets)})

@app.route('/api/appium/loop/status')
def api_loop_status():
    out={}
    for serial,loop in state['loops'].items():
        nxt=loop.get('next_restart')
        out[serial]={'active':loop.get('active',False),
            'remaining':max(0,int(nxt-time.time())) if nxt else None,
            'cycles':loop.get('cycles',0),'total_time':loop.get('total_time',0)}
    return jsonify(out)

@app.route('/api/accounts', methods=['GET'])
def api_acc_get(): return jsonify(load_vault())

@app.route('/api/accounts', methods=['POST'])
def api_acc_add():
    acc = request.get_json(silent=True) or {}
    if not acc.get('email'): return jsonify({'ok':False,'error':'Email required'})
    vault=load_vault()
    acc['id']=str(int(time.time()*1000))
    vault.append(acc); save_vault(vault)
    emit('Account added: '+acc.get('email',''))
    return jsonify({'ok':True,'id':acc['id']})

@app.route('/api/accounts/<acc_id>', methods=['PUT'])
def api_acc_update(acc_id):
    data = request.get_json(silent=True) or {}
    vault=load_vault()
    for i,a in enumerate(vault):
        if str(a.get('id'))==str(acc_id):
            vault[i]={**a,**data,'id':acc_id}; save_vault(vault); return jsonify({'ok':True})
    return jsonify({'ok':False,'error':'Not found'})

@app.route('/api/accounts/<acc_id>', methods=['DELETE'])
def api_acc_delete(acc_id):
    save_vault([a for a in load_vault() if str(a.get('id'))!=str(acc_id)])
    return jsonify({'ok':True})

@app.route('/api/accounts/switch', methods=['POST'])
def api_acc_switch():
    data = request.get_json(silent=True) or {}
    acc_id=data.get('id')
    acc=next((a for a in load_vault() if str(a.get('id'))==str(acc_id)),None)
    if not acc: return jsonify({'ok':False,'error':'Not found'})
    s=state['selected']; drv=state['drivers'].get(s)
    if not drv: return jsonify({'ok':False,'error':'Connect Appium first'})
    threading.Thread(target=do_account_switch,args=(s,drv,acc),daemon=True).start()
    return jsonify({'ok':True})

@app.route('/api/accounts/register/gmail', methods=['POST'])
def api_reg_gmail():
    data = request.get_json(silent=True) or {}
    s=state['selected']; drv=state['drivers'].get(s)
    if not drv: return jsonify({'ok':False,'error':'Connect Appium first'})
    threading.Thread(target=do_gmail_register,
        args=(s,drv,data.get('first_name',''),data.get('last_name',''),data.get('email','')),
        daemon=True).start()
    return jsonify({'ok':True})

@app.route('/api/accounts/register/spotify', methods=['POST'])
def api_reg_spotify():
    data = request.get_json(silent=True) or {}
    s=state['selected']; drv=state['drivers'].get(s)
    if not drv: return jsonify({'ok':False,'error':'Connect Appium first'})
    threading.Thread(target=do_spotify_register,
        args=(s,drv,data.get('email',''),data.get('password',''),data.get('username','')),
        daemon=True).start()
    return jsonify({'ok':True})

@app.route('/api/wireguard/generate', methods=['POST'])
def api_wg():
    import base64
    data = request.get_json(silent=True) or {}
    host,port=data.get('host',''),int(data.get('port',51820))
    spub=data.get('server_pubkey','')
    client_ip=data.get('client_ip','10.0.0.2/32')
    if not host or not spub: return jsonify({'ok':False,'error':'Host and key required'})
    try:
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
        from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat,PrivateFormat,NoEncryption
        priv=X25519PrivateKey.generate()
        pb=base64.b64encode(priv.private_bytes(Encoding.Raw,PrivateFormat.Raw,NoEncryption())).decode()
        pub=base64.b64encode(priv.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)).decode()
    except ImportError: return jsonify({'ok':False,'error':'pip install cryptography'})
    cfg='\n'.join(['[Interface]','PrivateKey = '+pb,'Address = '+client_ip,'DNS = '+data.get('dns','1.1.1.1'),'',
        '[Peer]','PublicKey = '+spub,'Endpoint = '+host+':'+str(port),
        'AllowedIPs = '+data.get('allowed_ips','0.0.0.0/0, ::/0'),'PersistentKeepalive = 25'])+'\n'
    return jsonify({'ok':True,'config':cfg,'client_pubkey':pub})

@app.route('/api/adb/command', methods=['POST'])
def api_adb():
    import shlex
    data = request.get_json(silent=True) or {}
    cmd=data.get('command','').strip()
    s=state['selected']
    if not cmd: return jsonify({'ok':False,'error':'Empty'})
    try: args=shlex.split(cmd)
    except Exception: args=cmd.split()
    out,code=adb('shell',*args,serial=s)
    emit('$ adb shell '+cmd+'\n  -> '+(out or '(no output)'))
    return jsonify({'ok':code==0,'output':out})

@app.route('/api/appium/server/status')
def api_appium_server_status():
    return jsonify({'running': is_appium_running(), 'port': 4723})

@app.route('/api/appium/server/start', methods=['POST'])
def api_appium_server_start():
    ok = ensure_appium_server()
    return jsonify({'ok': ok, 'running': is_appium_running()})

@app.route('/api/appium/server/stop', methods=['POST'])
def api_appium_server_stop():
    if state.get('appium_server_proc'):
        try: state['appium_server_proc'].terminate()
        except Exception: pass
        state['appium_server_proc'] = None
    clean_port_4723()
    emit("Appium server stopped.")
    return jsonify({'ok': True, 'running': is_appium_running()})

@app.route('/api/tools')
def api_tools():
    res = {k: bool(shutil.which(k)) for k in ['adb', 'scrcpy', 'appium', 'mitmdump']}
    res['appium_running'] = is_appium_running()
    return jsonify(res)

@app.route('/api/status')
def api_status():
    active=sum(1 for l in state['loops'].values() if l.get('active'))
    return jsonify({'selected':state['selected'],'platform':state['platform'],
        'proxy_active':state['proxy_active'],'scrcpy_open':list(state['scrcpy_procs'].keys()),
        'active_loops':active,'connected_drivers':list(state['drivers'].keys())})

@app.route('/api/logs')
def api_logs():
    def gen():
        while True:
            try: item=log_queue.get(timeout=30); yield 'data: '+json.dumps(item)+'\n\n'
            except queue.Empty: yield 'data: {"msg":"","level":"ping"}\n\n'
    return Response(gen(),mimetype='text/event-stream',
        headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})

@app.route('/')
def index(): return UI_HTML

if __name__ == '__main__':
    def start_flask():
        app.run(host='127.0.0.1', port=5050, debug=False, threaded=True)

    threading.Thread(target=start_flask, daemon=True).start()
    print('\n  StreamGrid — Multi-Device Streaming Engine')
    print('  Support Contact: chestbot.support@gmail.com')
    print('  http://127.0.0.1:5050\n')
    time.sleep(0.8)

    try:
        import webview
        webview.create_window(
            title='StreamGrid',
            url='http://127.0.0.1:5050',
            width=1280,
            height=850,
            min_size=(900, 600),
            resizable=True
        )
        webview.start()
    except Exception as e:
        print(f"Native window error: {e}. Opening web browser fallback...")
        webbrowser.open('http://127.0.0.1:5050')
        while True:
            time.sleep(1)