import app

s = "AQH7N18409001833"
print(f"Testing HTTP endpoints on device {s}...")

endpoints = [
    ("ipinfo HTTP", ["/data/local/tmp/curl", "-s", "--max-time", "6", "http://ipinfo.io/json"]),
    ("ipify HTTP", ["/data/local/tmp/curl", "-s", "--max-time", "6", "http://api.ipify.org"]),
    ("ifconfig HTTP", ["/data/local/tmp/curl", "-s", "--max-time", "6", "http://ifconfig.me"]),
    ("icanhazip HTTP", ["/data/local/tmp/curl", "-s", "--max-time", "6", "http://icanhazip.com"]),
    ("checkip HTTP", ["/data/local/tmp/curl", "-s", "--max-time", "6", "http://checkip.amazonaws.com"]),
]

for name, cmd in endpoints:
    out, code = app.adb("shell", *cmd, serial=s)
    print(f"{name}: out={repr(out)}, code={code}")
