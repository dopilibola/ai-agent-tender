# 📱 Botni Samsung (Android) telefonda server sifatida ishlatish

Telefon **Termux** (Android ichidagi Linux) orqali 24/7 server bo'ladi.

| Ishlaydi ✅ | Ishlamaydi ⛔ |
|---|---|
| 🏛 etender, xarid + Gemini tahlili, eslatmalar, kunlik hisobot | **Truelancer** — Android'da Chromium (Playwright) yo'q, avtomatik o'chadi |
| 💼 Freelancer.com, FL.ru, Kwork, Gmail (Upwork/Fiverr) | `.doc` (eski Word) fayllar o'qilmaydi — PDF/DOCX/RAR o'qiladi |
| Telegram bot, 💼/🏛 tugmalar, takliflar | |

Talab: Android 8+, ~1.5 GB bo'sh joy, doimiy internet (Wi-Fi tavsiya), iloji bo'lsa zaryadlovchiga ulangan.

---

## 1. Ilovalarni o'rnatish (Play Store'dan EMAS)

Play Store'dagi Termux eskirgan. **F-Droid** dan o'rnating:

1. https://f-droid.org → F-Droid ilovasini o'rnating.
2. F-Droid'da: **Termux**, **Termux:Boot**, **Termux:API** — uchalasini o'rnating.
3. **Termux:Boot** ni bir marta oching (avtomatik ishga tushish shundan keyin yoqiladi).

## 2. Samsung batareya sozlamalari (muhim — aks holda Android botni o'ldiradi)

- **Settings → Apps → Termux → Battery → Unrestricted** (Cheklanmagan).
- **Settings → Battery → Background usage limits → Never sleeping apps** → Termux, Termux:Boot, Termux:API ni qo'shing.
- Shu sahifada **"Put unused apps to sleep"** ni o'chiring.
- Termux ochilganda bildirishnomada **"Acquire wakelock"** ni bosing.

## 3. O'rnatish (bitta buyruq)

Termux'ni oching va yozing:

```bash
curl -fsSL https://raw.githubusercontent.com/dopilibola/ai-agent-tender/main/scripts/termux-setup.sh | bash
```

10–30 daqiqa ketadi (pydantic-core telefonda Rust bilan yig'iladi). Oxirida `✅ O'rnatildi` chiqadi.

## 4. Kalitlarni yozish (`.env`)

```bash
nano ~/ai-agent-tender/.env
```

Kompyuterdagi `~/Projects/freelancehunter/.env` dagi qiymatlarni ko'chiring:
`GOOGLE_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_OWNER_CHAT_ID`, `IMAP_USER`, `IMAP_APP_PASSWORD`.
Saqlash: **Ctrl+O → Enter → Ctrl+X** (Termux klaviaturasidagi CTRL tugmasi).

> Qulay yo'l: kompyuterda `.env` matnini Telegram'dagi **Saved Messages** ga yuboring, telefonda nusxalab
> `nano` ga joylang, keyin o'sha xabarni **o'chiring**.

## 5. Eski bazani ko'chirish (tavsiya — takroriy xabarlar bo'lmasin)

Kompyuterdagi `~/Projects/freelancehunter/data/freelancehunter.db` faylini telefonga (masalan Telegram
Saved Messages orqali) yuboring va yuklab oling, so'ng Termux'da:

```bash
termux-setup-storage                     # bir marta: xotiraga ruxsat
cp ~/storage/downloads/freelancehunter.db ~/ai-agent-tender/data/
```

## 6. Ishga tushirish

⚠️ Avval **kompyuterdagi botni to'xtating** — bot bir vaqtda faqat bitta joyda ishlashi kerak
(aks holda Telegram `Conflict` xatosi).

```bash
bash ~/ai-agent-tender/scripts/termux-run.sh &
tail -f ~/ai-agent-tender/data/bot.log      # Ctrl+C — chiqish (bot ishlashda davom etadi)
```

Telegram'da `/start` → 💼 / 🏛 tugmalari. `/status` va 💼 → 🩺 Holat qaysi manbalar ishlayotganini ko'rsatadi.
Telefon qayta yoqilsa, **Termux:Boot** botni o'zi ishga tushiradi.

## Foydali buyruqlar

```bash
pkill -f "app.main"                                   # to'xtatish (termux-run.sh 30 s da qayta ko'taradi)
pkill -f termux-run.sh; pkill -f "app.main"           # butunlay to'xtatish
cd ~/ai-agent-tender && git pull && . .venv/bin/activate && pip install -e . && pkill -f "app.main"   # yangilash
tail -n 50 ~/ai-agent-tender/data/bot.log             # oxirgi loglar
```

## Muammolar

| Belgi | Yechim |
|---|---|
| Bot bir necha soatdan keyin jim | 2-bo'limdagi batareya sozlamalari; wakelock yoqilganmi |
| `pydantic-core` o'rnatilmayapti | `pkg install rust` qayta, keyin `export ANDROID_API_LEVEL=$(getprop ro.build.version.sdk)` va `pip install -e .` |
| `Conflict: terminated by other getUpdates` | Bot boshqa joyda ham ishlayapti (kompyuter) — o'shani to'xtating |
| Truelancer ishlamaydi | Kutilgan holat — telefonda brauzer yo'q |

---

## 🖥 Telefonni VPS'dek ishlatish (SSH + web + tunnel)

Bot o'rnatilgandan keyin (yoki o'rniga — skript botni ham o'rnatadi) Termux'da bir marta:

```bash
curl -fsSL https://raw.githubusercontent.com/dopilibola/ai-agent-tender/main/scripts/termux-vps.sh | bash
# Ubuntu (proot) ham kerak bo'lsa:
curl -fsSL https://raw.githubusercontent.com/dopilibola/ai-agent-tender/main/scripts/termux-vps.sh | INSTALL_UBUNTU=1 bash
```

Natija:
- **SSH**: Mac'dan `ssh -p 8022 <termux_user>@<Tailscale IP>` — faqat kalit bilan, parol o'chiq.
- **Web**: bot bilan birga `http://localhost:8080/tender/lots` (lotlar jadvali, `?all=1` — hammasi, `/tender/lots.json`).
  `.env` da `WEB_TOKEN=...` bo'lsa, sahifa faqat `?key=<WEB_TOKEN>` bilan ochiladi.
- **Tunnel**: `cloudflared` ochiq https link beradi → `grep trycloudflare ~/ai-agent-tender/data/tunnel.log`.
  Link har qayta ishga tushishda o'zgaradi.
- Telefon qayta yoqilsa Termux:Boot hammasini (sshd → bot → tunnel) o'zi ko'taradi.
