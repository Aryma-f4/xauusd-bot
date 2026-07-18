#!/usr/bin/env python3
"""Deploy XAUUSD trading bot to Coolify."""
import json, base64, requests, gzip, time, sys

TOKEN = "2|3AWVtArQw1IBG0LK0YDvtQc2M5K7dlIJorNHkFDD64fe242f"
BASE = "https://master.jagadtix.id/api/v1"
headers = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

# ── Step 1: Delete any existing service ──
SVC_UUID = "wu1bn2g04sntx4drhb1b4xrb"  # old one that was stuck

print("=== Deleting old service ===")
r = requests.delete(f"{BASE}/services/{SVC_UUID}", headers=headers, timeout=10)
print(f"Delete old: {r.status_code}")

# Also delete the test service if it still exists
r2 = requests.delete(f"{BASE}/services/jxw8lr005zsds6sprd7kvctk", headers=headers, timeout=10)
print(f"Delete test: {r2.status_code}")

# ── Step 2: Read bot code and encode ──
with open("bot.py", "rb") as f:
    bot_b64 = base64.b64encode(gzip.compress(f.read())).decode()

print(f"Bot encoded: {len(bot_b64)} chars")

# ── Step 3: Create the compose ──
# Use python:3.12-slim (known-working image on ghcr.io ecosystem)
# Decode bot via Python itself (no apt/base64/gunzip needed)
COMPOSE = f"""services:
  xauusd-bot:
    image: python:3.12-slim
    container_name: xauusd-trading-bot
    restart: unless-stopped
    volumes:
      - xauusd-data:/app/data
    environment:
      - BYBIT_API_KEY=CHANGE_ME_VIA_COOLIFY_UI
      - BYBIT_API_SECRET=CHANGE_ME_VIA_COOLIFY_UI
      - USE_TESTNET=true
      - SYMBOL=XAUUSD
      - ACCOUNT_BALANCE=100
      - RISK_PER_TRADE_PCT=10
      - MAX_TRADES_PER_DAY=5
      - MAX_DAILY_LOSS_PCT=20
      - CHECK_INTERVAL=300
      - TELEGRAM_TOKEN=
      - TELEGRAM_CHAT_ID=
    command: >
      sh -c "pip install -q --no-cache-dir pybit python-telegram-bot pandas numpy ta &&
      python3 -c \\"import base64,gzip; open('/app/bot.py','w').write(gzip.decompress(base64.b64decode('{bot_b64}')).decode())\\" &&
      python3 /app/bot.py"
volumes:
  xauusd-data:
    driver: local
"""

# Check compose is valid YAML
print(f"Compose: {len(COMPOSE)} chars")
print("First 200 chars:", COMPOSE[:200])

compose_b64 = base64.b64encode(COMPOSE.encode()).decode()

# ── Step 4: Create the service ──
print("\n=== Creating service ===")
payload = {
    "project_uuid": "ifw1jn4t1gg7vozhtjd65kq8",
    "server_uuid": "cg8kw8oss0kswso0gg4kg8o8",
    "environment_name": "production",
    "name": "XAUUSD Trading Bot",
    "docker_compose_raw": compose_b64,
}

r = requests.post(f"{BASE}/services", headers=headers, json=payload, timeout=15)
print(f"Create: {r.status_code}")
resp = r.json()
print(json.dumps(resp, indent=2)[:500])

if r.status_code not in (200, 201):
    sys.exit(1)

svc_uuid = resp.get("uuid")
print(f"\nService UUID: {svc_uuid}")

# ── Step 5: Deploy ──
print("\n=== Deploying ===")
r = requests.post(f"{BASE}/deploy?uuid={svc_uuid}&type=service", headers=headers, timeout=15)
print(f"Deploy: {r.status_code}")
print(json.dumps(r.json(), indent=2)[:300])

print(f"\n✅ Service deployed! UUID: {svc_uuid}")
print("Check status with:")
print(f"  curl -s {BASE}/services/{svc_uuid} -H 'Authorization: Bearer {TOKEN[:10]}...'")
