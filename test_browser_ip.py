import app, time

s = "AQH7N18409001833"
print("Launching browser view intent for ipinfo.io/json...")
app.adb("shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", "https://ipinfo.io/json", serial=s)

time.sleep(3)
print("Done! Browser launched on phone screen.")
