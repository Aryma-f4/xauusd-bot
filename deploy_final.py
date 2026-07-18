#!/usr/bin/env python3
"""Final deploy script for XAUUSD trading bot on Coolify."""
import json, base64, requests, gzip, time, sys

TOKEN = "2|3AWVtArQw1IBG0LK0YDvtQc2M5K7dlIJorNHkFDD64fe242f"
BASE = "https://master.jagadtix.id/api/v1"
H = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

def api(method, path, data=None, timeout=15):
    """Retry API call up to 3 times on failure."""
    for attempt in range(3):
        try:
            r = requests.request(method, BASE + path, headers=H, json=data, timeout=timeout)
            try: return r.status_code, r.json()
            except: return r.status_code, {}
        except Exception as e:
            if attempt < 2:
                time.sleep(3)
            else:
                print(f"API error after 3 retries: {e}")
                return 0, {}

# Read bot code
with open("bot.py", "rb") as f:
    bot_b64 = base64.b64encode(gzip.compress(f.read())).decode()

# Create compose using ghcr.io/goauthentik/server (only image that works on this server)
# Install Python + pip via apk, then run bot
COMPOSE = f"""services:
  xauusd-bot:
    image: ghcr.io/goauthentik/server:2025.10.3
    container_name: xauusd-bot
    restart: unless-stopped
    volumes:
      - xauusd-bot-data:/app/data
    environment:
      - BYBIT_API_KEY=SET_IN_COOLIFY_UI
      - BYBIT_API_SECRET=SET_IN_COOLIFY_UI
      - USE_TESTNET=true
      - SYMBOL=XAUUSD
      - ACCOUNT_BALANCE=100
      - RISK_PER_TRADE_PCT=10
      - MAX_TRADES_PER_DAY=5
      - MAX_DAILY_LOSS_PCT=20
      - CHECK_INTERVAL=300
      - TELEGRAM_TOKEN=
      - TELEGRAM_CHAT_ID=
    command: ["sh", "-c", "pip3 install --break-system-packages -q pybit python-telegram-bot pandas numpy ta 2>/dev/null && python3 -c 'import base64,gzip; open(\"/app/bot.py\",\"w\").write(gzip.decompress(base64.b64decode(\"{bot_b64}\")).decode())' && python3 /app/bot.py"]

volumes:
  xauusd-bot-data:
    driver: local
"""

# Step 1: Delete any existing service with same name
code, services = api("GET", "/services?per_page=50")
if code == 200 and isinstance(services, list):
    for s in services:
        if s.get("name") == "XAUUSD Bot":
            api("DELETE", f"/services/{s['uuid']}")
            print("Deleted old XAUUSD Bot")
            time.sleep(2)

# Step 2: Create the service
print("Creating service...")
B64 = base64.b64encode(COMPOSE.encode()).decode()
code, resp = api("POST", "/services", {
    "project_uuid": "ifw1jn4t1gg7vozhtjd65kq8",
    "server_uuid": "cg8kw8oss0kswso0gg4kg8o8",
    "environment_name": "production",
    "name": "XAUUSD Bot",
    "docker_compose_raw": B64,
}, timeout=20)
if code not in (200, 201) or not resp.get("uuid"):
    print(f"Create failed: {code} {resp}")
    sys.exit(1)

svc_uuid = resp["uuid"]
print(f"Created: {svc_uuid}")

# Step 3: Deploy
print("Deploying...")
api("POST", f"/deploy?uuid={svc_uuid}&type=service", timeout=20)

# Step 4: Monitor
print("\nMonitoring (120s)...")
for i in range(4):
    time.sleep(30)
    code2, d = api("GET", f"/services/{svc_uuid}")
    if code2 == 200:
        apps = d.get("applications", [])
        svc_st = d.get("status", "?")
        app_st = apps[0].get("status", "?") if apps else "?"
        print(f"[{i*30+30}s] svc={svc_st} app={app_st}")
        if "healthy" in svc_st or "running" in svc_st:
            print("✅ Service running!")
            break
    else:
        print(f"[{i*30+30}s] API error")

# Final status
code3, final = api("GET", f"/services/{svc_uuid}")
if code3 == 200:
    print(f"\nFinal status: {final.get('status')}")
    print(f"Service UUID: {svc_uuid}")
    for a in final.get("applications", []):
        print(f"  App: {a['name']} status={a['status']}")
