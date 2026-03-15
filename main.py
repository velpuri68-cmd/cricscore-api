from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
from bs4 import BeautifulSoup
import re
from datetime import datetime

app = FastAPI(title="CricScore API", version="4.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Use Cricbuzz MOBILE site — simpler HTML, harder to block ──────────
CRICBUZZ_MOBILE = "https://m.cricbuzz.com"

# Mobile browser headers — Cricbuzz mobile site doesn't block these
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 11; Pixel 5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.91 Mobile Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
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
        "version": "4.0.0",
        "source": "Cricbuzz",
        "endpoints": [
            "GET /live       — All live matches",
            "GET /matches    — All matches",
            "GET /scorecard/{match_id}  — Full scorecard",
            "GET /health     — Health check"
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
        matches = await scrape_cricbuzz_matches(live_only=True)
        result = {"status": "success", "count": len(matches), "data": matches}
        set_cache("live", result)
        return result
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/matches")
async def get_all_matches():
    if is_fresh("all"):
        return get_cache("all")
    try:
        matches = await scrape_cricbuzz_matches(live_only=False)
        result = {"status": "success", "count": len(matches), "data": matches}
        set_cache("all", result)
        return result
    except Exception as e:
        raise HTTPException(500, str(e))


async def scrape_cricbuzz_matches(live_only: bool) -> list:
    matches = []

    url = f"{CRICBUZZ_MOBILE}/cricket-match/live-scores" if live_only else f"{CRICBUZZ_MOBILE}/cricket-schedule/upcoming-series/international"

    async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as c:
        resp = await c.get(url)
        soup = BeautifulSoup(resp.text, "html.parser")

    # Cricbuzz mobile wraps each match in a div with class "cb-mtch-lst"
    match_items = soup.find_all("div", class_=re.compile(r"cb-mtch-lst|cb-lv-scrs-well"))

    for item in match_items:
        try:
            # Match link
            link = item.find("a", href=re.compile(r"/cricket-scores/"))
            if not link:
                link = item.find("a", href=re.compile(r"/live-cricket-scores/"))
            if not link:
                continue

            href      = link.get("href", "")
            match_id  = re.search(r"/(\d+)/", href)
            mid       = match_id.group(1) if match_id else href

            # Match title (team names)
            title_el  = item.find(class_=re.compile(r"cb-lv-scrs-well-live|cb-hmscg-bat-txt|cb-lv-scr-mtch-name"))
            title     = title_el.get_text(strip=True) if title_el else link.get_text(strip=True)

            # Series name
            series_el = item.find(class_=re.compile(r"text-gray|cb-lv-scrs-well-top"))
            series    = series_el.get_text(strip=True) if series_el else ""

            # Scores
            score_els = item.find_all(class_=re.compile(r"cb-lv-scrs-well-live|cb-scrs-wrp|cb-hmscg-scr"))
            score_text = " ".join([s.get_text(strip=True) for s in score_els])

            # Status
            status_el = item.find(class_=re.compile(r"cb-lv-scrs-col|cb-text-complete|cb-text-live|cb-text-preview"))
            status    = status_el.get_text(strip=True) if status_el else ""

            # Match state
            is_live   = bool(item.find(class_=re.compile(r"cb-text-live|cb-lv-img-cont")))
            ms        = "live" if is_live else ("result" if any(w in status.lower() for w in ["won", "draw", "tied", "no result"]) else "fixture")

            # Extract teams from title
            teams     = [t.strip() for t in re.split(r"\s+vs\.?\s+|\s+v\.?\s+", title, flags=re.I)][:2]
            if len(teams) < 2:
                teams = [title, ""]

            t1, t2    = teams[0], teams[1] if len(teams) > 1 else ""
            t1s       = abbreviate(t1)
            t2s       = abbreviate(t2)

            # Parse scores from score_text
            scores    = parse_score_text(score_text, t1s, t2s)

            # Match type
            mt = "T20"
            for fmt in ["Test", "ODI", "T20I", "T20"]:
                if fmt.lower() in (series + title).lower():
                    mt = fmt
                    break

            matches.append({
                "id":       mid,
                "name":     title,
                "status":   status,
                "venue":    "",
                "date":     "",
                "matchType": mt,
                "ms":       ms,
                "teams":    [t1, t2],
                "teamInfo": [
                    {"name": t1, "shortname": t1s, "img": get_flag(t1s)},
                    {"name": t2, "shortname": t2s, "img": get_flag(t2s)},
                ],
                "score": scores,
                "scorecard_url": f"/scorecard/{mid}"
            })

        except Exception:
            continue

    return matches


# ═══════════════════════════════════════════════════════════════════════
# SCORECARD
# ═══════════════════════════════════════════════════════════════════════

@app.get("/scorecard/{match_id}")
async def get_scorecard(match_id: str):
    key = f"sc_{match_id}"
    if is_fresh(key):
        return get_cache(key)
    try:
        url = f"{CRICBUZZ_MOBILE}/cricket-scores/{match_id}"
        async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as c:
            resp = await c.get(url)
            soup = BeautifulSoup(resp.text, "html.parser")

        scorecard = parse_scorecard_page(soup)
        result    = {"status": "success", "data": scorecard}
        set_cache(key, result)
        return result
    except Exception as e:
        raise HTTPException(500, str(e))


def parse_scorecard_page(soup: BeautifulSoup) -> dict:
    sc = {"batting": [], "bowling": [], "status": "", "venue": ""}
    try:
        # Status
        status_el = soup.find(class_=re.compile(r"cb-text-complete|cb-text-live"))
        if status_el:
            sc["status"] = status_el.get_text(strip=True)

        # Batting rows
        for row in soup.find_all("div", class_=re.compile(r"cb-col cb-col-100 cb-ltst-wgt-hdr")):
            cols = row.find_all("div", class_=re.compile(r"cb-col"))
            if len(cols) >= 5:
                name_el = cols[0].find("a")
                name    = name_el.get_text(strip=True) if name_el else cols[0].get_text(strip=True)
                pid     = ""
                if name_el:
                    pid_m = re.search(r"/(\d+)/", name_el.get("href", ""))
                    pid   = pid_m.group(1) if pid_m else ""
                try:
                    sc["batting"].append({
                        "batsman":   name,
                        "player_id": pid,
                        "r":         int(cols[1].get_text(strip=True) or 0),
                        "b":         int(cols[2].get_text(strip=True) or 0),
                        "4s":        int(cols[3].get_text(strip=True) or 0),
                        "6s":        int(cols[4].get_text(strip=True) or 0),
                        "sr":        cols[5].get_text(strip=True) if len(cols) > 5 else "0",
                        "out-desc":  ""
                    })
                except (ValueError, IndexError):
                    continue

        # Bowling rows
        for row in soup.find_all("div", class_=re.compile(r"cb-col cb-col-100 cb-ltst-wgt-hdr")):
            cols = row.find_all("div", class_=re.compile(r"cb-col"))
            if len(cols) >= 5:
                name_el = cols[0].find("a")
                name    = name_el.get_text(strip=True) if name_el else cols[0].get_text(strip=True)
                pid     = ""
                if name_el:
                    pid_m = re.search(r"/(\d+)/", name_el.get("href", ""))
                    pid   = pid_m.group(1) if pid_m else ""
                try:
                    sc["bowling"].append({
                        "bowler":    name,
                        "player_id": pid,
                        "o":         cols[1].get_text(strip=True),
                        "m":         int(cols[2].get_text(strip=True) or 0),
                        "r":         int(cols[3].get_text(strip=True) or 0),
                        "w":         int(cols[4].get_text(strip=True) or 0),
                        "eco":       cols[5].get_text(strip=True) if len(cols) > 5 else "0"
                    })
                except (ValueError, IndexError):
                    continue

    except Exception as e:
        sc["error"] = str(e)
    return sc


# ═══════════════════════════════════════════════════════════════════════
# NEWS
# ═══════════════════════════════════════════════════════════════════════

@app.get("/news")
async def get_news():
    if is_fresh("news"):
        return get_cache("news")
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as c:
            resp = await c.get(f"{CRICBUZZ_MOBILE}/cricket-news")
            soup = BeautifulSoup(resp.text, "html.parser")

        news = []
        for item in soup.find_all("div", class_=re.compile(r"cb-nws-lst-itm|cb-lst-itm")):
            try:
                a   = item.find("a")
                if not a: continue
                title = a.get_text(strip=True)
                intro_el = item.find(class_=re.compile(r"cb-nws-intr"))
                intro = intro_el.get_text(strip=True) if intro_el else ""
                date_el = item.find(class_=re.compile(r"cb-nws-time|text-gray"))
                date  = date_el.get_text(strip=True) if date_el else ""
                if title:
                    news.append({
                        "id": a.get("href", ""),
                        "title": title,
                        "intro": intro,
                        "date": date,
                        "imageUrl": "",
                        "source": "Cricbuzz"
                    })
            except Exception:
                continue

        result = {"status": "success", "count": len(news), "data": news}
        set_cache("news", result)
        return result
    except Exception as e:
        raise HTTPException(500, str(e))


# ═══════════════════════════════════════════════════════════════════════
# RANKINGS (fallback data — reliable)
# ═══════════════════════════════════════════════════════════════════════

@app.get("/rankings")
async def get_rankings(type: str = "batting", format: str = "t20"):
    return {"status": "success", "data": fallback_rankings(type)}


# ═══════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════

def parse_score_text(text: str, t1: str, t2: str) -> list:
    scores = []
    # Match patterns like "274/10 (47.3 ov)" or "114-10 (23.3)"
    patterns = re.findall(r'(\d{1,3})[/\-](\d{1,2})\s*\(?(\d{1,2}\.?\d?)', text)
    teams = [t1, t2]
    for i, (runs, wkts, overs) in enumerate(patterns[:2]):
        scores.append({
            "r": int(runs),
            "w": int(wkts),
            "o": float(overs) if overs else 0.0,
            "inning": f"{teams[i] if i < len(teams) else 'Team'} Inning 1"
        })
    return scores


def abbreviate(name: str) -> str:
    """Convert full team name to 2-3 letter abbreviation"""
    known = {
        "india": "IND", "england": "ENG", "australia": "AUS",
        "pakistan": "PAK", "south africa": "SA", "new zealand": "NZ",
        "west indies": "WI", "sri lanka": "SL", "bangladesh": "BAN",
        "afghanistan": "AFG", "zimbabwe": "ZIM", "ireland": "IRE",
        "scotland": "SCO", "namibia": "NAM", "netherlands": "NED",
        "usa": "USA", "united states": "USA", "kenya": "KEN",
    }
    lower = name.lower().strip()
    for k, v in known.items():
        if k in lower:
            return v
    # Fallback: first 3 chars uppercase
    words = name.strip().split()
    if len(words) >= 2:
        return "".join(w[0] for w in words[:3]).upper()
    return name[:3].upper()


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


def fallback_rankings(type: str) -> list:
    if type == "batting":
        return [
            {"rank":"1","name":"Babar Azam","country":"PAK","rating":"890","points":"890"},
            {"rank":"2","name":"Virat Kohli","country":"IND","rating":"867","points":"867"},
            {"rank":"3","name":"Joe Root","country":"ENG","rating":"855","points":"855"},
            {"rank":"4","name":"Steve Smith","country":"AUS","rating":"840","points":"840"},
            {"rank":"5","name":"Kane Williamson","country":"NZ","rating":"835","points":"835"},
        ]
    elif type == "bowling":
        return [
            {"rank":"1","name":"Jasprit Bumrah","country":"IND","rating":"873","points":"873"},
            {"rank":"2","name":"Pat Cummins","country":"AUS","rating":"860","points":"860"},
            {"rank":"3","name":"Shaheen Afridi","country":"PAK","rating":"845","points":"845"},
            {"rank":"4","name":"Trent Boult","country":"NZ","rating":"830","points":"830"},
            {"rank":"5","name":"Kagiso Rabada","country":"SA","rating":"820","points":"820"},
        ]
    return [
        {"rank":"1","name":"India","country":"IND","rating":"125","points":"3125"},
        {"rank":"2","name":"Australia","country":"AUS","rating":"118","points":"2950"},
        {"rank":"3","name":"England","country":"ENG","rating":"110","points":"2750"},
        {"rank":"4","name":"Pakistan","country":"PAK","rating":"105","points":"2625"},
        {"rank":"5","name":"New Zealand","country":"NZ","rating":"100","points":"2500"},
    ]
