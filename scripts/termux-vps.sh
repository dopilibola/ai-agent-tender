#!/data/data/com.termux/files/usr/bin/bash
# Telefonni VPS'ga aylantirish: SSH (kalit bilan), avtomatik ishga tushish, tunnel, ixtiyoriy Ubuntu.
# Ishlatish (Termux'da, bir marta):
#   curl -fsSL https://raw.githubusercontent.com/dopilibola/ai-agent-tender/main/scripts/termux-vps.sh | bash
# Ubuntu (proot) ham kerak bo'lsa:
#   curl -fsSL .../termux-vps.sh | INSTALL_UBUNTU=1 bash
set -euo pipefail

REPO_RAW="https://raw.githubusercontent.com/dopilibola/ai-agent-tender/main"
APP="$HOME/ai-agent-tender"
# Mac'dan parolsiz kirish uchun ochiq kalit (public key — maxfiy emas).
MAC_KEY="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGOYajMpwHo8TinFBxRKcF4E6LMTYfadLDpvfuKHBhIy komronbek-macbook-air"

echo "▶ 1/5 Paketlar: openssh, cloudflared, termux-api…"
pkg update -y
pkg install -y openssh cloudflared termux-api

echo "▶ 2/5 SSH: Mac kaliti, parol bilan kirish o'chiriladi…"
mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
touch "$HOME/.ssh/authorized_keys" && chmod 600 "$HOME/.ssh/authorized_keys"
grep -qF "$MAC_KEY" "$HOME/.ssh/authorized_keys" || echo "$MAC_KEY" >> "$HOME/.ssh/authorized_keys"
SSHD_CFG="$PREFIX/etc/ssh/sshd_config"
sed -i -E '/^#?(PasswordAuthentication|KbdInteractiveAuthentication|PubkeyAuthentication) /d' "$SSHD_CFG"
printf '%s\n' "PasswordAuthentication no" "KbdInteractiveAuthentication no" "PubkeyAuthentication yes" >> "$SSHD_CFG"
pgrep -x sshd >/dev/null && pkill -x sshd || true
sshd

echo "▶ 3/5 Bot kodi…"
if [ -d "$APP/.git" ]; then
  git -C "$APP" pull --ff-only
  # shellcheck disable=SC1091
  ( cd "$APP" && . .venv/bin/activate && pip install -q -e . )
else
  curl -fsSL "$REPO_RAW/scripts/termux-setup.sh" | bash
fi

echo "▶ 4/5 Telefon yoqilganda avtomatik: sshd → bot (+ web :8080) → tunnel…"
mkdir -p "$HOME/.termux/boot" "$APP/data"
cat > "$HOME/.termux/boot/00-sshd.sh" <<'BOOT'
#!/data/data/com.termux/files/usr/bin/bash
termux-wake-lock
sshd
BOOT
cat > "$HOME/.termux/boot/20-tunnel.sh" <<'BOOT'
#!/data/data/com.termux/files/usr/bin/bash
# Ochiq https link → http://localhost:8080 (/tender/lots). Link data/tunnel.log ichida.
sleep 40
nohup cloudflared tunnel --no-autoupdate --url http://localhost:8080 > "$HOME/ai-agent-tender/data/tunnel.log" 2>&1 &
BOOT
chmod +x "$HOME/.termux/boot/"*.sh

if [ "${INSTALL_UBUNTU:-0}" = "1" ]; then
  echo "▶ Ubuntu (proot-distro)…"
  pkg install -y proot-distro
  proot-distro list 2>/dev/null | grep -q "ubuntu.*installed" || proot-distro install ubuntu
  echo "   Kirish: proot-distro login ubuntu"
fi

echo "▶ 5/5 Hozir ishga tushirish…"
termux-wake-lock || true
pgrep -f "termux-run.sh" >/dev/null || nohup bash "$APP/scripts/termux-run.sh" >/dev/null 2>&1 &
pgrep -f "cloudflared tunnel" >/dev/null || \
  nohup cloudflared tunnel --no-autoupdate --url http://localhost:8080 > "$APP/data/tunnel.log" 2>&1 &
sleep 15
URL="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$APP/data/tunnel.log" | head -1 || true)"

cat <<MSG

✅ Telefon server rejimida.
  SSH (Mac'dan, Tailscale orqali):  ssh -p 8022 $(whoami)@<telefonning Tailscale IP>
  Web:   ${URL:-"(tunnel hali tayyor emas: grep trycloudflare ~/ai-agent-tender/data/tunnel.log)"}/tender/lots
  Loglar: tail -f ~/ai-agent-tender/data/bot.log

Eslatma:
  • .env hali to'ldirilmagan bo'lsa: nano ~/ai-agent-tender/.env  (WEB_TOKEN ham qo'ying)
  • 8080 portni boshqa server (masalan "Salom") band qilgan bo'lsa, uni to'xtating — aks holda web ochilmaydi.
  • Batareya: Settings → Apps → Termux → Battery → Unrestricted.
MSG
