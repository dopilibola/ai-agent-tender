#!/data/data/com.termux/files/usr/bin/bash
# Botni ishlatadi va yiqilsa 30 soniyadan keyin qayta ko'taradi. Bitta nusxa: lock fayl bilan.
cd "$HOME/ai-agent-tender"
exec 9>data/.run.lock
if ! flock -n 9; then echo "Bot allaqachon ishlayapti."; exit 0; fi
termux-wake-lock 2>/dev/null || true
. .venv/bin/activate
while true; do
  python -m app.main >> data/bot.log 2>&1
  echo "{\"event\": \"restart_in_30s\", \"ts\": \"$(date -Iseconds)\"}" >> data/bot.log
  sleep 30
done
