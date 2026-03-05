# 🎌 KenshinAnimeBot v2.0

> **@KENSHIN_ANIME** — Dual-site anime search bot for Telegram

## ✨ Features

| Feature | Detail |
|---------|--------|
| 🔍 Dual Search | `desidubanime.me` + `animehindidubbed.in` |
| 📺 Episode List | Saare episodes paginated buttons ke saath |
| 📦 All Episodes | Ek click mein saare episodes ke links |
| 🎞️ Quality Sort | 4K / 1080p / 720p / 480p / 360p detect |
| ⚡ Fast | Async + connection pooling (50 concurrent) |
| 🔄 Site Switch | Inline se site change karo |

---

## 🚀 Deploy — Railway (Free Hosting)

### Step 1 — GitHub pe code daalo

```bash
git init
git add .
git commit -m "KenshinAnimeBot v2"
git remote add origin https://github.com/YOUR_USERNAME/kenshin-anime-bot.git
git push -u origin main
```

### Step 2 — Bot Token lo
1. Telegram → [@BotFather](https://t.me/BotFather)
2. `/newbot` → naam: `KenshinAnimeBot` → username: `kenshin_anime_search_bot`
3. Token copy karo

### Step 3 — Railway Deploy
1. [railway.app](https://railway.app) → Login with GitHub
2. **New Project** → **Deploy from GitHub repo** → apna repo select karo
3. **Variables** tab → `BOT_TOKEN` = `your_token_here` add karo
4. **Deploy** click karo → Done! ✅

---

## 💻 Local Run

```bash
# 1. Clone / unzip
cd anime_bot_v2

# 2. Install deps
pip install -r requirements.txt

# 3. Token set karo
cp .env.example .env
# .env file mein BOT_TOKEN paste karo

# 4. Run!
python bot.py
```

---

## 🤖 Bot Commands

| Command | Kaam |
|---------|------|
| `/start` | Welcome |
| `/help` | Help + quality guide |
| `/search Naruto` | Anime search |
| `/site` | Source site change karo |
| Seedha naam likho | Bhi search ho jaata hai |

### Buttons kaise kaam karte hain:

```
Search results → Anime select → Episode list
                                    │
                    ┌───────────────┼───────────────────┐
                    │               │                   │
               ▶ Ep 1          ▶ Ep 2          📦 All Episodes
               (single)        (single)        (batch fetch)
                    │                               │
              Download links               Saare episodes ke
              quality-wise               links ek saath send
```

---

## 📁 File Structure

```
anime_bot_v2/
├── bot.py            # Main bot logic
├── scraper.py        # Website scraper (both sites)
├── requirements.txt  # Python dependencies
├── .env.example      # Token config template
├── railway.json      # Railway deployment config
├── Procfile          # Process definition
└── README.md         # Ye file
```

---

## ⚙️ Performance Settings

- `TCPConnector(limit=50)` — 50 concurrent connections
- `Semaphore(8)` — All Episodes mein max 8 parallel requests
- Progress bar — Real-time fetch progress
- Auto-chunk — Long messages auto-split
- Connection reuse — Session-level pooling

---

## 🔧 Troubleshooting

| Problem | Solution |
|---------|----------|
| Search kuch nahi deta | `/site` se dusri site try karo |
| All Episodes slow hai | Normal hai — 50+ episode pages fetch ho rahe |
| Bot respond nahi karta | Railway logs check karo, BOT_TOKEN verify karo |
| Quality Unknown aata | Site pe quality label nahi hota — link open karo |

---

📢 **Channel:** [@KENSHIN_ANIME](https://t.me/KENSHIN_ANIME)
