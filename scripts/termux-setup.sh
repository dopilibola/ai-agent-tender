#!/data/data/com.termux/files/usr/bin/bash
# AI Agent Tender — Android (Termux) o'rnatish skripti.
# Ishlatish:  curl -fsSL https://raw.githubusercontent.com/dopilibola/ai-agent-tender/main/scripts/termux-setup.sh | bash
set -euo pipefail

echo "▶ 1/5 Termux paketlari (10–20 daqiqa olishi mumkin)…"
pkg update -y && pkg upgrade -y
pkg install -y python git rust clang make binutils libarchive openssl libffi \
               python-cryptography termux-api

echo "▶ 2/5 Kod…"
cd "$HOME"
if [ -d ai-agent-tender/.git ]; then
  git -C ai-agent-tender pull --ff-only
else
  git clone https://github.com/dopilibola/ai-agent-tender.git
fi
cd ai-agent-tender

echo "▶ 3/5 Python kutubxonalari (pydantic-core Rust bilan yig'iladi — sabr qiling)…"
export ANDROID_API_LEVEL="$(getprop ro.build.version.sdk)"   # maturin (pydantic-core) uchun kerak
python -m venv --system-site-packages .venv                 # python-cryptography paketini qayta ishlatadi
. .venv/bin/activate
pip install --upgrade pip wheel
pip install -e .                                            # Playwright'siz: Truelancer telefonda o'chadi

echo "▶ 4/5 Papkalar va avtomatik ishga tushish…"
mkdir -p data "$HOME/.termux/boot"
cat > "$HOME/.termux/boot/start-ai-agent.sh" <<'BOOT'
#!/data/data/com.termux/files/usr/bin/bash
termux-wake-lock
nohup bash "$HOME/ai-agent-tender/scripts/termux-run.sh" >/dev/null 2>&1 &
BOOT
chmod +x "$HOME/.termux/boot/start-ai-agent.sh" scripts/termux-run.sh
[ -f .env ] || cp .env.example .env

echo "▶ 5/5 Tekshiruv…"
python -c "import app.main, aiogram, google.genai, pydantic; print('✅ kutubxonalar joyida')"

cat <<'MSG'

✅ O'rnatildi. Endi:
  1) nano ~/ai-agent-tender/.env      # GOOGLE_API_KEY, TELEGRAM_*, IMAP_* ni yozing (Ctrl+O, Enter, Ctrl+X)
  2) bash ~/ai-agent-tender/scripts/termux-run.sh &     # ishga tushirish
  3) tail -f ~/ai-agent-tender/data/bot.log             # loglar (Ctrl+C — chiqish)
Telefon qayta yoqilganda Termux:Boot botni o'zi ishga tushiradi.
MSG
