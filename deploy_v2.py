#!/usr/bin/env python3
"""Deploy trading bot - try standalone Docker app approach."""
import json, base64, requests, sys, time, gzip

TOKEN = "2|3AWVtArQw1IBG0LK0YDvtQc2M5K7dlIJorNHkFDD64fe242f"
BASE = "https://master.jagadtix.id/api/v1"
H = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

def api(method, path, data=None, timeout=15):
    r = requests.request(method, f"{BASE}{path}", headers=H, json=data, timeout=timeout)
    try:
        return r.status_code, r.json()
    except:
        return r.status_code, {"raw": r.text[:500]}

# 1. Clean up old mess (delete all our test services)
code, services = api("GET", "/services?per_page=50")
if code == 200 and isinstance(services, list):
    for s in services:
        name = s.get("name", "")
        uuid = s.get("uuid", "")
        if "XAUUSD" in name or "TRACE" in name or "Test " in name or "IMG test" in name or "NETWORK" in name:
            api("DELETE", f"/services/{uuid}")
            print(f"Deleted: {name} ({uuid})")

time.sleep(3)

# 2. Try creating a STANDALONE DOCKER application instead of Docker Compose
# The key fields might be different
print("\n=== Create Standalone Docker App ===")
payload = {
    "project_uuid": "ifw1jn4t1gg7vozhtjd65kq8",
    "server_uuid": "cg8kw8oss0kswso0gg4kg8o8",
    "environment_name": "production",
    "name": "XAUUSD Bot",
    "image": "ghcr.io/goauthentik/server:2025.10.3",
    "command": "sleep 9999",
    "port": 9000,
    "ports": "",
}

for endpoint in ["/applications", "/services"]:
    code, resp = api("POST", endpoint, payload)
    print(f"POST {endpoint}: {code}")
    if isinstance(resp, dict):
        print(f"  Response: {json.dumps(resp, indent=2)[:300]}")
    else:
        print(f"  Response: {resp}")

# 3. Try creating a Docker Compose service with ALL possible fields
print("\n=== Create service with full fields ===")
with open("bot.py", "rb") as f:
    bot_b64 = base64.b64encode(gzip.compress(f.read())).decode()

COMPOSE = f"""services:
  xauusd-bot:
    image: ghcr.io/goauthentik/server:2025.10.3
    container_name: xauusd-trading-bot
    restart: unless-stopped
    command: server
    environment:
      - BYBIT_API_KEY=CHANGE_ME
      - BYBIT_API_SECRET=CHANGE_ME
      - USE_TESTNET=true
      - SYMBOL=XAUUSD
      - ACCOUNT_BALANCE=100
      - RISK_PER_TRADE_PCT=10
      - MAX_TRADES_PER_DAY=5
      - MAX_DAILY_LOSS_PCT=20
      - CHECK_INTERVAL=300
    command: >
      sh -c "pip install pybit python-telegram-bot pandas numpy ta &&
      python3 -c 'import base64,gzip; open(chr(47)+chr(97)+chr(112)+chr(112)+chr(47)+chr(98)+chr(111)+chr(116)+chr(46)+chr(112)+chr(121),chr(119)).write(gzip.decompress(base64.b64decode(chr(39)+chr(98)+chr(111)+chr(116)+chr(95)+chr(98)+chr(54)+chr(52)+chr(39))).decode())' &&
      python3 /app/bot.py"
"""

# Actually wait, the Python code with the chr() approach is too hacky. Let me use a simpler approach.
# The problem was base64 in shell. Let me just use plain Python decoding.

# Actually, authentik image runs as 'server' or 'worker' command. Let me just test if ANY compose works
SIMPLE_COMPOSE = """services:
  test:
    image: ghcr.io/goauthentik/server:2025.10.3
    command: ["sh", "-c", "sleep 9999"]
"""

code, resp = api("POST", "/services", {
    "project_uuid": "ifw1jn4t1gg7vozhtjd65kq8",
    "server_uuid": "cg8kw8oss0kswso0gg4kg8o8",
    "environment_name": "production",
    "name": "SLEEP TEST",
    "docker_compose_raw": base64.b64encode(SIMPLE_COMPOSE.encode()).decode(),
})
print(f"Create sleep test: {code}")
if code in (200, 201):
    uuid = resp.get("uuid")
    print(f"UUID: {uuid}")

    # Start it
    code2, resp2 = api("GET", f"/services/{uuid}/start")
    print(f"Start: {code2} {resp2}")

    # Wait 60s and check
    for i in range(4):
        time.sleep(30)
        code3, resp3 = api("GET", f"/services/{uuid}")
        if code3 == 200:
            s = resp3.get("status", "?")
            apps = resp3.get("applications", [])
            for a in apps:
                cnt = a.get("container")
                ctxt = "container" if cnt else "no_container"
                print(f"[{i*30+30}s] svc={s} app={a['status']} {ctxt}")
