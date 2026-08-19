import app, json, time

devs = app.refresh_devices()
print("Devs:", devs)
if devs:
    s = devs[0]['serial']
    print(f"Testing Appium driver fetch on {s}...")
    drv = app._make_driver(s)
    if drv:
        drv.get("https://ipinfo.io/json")
        time.sleep(2)
        src = drv.page_source
        print("Page source:", repr(src[:400]))
        if "{" in src and "ip" in src:
            try:
                json_str = src[src.find("{"):src.rfind("}")+1]
                print("Parsed IP data:", json.loads(json_str))
            except Exception as e:
                print("JSON parse error:", e)
