import urllib.request

url = "https://github.com/moparisthebest/static-curl/releases/download/v8.4.0/curl-aarch64"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
try:
    with urllib.request.urlopen(req) as resp, open("curl-aarch64", "wb") as f:
        f.write(resp.read())
    print("Downloaded curl-aarch64 successfully!")
except Exception as e:
    print("Download failed:", e)
