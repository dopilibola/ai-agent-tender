#!/data/data/com.termux/files/usr/bin/bash
# Telefondagi web (:8080) ni o'z domeningizga ulash: telefon → VPS'ga teskari SSH tunnel.
# VPS'da nginx domenni 127.0.0.1:<REMOTE_PORT> ga proxy qiladi; kod va data faqat telefonda qoladi.
#
# Ishlatish (Termux'da):
#   bash ~/ai-agent-tender/scripts/termux-domain-tunnel.sh user@server_ip [REMOTE_PORT=3050]
# Birinchi marta ochiq kalitni chiqaradi — uni server'ning ~/.ssh/authorized_keys ga qo'shish kerak.
set -euo pipefail

CFG="$HOME/.config/domain-tunnel"
KEY="$HOME/.ssh/domain_tunnel"
mkdir -p "$CFG" "$HOME/.ssh" "$HOME/.termux/boot"

if [ $# -ge 1 ]; then
  echo "$1" > "$CFG/target"
  echo "${2:-3050}" > "$CFG/port"
fi
[ -f "$CFG/target" ] || { echo "Ishlatish: $0 user@server_ip [port]"; exit 1; }
TARGET="$(cat "$CFG/target")"
PORT="$(cat "$CFG/port")"

command -v ssh >/dev/null || pkg install -y openssh
[ -f "$KEY" ] || ssh-keygen -t ed25519 -N "" -C "phone-domain-tunnel" -f "$KEY" >/dev/null

# Doimiy tunnel: uzilsa 10 soniyada qayta ulanadi.
cat > "$CFG/run.sh" <<RUN
#!/data/data/com.termux/files/usr/bin/bash
exec 9>"$CFG/.lock"; flock -n 9 || exit 0
while true; do
  ssh -N -i "$KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \\
      -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes \\
      -R 127.0.0.1:$PORT:127.0.0.1:8080 "$TARGET" >> "$CFG/tunnel.log" 2>&1
  sleep 10
done
RUN
chmod +x "$CFG/run.sh"
cat > "$HOME/.termux/boot/30-domain-tunnel.sh" <<BOOT
#!/data/data/com.termux/files/usr/bin/bash
sleep 30
nohup bash "$CFG/run.sh" >/dev/null 2>&1 &
BOOT
chmod +x "$HOME/.termux/boot/30-domain-tunnel.sh"

echo
echo "🔑 Telefonning ochiq kaliti (server'ga qo'shish uchun — maxfiy emas):"
echo
echo "restrict,port-forwarding,permitlisten=\"127.0.0.1:$PORT\",command=\"/usr/sbin/nologin\" $(cat "$KEY.pub")"
echo

# Sinov: kalit qabul qilinsa, ssh 8 soniya davomida ulanib turadi.
ssh -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 \
    -N -o ExitOnForwardFailure=yes -R "127.0.0.1:$PORT:127.0.0.1:8080" "$TARGET" </dev/null 2>/dev/null &
TEST_PID=$!
sleep 8
if kill -0 "$TEST_PID" 2>/dev/null; then
  kill "$TEST_PID" 2>/dev/null || true
  sleep 1
  nohup bash "$CFG/run.sh" >/dev/null 2>&1 &
  echo "✅ Tunnel ishlayapti: domen → telefon :8080. Telefon qayta yoqilsa o'zi ulanadi."
  exit 0
fi
echo "⏳ Server hali kalitni qabul qilmadi. Yuqoridagi qatorni server'ga qo'shing, keyin shu buyruqni qayta ishga tushiring:"
echo "   bash ~/ai-agent-tender/scripts/termux-domain-tunnel.sh"
