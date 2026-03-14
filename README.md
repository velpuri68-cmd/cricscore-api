# CricScore API — Self-hosted cricket data server

Scrapes live cricket data from crex.com and serves it as clean JSON.
Deploy free on Railway.app in 5 minutes.

---

## API Endpoints

| Endpoint | Description |
|---|---|
| GET / | API info |
| GET /health | Health check |
| GET /live | All live matches |
| GET /matches | All matches (live + upcoming + finished) |
| GET /scorecard?url=/scoreboard/... | Full scorecard |
| GET /player?url=/player/... | Player profile |
| GET /rankings?type=batting&format=t20 | ICC Rankings |

---

## Deploy to Railway (free hosting)

### Step 1 — Create GitHub account
Go to github.com → Sign up free

### Step 2 — Create a new repository
- Click + → New repository
- Name it: cricscore-api
- Set to Public
- Click Create repository

### Step 3 — Upload these files
- Drag and drop all files from this folder into the GitHub repo
- main.py, requirements.txt, Procfile, railway.json
- Click Commit changes

### Step 4 — Deploy on Railway
- Go to railway.app → Login with GitHub
- Click New Project → Deploy from GitHub repo
- Select cricscore-api
- Railway auto-detects Python and deploys
- Takes about 2 minutes

### Step 5 — Get your server URL
- In Railway dashboard click your project
- Click Settings → Networking → Generate Domain
- You get a URL like: https://cricscore-api-production.up.railway.app

### Step 6 — Test it
Open in browser:
https://your-railway-url.up.railway.app/live

You should see JSON with live match data!

---

## Update Android app to use your API

In CricketApiService.kt change BASE_URL to your Railway URL:

```kotlin
private const val BASE_URL = "https://your-railway-url.up.railway.app/"
```

---

## Local testing (optional)

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

Then open: http://localhost:8000/live
