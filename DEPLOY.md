# Dual Engine Deploy — Quick Steps

## 1. Upload the new files to VPS
scp aggregator_engine.py main.py root@45.151.152.197:~/sports-arb-bot/

## 2. Add TELEGRAM_TOKEN and CHAT_ID to config.py if not already there
# Open config.py and confirm these exist:
#   TELEGRAM_TOKEN = "8585092786:AAGgFJjqnpuFvKvVCucnhgGjsJkgw9xlLpg"
#   CHAT_ID        = "-1003996115930"

## 3. Quick smoke test for Engine 2 alone (before restarting the bot)
cd ~/sports-arb-bot
oddsharvester upcoming -s football -d $(date +%Y%m%d) -m 1x2 -f json -o /tmp/test_run --headless
cat /tmp/test_run.json | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'Got {len(d)} matches')"

## 4. Restart the bot in the arb screen session
screen -S arb
# Ctrl+C to stop the running bot, then:
python main.py

## 5. What you should see in logs
# [E1] Engine 1 scan loop started.
# [AGG] Engine 2 started (OddsPortal aggregator)
# [E1] Scanning X live + Y pre-match
# ... (every 10 min) ...
# [AGG] Scraping OddsPortal: sport=football market=1x2
# [AGG] Cycle done | sport=football | candidates=N | alerted=M

## 6. Telegram alert format
# Engine 1 arbs look the same as before
# Engine 2 arbs will appear as:
#
#   🔍 [AGG] Aggregator Arb Found!
#
#   ⚽ Team A vs Team B
#   📊 Market: 1x2
#   💰 Profit: 5.30%
#
#   Best odds per side:
#     • Home: 2.45 @ Pinnacle
#     • Away: 3.10 @ Bet365
#     • Draw: 4.20 @ 1xBet
#
#   ⏰ Kickoff: 2026-05-13T18:00:00Z
#   ⚠️ Verify odds are still live before placing bets

## Troubleshooting
# "oddsharvester not found"  → pip install oddsharvester --break-system-packages
# "playwright not found"     → playwright install chromium
# Engine 2 never alerts      → lower AGG_MIN_PROFIT in aggregator_engine.py (try 1.5)
# Too many alerts            → raise AGG_MIN_PROFIT or reduce AGG_MAX_ALERTS
