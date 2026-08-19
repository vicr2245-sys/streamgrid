import app

s = "AQH7N18409001833"
print(f"Testing direct IP query with Host header on device {s}...")

# ipinfo.io IP is 104.21.37.198 / 172.67.189.176
cmd = ["/data/local/tmp/curl", "-k", "-s", "--max-time", "6", "-H", "Host: ipinfo.io", "https://104.21.37.198/json"]
out1, c1 = app.adb("shell", *cmd, serial=s)
print("Direct IP out 1:", repr(out1), c1)

cmd2 = ["/data/local/tmp/curl", "-k", "-s", "--max-time", "6", "-H", "Host: ipinfo.io", "https://172.67.189.176/json"]
out2, c2 = app.adb("shell", *cmd2, serial=s)
print("Direct IP out 2:", repr(out2), c2)

cmd3 = ["/data/local/tmp/curl", "-s", "--max-time", "6", "http://api.ipify.org"]
out3, c3 = app.adb("shell", *cmd3, serial=s)
print("Ipify out:", repr(out3), c3)
