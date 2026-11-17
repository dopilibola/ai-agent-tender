# AI Agent Tender — 🏛 tenderlar + 💼 frilans

Bitta Telegram bot ichida ikki yordamchi:

- **🏛 Tenderlar** — O'zbekiston davlat xaridlaridagi yangi **IT lotlarni** topadi, TZ'ni o'qib
  **Gemini** bilan baholaydi (QATNASHAMIZ / SHARTLI / YO'Q) va Telegram'ga yuboradi. Manbalar:
  etender.uzex.uz, xarid.uzex.uz («taklif so'rovi»).
- **💼 Frilans** — Freelancer.com, FL.ru, Kwork, Truelancer va (Gmail orqali) Upwork/Fiverr'dan
  profilingizga mos ishlarni topadi, baholaydi, **taklif matni + narx** yozadi va karta yuboradi.

> Agent **hech qachon o'zi** taklif topshirmaydi, E-IMZO ishlatmaydi, mijozga yozmaydi.
> Har bir chiquvchi harakat faqat Telegram'dagi tugmangizdan keyin (✅) va hozircha matnni saytga
> o'zingiz joylaysiz.

---

## 1. Kerakli kalitlar (`.env`)

```bash
cp .env.example .env
```

| O'zgaruvchi | Qayerdan olinadi |
|---|---|
| `GOOGLE_API_KEY` | https://aistudio.google.com/apikey → **Create API key** |
| `TELEGRAM_BOT_TOKEN` | Telegram'da **@BotFather** → `/newbot` |
| `TELEGRAM_OWNER_CHAT_ID` | **@userinfobot** ga `/start` → `Id` (bot faqat shu odamga javob beradi) |
| `IMAP_USER`, `IMAP_APP_PASSWORD` | ixtiyoriy, Upwork/Fiverr uchun: Google hisobi → Security → 2-Step Verification → **App passwords** |
| `FREELANCER_COM_TOKEN` | ixtiyoriy (hozircha kerak emas) |

`.env` hech qachon git'ga tushmaydi (`.gitignore`).

Sozlamalar: `config.yaml` (manbalar, intervallar, kalit so'zlar, limitlar, Gemini modeli),
`profile.yaml` (frilans profilingiz — takliflarda faqat shu ma'lumot ishlatiladi),
`company.yaml` (tenderlar uchun kompaniya profili).

---

## 2. Serverga o'rnatish (Docker) — tavsiya

Talab: Linux server (Ubuntu 22.04+), 2 GB RAM, Docker + Docker Compose.

```bash
# 1) Docker (agar yo'q bo'lsa)
curl -fsSL https://get.docker.com | sh

# 2) Kod
git clone https://github.com/dopilibola/ai-agent-tender.git
cd ai-agent-tender

# 3) Kalitlar
cp .env.example .env && nano .env

# 4) Ma'lumotlar papkasi (konteyner uid 1000 bilan ishlaydi)
mkdir -p data && sudo chown -R 1000:1000 data

# 5) Ishga tushirish
docker compose up -d --build
docker compose logs -f          # loglar (Ctrl+C — chiqish, bot ishlashda davom etadi)
```

Telegram'da botga `/start` yozing — pastda **💼 Frilans** va **🏛 Tenderlar** tugmalari chiqadi.

Foydali buyruqlar:

```bash
docker compose restart                     # config.yaml / .env o'zgargandan keyin
git pull && docker compose up -d --build   # yangilash
docker compose down                        # to'xtatish
```

> ⚠️ Bot bir vaqtning o'zida **faqat bitta joyda** ishlashi kerak. Serverda ishga tushirishdan
> oldin kompyuteringizdagi botni to'xtating, aks holda Telegram `Conflict` xatosini beradi.

Mavjud bazani ko'chirish (ixtiyoriy): kompyuterdagi `data/` papkasini serverdagi `data/` ga nusxalang
(`scp -r data/ user@server:~/ai-agent-tender/`) — shunda takrorlanuvchi xabarlar bo'lmaydi.

---

## 2a. 📱 Telefonni server qilish (Samsung / Android)

Docker'siz, **Termux** orqali — to'liq qo'llanma: [`docs/telefon.md`](docs/telefon.md).
Qisqasi: F-Droid'dan Termux + Termux:Boot, Samsung batareya cheklovini o'chirish, keyin:

```bash
curl -fsSL https://raw.githubusercontent.com/dopilibola/ai-agent-tender/main/scripts/termux-setup.sh | bash
```

Telefonda Truelancer ishlamaydi (Chromium yo'q), qolgan hamma narsa ishlaydi.

---

## 3. Docker'siz ishga tushirish (lokal)

```bash
# Python 3.12 + uv
uv venv --python 3.12 && uv pip install -e ".[dev]"
.venv/bin/playwright install chromium     # Truelancer uchun
.venv/bin/python -m app.main
```

macOS'da loyihani **iCloud bilan sinxronlanadigan Desktop/Documents papkasida saqlamang** —
fayllar "dataless" bo'lib qolib, Python qotib qoladi.

---

## 4. Telegram buyruqlari

| 🏛 Tenderlar | 💼 Frilans |
|---|---|
| `/list` — ochiq IT lotlar | `/jobs` — oxirgi ishlar |
| `/lot 524524` — to'liq tahlil | `/job 12` — ish kartasi |
| `/bid 524524` — qatnashamiz (eslatmalar yoqiladi) | karta tugmalari: ✅ Yuborish · ✏️ Tahrirlash · 🔁 Qayta yoz · ⏭ O'tkazib yuborish |
| `/ignore 524524` — kerak emas | `/cancel` — tahrirni bekor qilish |
| `/analyze 524524` — AI qayta tahlil | `/llmtest` — Gemini tekshiruvi |
| `/status` — agent holati | |

Avtomatik: tenderlar har 30 daqiqada, kunlik hisobot 09:00; frilans 5–60 daqiqada (platformaga qarab),
kunlik xulosa 21:00; sayt ishlamasa — bitta ogohlantirish va tiklanganda xabar.

---

## 5. Manbalar

| Manba | Usul | Izoh |
|---|---|---|
| etender.uzex.uz | ochiq JSON API | `docs/etender_api.md` |
| xarid.uzex.uz | ochiq JSON API (taklif so'rovi) | `docs/xarid_api.md` |
| xt-xarid.uz | — | robots.txt API'ni taqiqlaydi |
| Freelancer.com | rasmiy API | token shart emas (o'qish) |
| FL.ru | RSS | |
| Kwork | ochiq «Биржа» sahifasi | faqat 1-sahifa |
| Truelancer | brauzer (Playwright) | 20–40 s pauza, kunlik limit, CAPTCHA'da to'xtaydi |
| Upwork, Fiverr | Gmail (IMAP, faqat o'qish) | App Password kerak |

Truelancer'ga login ixtiyoriy (o'qish uchun shart emas). Kerak bo'lsa, grafik muhitli kompyuterda:
`python -m app.connectors.truelancer login`, keyin `data/browser_profile/truelancer` ni serverga ko'chiring.

---

## 6. Testlar

```bash
.venv/bin/pytest -q      # tarmoqsiz, Gemini mock qilingan
.venv/bin/ruff check .
```

---

## English (short)

Self-hosted Telegram bot with two agents: an Uzbek public-procurement IT-lot monitor (etender, xarid)
with Gemini analysis, and a freelance job hunter (Freelancer.com, FL.ru, Kwork, Truelancer, Upwork/Fiverr
via Gmail) that scores jobs and drafts honest proposals. Nothing is ever submitted without the owner's
button click. Deploy: `cp .env.example .env`, fill keys, `mkdir -p data && sudo chown 1000:1000 data`,
`docker compose up -d --build`.
