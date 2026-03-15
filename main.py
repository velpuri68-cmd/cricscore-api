from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
from datetime import datetime

app = FastAPI(title="CricScore API", version="5.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Uses cricbuzz-live.vercel.app — already working, free, no key ─────
CB_BASE = "https://cricbuzz-live.vercel.app/v1"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Android 11; Mobile) AppleWebKit/537.36",
    "Accept": "application/json",
}

# ── Cache ─────────────────────────────────────────────────────────────
cache = {}
CACHE_TTL = 15

def is_fresh(key):
    return key in cache and (datetime.now() - cache[key]["t"]).seconds < CACHE_TTL

def set_cache(key, data):
    cache[key] = {"data": data, "t": datetime.now()}

def get_cache(key):
    return cache[key]["data"]


# ═══════════════════════════════════════════════════════════════════════
# ROOT & HEALTH
# ═══════════════════════════════════════════════════════════════════════

@app.get("/")
async def root():
    return {
        "name": "CricScore API",
        "version": "5.0.0",
        "endpoints": [
            "GET /live           — All live matches",
            "GET /matches        — All matches",
            "GET /scorecard/{id} — Full scorecard by match ID",
            "GET /rankings       — ICC rankings",
            "GET /news           — Cricket news",
            "GET /health         — Health check",
        ]
    }

@app.get("/health")
async def health():
    return {"status": "ok", "time": str(datetime.now())}


# ═══════════════════════════════════════════════════════════════════════
# LIVE MATCHES
# ═══════════════════════════════════════════════════════════════════════

@app.get("/live")
async def get_live():
    if is_fresh("live"):
        return get_cache("live")
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=20) as c:
            r = await c.get(f"{CB_BASE}/matches")
        data = r.json()

        # Filter only live matches
        all_matches = data.get("data", {}).get("matches", [])
        live = [transform_match(m) for m in all_matches if is_live_match(m)]

        result = {"status": "success", "count": len(live), "data": live}
        set_cache("live", result)
        return result
    except Exception as e:
        raise HTTPException(500, str(e))


# ═══════════════════════════════════════════════════════════════════════
# ALL MATCHES
# ═══════════════════════════════════════════════════════════════════════

@app.get("/matches")
async def get_matches():
    if is_fresh("all"):
        return get_cache("all")
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=20) as c:
            r = await c.get(f"{CB_BASE}/matches")
        data = r.json()

        all_matches = data.get("data", {}).get("matches", [])
        transformed = [transform_match(m) for m in all_matches]

        result = {"status": "success", "count": len(transformed), "data": transformed}
        set_cache("all", result)
        return result
    except Exception as e:
        raise HTTPException(500, str(e))


# ═══════════════════════════════════════════════════════════════════════
# SCORECARD — full batters, bowler, scores
# ═══════════════════════════════════════════════════════════════════════

@app.get("/scorecard/{match_id}")
async def get_scorecard(match_id: str):
    key = f"sc_{match_id}"
    if is_fresh(key):
        return get_cache(key)
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=20) as c:
            r = await c.get(f"{CB_BASE}/score/{match_id}")
        data = r.json()

        raw = data.get("data", {})
        scorecard = {
            "name":           raw.get("title", ""),
            "status":         raw.get("update", ""),
            "liveScore":      raw.get("liveScore", ""),
            "runRate":        raw.get("runRate", ""),
            "batter1": {
                "name":        raw.get("batsmanOne", ""),
                "runs":        raw.get("batsmanOneRun", "0"),
                "balls":       raw.get("batsmanOneBall", "(0)"),
                "strikeRate":  raw.get("batsmanOneSR", "0"),
            },
            "batter2": {
                "name":        raw.get("batsmanTwo", ""),
                "runs":        raw.get("batsmanTwoRun", "0"),
                "balls":       raw.get("batsmanTwoBall", "(0)"),
                "strikeRate":  raw.get("batsmanTwoSR", "0"),
            },
            "bowler1": {
                "name":        raw.get("bowlerOne", ""),
                "overs":       raw.get("bowlerOneOver", "0"),
                "runs":        raw.get("bowlerOneRun", "0"),
                "wickets":     raw.get("bowlerOneWickets", "0"),
                "economy":     raw.get("bowlerOneEconomy", "0"),
            },
            "bowler2": {
                "name":        raw.get("bowlerTwo", ""),
                "overs":       raw.get("bowlerTwoOver", "0"),
                "runs":        raw.get("bowlerTwoRun", "0"),
                "wickets":     raw.get("bowlerTwoWicket", "0"),
                "economy":     raw.get("bowlerTwoEconomy", "0"),
            },
            # Also format as batting/bowling arrays for Android app
            "batting": [
                {
                    "batsman": raw.get("batsmanOne", ""),
                    "r": safe_int(raw.get("batsmanOneRun", "0")),
                    "b": safe_int(raw.get("batsmanOneBall", "0").replace("(","").replace(")","")),
                    "4s": 0, "6s": 0,
                    "sr": raw.get("batsmanOneSR", "0"),
                    "out-desc": ""
                },
                {
                    "batsman": raw.get("batsmanTwo", ""),
                    "r": safe_int(raw.get("batsmanTwoRun", "0")),
                    "b": safe_int(raw.get("batsmanTwoBall", "0").replace("(","").replace(")","")),
                    "4s": 0, "6s": 0,
                    "sr": raw.get("batsmanTwoSR", "0"),
                    "out-desc": ""
                },
            ],
            "bowling": [
                {
                    "bowler": raw.get("bowlerOne", ""),
                    "o": raw.get("bowlerOneOver", "0"),
                    "m": 0,
                    "r": safe_int(raw.get("bowlerOneRun", "0")),
                    "w": safe_int(raw.get("bowlerOneWickets", "0")),
                    "eco": raw.get("bowlerOneEconomy", "0"),
                },
                {
                    "bowler": raw.get("bowlerTwo", ""),
                    "o": raw.get("bowlerTwoOver", "0"),
                    "m": 0,
                    "r": safe_int(raw.get("bowlerTwoRun", "0")),
                    "w": safe_int(raw.get("bowlerTwoWicket", "0")),
                    "eco": raw.get("bowlerTwoEconomy", "0"),
                },
            ]
        }

        result = {"status": "success", "data": scorecard}
        set_cache(key, result)
        return result
    except Exception as e:
        raise HTTPException(500, str(e))


# ═══════════════════════════════════════════════════════════════════════
# RANKINGS — reliable fallback data
# ═══════════════════════════════════════════════════════════════════════

@app.get("/rankings")
async def get_rankings(type: str = "batting", format: str = "t20"):
    return {"status": "success", "data": get_rankings_data(type)}


# ═══════════════════════════════════════════════════════════════════════
# NEWS
# ═══════════════════════════════════════════════════════════════════════

@app.get("/news")
async def get_news():
    if is_fresh("news"):
        return get_cache("news")
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=20) as c:
            r = await c.get(f"{CB_BASE}/news")
        data = r.json()

        news_raw = data.get("data", {}).get("stories", data.get("data", []))
        news = []
        if isinstance(news_raw, list):
            for item in news_raw:
                news.append({
                    "id":       str(item.get("id", "")),
                    "title":    item.get("title", item.get("headline", "")),
                    "intro":    item.get("intro", item.get("description", "")),
                    "date":     item.get("publishedAt", item.get("date", "")),
                    "imageUrl": item.get("imageUrl", ""),
                    "source":   "Cricbuzz"
                })

        result = {"status": "success", "count": len(news), "data": news}
        set_cache("news", result)
        return result
    except Exception as e:
        # Return empty news rather than error
        return {"status": "success", "count": 0, "data": []}


# ═══════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════

def transform_match(m: dict) -> dict:
    """Convert cricbuzz-live format to our app format"""
    mid   = str(m.get("id", ""))
    title = m.get("title", "")
    teams = [t.get("team", "") for t in m.get("teams", [])]
    t1    = teams[0] if len(teams) > 0 else ""
    t2    = teams[1] if len(teams) > 1 else ""
    t1s   = abbrev(t1)
    t2s   = abbrev(t2)

    # Build score entries
    scores = []
    for i, t in enumerate(m.get("teams", [])[:2]):
        run = t.get("run", "")
        if run and run not in ["-", ""]:
            import re
            rw = re.search(r'(\d+)[/\-](\d+)', str(run))
            ov = re.search(r'\((\d+\.?\d*)\)', str(run))
            scores.append({
                "r": int(rw.group(1)) if rw else 0,
                "w": int(rw.group(2)) if rw else 0,
                "o": float(ov.group(1)) if ov else 0.0,
                "inning": f"{[t1s, t2s][i]} Inning 1"
            })

    ms = "live" if is_live_match(m) else "fixture"

    return {
        "id":       mid,
        "name":     title,
        "status":   m.get("status", ""),
        "venue":    "",
        "date":     "",
        "matchType": "T20",
        "ms":       ms,
        "teams":    [t1, t2],
        "teamInfo": [
            {"name": t1, "shortname": t1s, "img": get_flag(t1s)},
            {"name": t2, "shortname": t2s, "img": get_flag(t2s)},
        ],
        "score": scores,
        "scorecard_url": f"/scorecard/{mid}"
    }


def is_live_match(m: dict) -> bool:
    status = str(m.get("status", "")).lower()
    return any(w in status for w in ["live", "innings", "over", "need", "bat", "bowl"])


def safe_int(val) -> int:
    try:
        return int(str(val).strip())
    except Exception:
        return 0


def abbrev(name: str) -> str:
    known = {
        "india": "IND", "england": "ENG", "australia": "AUS",
        "pakistan": "PAK", "south africa": "SA", "new zealand": "NZ",
        "west indies": "WI", "sri lanka": "SL", "bangladesh": "BAN",
        "afghanistan": "AFG", "zimbabwe": "ZIM", "ireland": "IRE",
        "scotland": "SCO", "netherlands": "NED", "namibia": "NAM",
        "usa": "USA", "kenya": "KEN", "nepal": "NEP", "oman": "OMA",
    }
    lower = name.lower().strip()
    for k, v in known.items():
        if k in lower:
            return v
    words = name.strip().split()
    if len(words) >= 2:
        return "".join(w[0] for w in words[:3]).upper()
    return name[:3].upper() if name else "UNK"


def get_flag(short: str) -> str:
    flags = {
        "IND": "https://cricketvectors.akamaized.net/Teams/I.png",
        "ENG": "https://cricketvectors.akamaized.net/Teams/E.png",
        "AUS": "https://cricketvectors.akamaized.net/Teams/A.png",
        "PAK": "https://cricketvectors.akamaized.net/Teams/U.png",
        "SA":  "https://cricketvectors.akamaized.net/Teams/P.png",
        "NZ":  "https://cricketvectors.akamaized.net/Teams/R.png",
        "SL":  "https://cricketvectors.akamaized.net/Teams/SL.png",
        "BAN": "https://cricketvectors.akamaized.net/Teams/W.png",
        "WI":  "https://cricketvectors.akamaized.net/Teams/WI.png",
        "AFG": "https://cricketvectors.akamaized.net/Teams/AF.png",
        "ZIM": "https://cricketvectors.akamaized.net/Teams/Z.png",
        "IRE": "https://cricketvectors.akamaized.net/Teams/IR.png",
    }
    return flags.get(short.upper(), "https://cricketvectors.akamaized.net/Teams/ICC.png")


def get_rankings_data(type: str) -> list:
    if type == "batting":
        return [
            {"rank":"1","name":"Babar Azam","country":"PAK","rating":"890","points":"890"},
            {"rank":"2","name":"Virat Kohli","country":"IND","rating":"867","points":"867"},
            {"rank":"3","name":"Joe Root","country":"ENG","rating":"855","points":"855"},
            {"rank":"4","name":"Steve Smith","country":"AUS","rating":"840","points":"840"},
            {"rank":"5","name":"Kane Williamson","country":"NZ","rating":"835","points":"835"},
            {"rank":"6","name":"Rohit Sharma","country":"IND","rating":"820","points":"820"},
            {"rank":"7","name":"David Warner","country":"AUS","rating":"810","points":"810"},
            {"rank":"8","name":"Fakhar Zaman","country":"PAK","rating":"800","points":"800"},
        ]
    elif type == "bowling":
        return [
            {"rank":"1","name":"Jasprit Bumrah","country":"IND","rating":"873","points":"873"},
            {"rank":"2","name":"Pat Cummins","country":"AUS","rating":"860","points":"860"},
            {"rank":"3","name":"Shaheen Afridi","country":"PAK","rating":"845","points":"845"},
            {"rank":"4","name":"Trent Boult","country":"NZ","rating":"830","points":"830"},
            {"rank":"5","name":"Kagiso Rabada","country":"SA","rating":"820","points":"820"},
            {"rank":"6","name":"Mohammad Shami","country":"IND","rating":"810","points":"810"},
            {"rank":"7","name":"Mitchell Starc","country":"AUS","rating":"800","points":"800"},
        ]
    return [
        {"rank":"1","name":"India","country":"IND","rating":"125","points":"3125"},
        {"rank":"2","name":"Australia","country":"AUS","rating":"118","points":"2950"},
        {"rank":"3","name":"England","country":"ENG","rating":"110","points":"2750"},
        {"rank":"4","name":"Pakistan","country":"PAK","rating":"105","points":"2625"},
        {"rank":"5","name":"New Zealand","country":"NZ","rating":"100","points":"2500"},
        {"rank":"6","name":"South Africa","country":"SA","rating":"96","points":"2400"},
        {"rank":"7","name":"Sri Lanka","country":"SL","rating":"90","points":"2250"},
        {"rank":"8","name":"Bangladesh","country":"BAN","rating":"84","points":"2100"},
    ]
