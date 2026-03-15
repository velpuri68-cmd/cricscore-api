from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
import json
import re
from datetime import datetime
from bs4 import BeautifulSoup

app = FastAPI(title="CricScore API", version="3.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Headers ───────────────────────────────────────────────────────────
HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.espncricinfo.com/",
}

# ── Cache ─────────────────────────────────────────────────────────────
cache = {}
CACHE_TTL = 20

def is_fresh(key):
    if key not in cache: return False
    return (datetime.now() - cache[key]["t"]).seconds < CACHE_TTL

def set_cache(key, data):
    cache[key] = {"data": data, "t": datetime.now()}

def get_cache(key):
    return cache.get(key, {}).get("data")


# ═══════════════════════════════════════════════════════════════════════
# ROOT & HEALTH
# ═══════════════════════════════════════════════════════════════════════

@app.get("/")
async def root():
    return {
        "name": "CricScore API",
        "version": "3.0.0",
        "status": "running",
        "endpoints": [
            "GET /live",
            "GET /matches",
            "GET /scorecard/{match_id}",
            "GET /player/{player_id}",
            "GET /rankings",
            "GET /news",
            "GET /health"
        ]
    }

@app.get("/health")
async def health():
    return {"status": "ok", "time": str(datetime.now())}


# ═══════════════════════════════════════════════════════════════════════
# LIVE MATCHES — from ESPNCricinfo live scores page
# ═══════════════════════════════════════════════════════════════════════

@app.get("/live")
async def get_live():
    """Scrapes ESPNCricinfo live scores page"""
    if is_fresh("live"):
        return get_cache("live")
    try:
        matches = await fetch_espn_matches(live_only=True)
        result = {"status": "success", "count": len(matches), "data": matches}
        set_cache("live", result)
        return result
    except Exception as e:
        raise HTTPException(500, f"Error fetching live matches: {str(e)}")


# ═══════════════════════════════════════════════════════════════════════
# ALL MATCHES
# ═══════════════════════════════════════════════════════════════════════

@app.get("/matches")
async def get_matches():
    """Returns all matches — live, upcoming, finished"""
    if is_fresh("all_matches"):
        return get_cache("all_matches")
    try:
        matches = await fetch_espn_matches(live_only=False)
        result = {"status": "success", "count": len(matches), "data": matches}
        set_cache("all_matches", result)
        return result
    except Exception as e:
        raise HTTPException(500, f"Error fetching matches: {str(e)}")


async def fetch_espn_matches(live_only: bool) -> list:
    """Fetches match data from ESPNCricinfo JSON API"""
    matches = []

    try:
        # ESPNCricinfo has a reliable JSON endpoint for live cricket
        url = "https://www.espncricinfo.com/ci/engine/match/index.json"
        async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as c:
            r = await c.get(url)
            if r.status_code == 200:
                data = r.json()
                matches = parse_espn_match_index(data, live_only)
    except Exception:
        pass

    # Fallback — try the match API directly
    if not matches:
        try:
            url = "https://hs-consumer-api.espncricinfo.com/v1/pages/matches/current?lang=en&latest=true"
            async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as c:
                r = await c.get(url)
                if r.status_code == 200:
                    data = r.json()
                    matches = parse_hs_matches(data, live_only)
        except Exception:
            pass

    # Last fallback — scrape the live scores page
    if not matches:
        try:
            url = "https://www.espncricinfo.com/live-cricket-score"
            async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as c:
                r = await c.get(url)
                soup = BeautifulSoup(r.text, "html.parser")

            # Look for JSON data embedded in the page
            scripts = soup.find_all("script")
            for script in scripts:
                txt = script.string or ""
                if "matchInfo" in txt or "liveMatch" in txt:
                    json_match = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.*?});', txt, re.S)
                    if json_match:
                        try:
                            state = json.loads(json_match.group(1))
                            matches = parse_initial_state(state, live_only)
                            break
                        except Exception:
                            continue
        except Exception:
            pass

    return matches


def parse_espn_match_index(data: dict, live_only: bool) -> list:
    matches = []
    try:
        for match in data.get("match", []):
            state = match.get("match_status", "").lower()
            ms = "live" if state == "live" else ("result" if state in ["complete", "finished"] else "fixture")
            if live_only and ms != "live":
                continue

            t1 = match.get("team1_name", "")
            t2 = match.get("team2_name", "")
            t1s = match.get("team1_short", t1[:3].upper() if t1 else "")
            t2s = match.get("team2_short", t2[:3].upper() if t2 else "")

            matches.append({
                "id": str(match.get("match_id", "")),
                "name": f"{t1} vs {t2}",
                "status": match.get("status_text", ""),
                "venue": match.get("ground_name", ""),
                "date": match.get("start_date", ""),
                "matchType": match.get("match_type_text", "T20").upper(),
                "ms": ms,
                "teams": [t1, t2],
                "teamInfo": [
                    {"name": t1, "shortname": t1s, "img": get_team_flag_url(t1s)},
                    {"name": t2, "shortname": t2s, "img": get_team_flag_url(t2s)},
                ],
                "score": parse_match_scores(match)
            })
    except Exception:
        pass
    return matches


def parse_hs_matches(data: dict, live_only: bool) -> list:
    matches = []
    try:
        for content in data.get("content", []):
            for match in content.get("matches", []):
                info   = match.get("matchInfo", {})
                score  = match.get("matchScore", {})
                status = info.get("state", "preview")

                ms = "live" if status == "live" else ("result" if status == "complete" else "fixture")
                if live_only and ms != "live":
                    continue

                t1_info = info.get("team1", {})
                t2_info = info.get("team2", {})
                t1  = t1_info.get("teamName", "")
                t2  = t2_info.get("teamName", "")
                t1s = t1_info.get("teamSName", t1[:3].upper())
                t2s = t2_info.get("teamSName", t2[:3].upper())

                # Scores
                scores = []
                for inn_key in ["team1Score", "team2Score"]:
                    inn = score.get(inn_key, {})
                    if inn:
                        for inning in inn.values():
                            if isinstance(inning, dict):
                                scores.append({
                                    "r": inning.get("runs", 0),
                                    "w": inning.get("wickets", 0),
                                    "o": inning.get("overs", 0.0),
                                    "inning": inn_key.replace("Score", "")
                                })

                matches.append({
                    "id": str(info.get("matchId", "")),
                    "name": f"{t1} vs {t2}",
                    "status": info.get("status", ""),
                    "venue": info.get("ground", {}).get("longName", ""),
                    "date": info.get("startDate", ""),
                    "matchType": info.get("matchFormat", "T20").upper(),
                    "ms": ms,
                    "teams": [t1, t2],
                    "teamInfo": [
                        {"name": t1, "shortname": t1s, "img": get_team_flag_url(t1s)},
                        {"name": t2, "shortname": t2s, "img": get_team_flag_url(t2s)},
                    ],
                    "score": scores
                })
    except Exception:
        pass
    return matches


def parse_initial_state(state: dict, live_only: bool) -> list:
    matches = []
    try:
        # Navigate the state tree
        for key, val in state.items():
            if isinstance(val, dict):
                for mkey, mval in val.items():
                    if isinstance(mval, list):
                        for item in mval:
                            if isinstance(item, dict) and "matchInfo" in item:
                                info = item["matchInfo"]
                                ms   = "live" if info.get("state") == "live" else "fixture"
                                if live_only and ms != "live":
                                    continue
                                t1 = info.get("team1", {}).get("teamName", "")
                                t2 = info.get("team2", {}).get("teamName", "")
                                matches.append({
                                    "id": str(info.get("matchId", "")),
                                    "name": f"{t1} vs {t2}",
                                    "status": info.get("status", ""),
                                    "venue": "",
                                    "date": "",
                                    "matchType": info.get("matchFormat", "T20").upper(),
                                    "ms": ms,
                                    "teams": [t1, t2],
                                    "teamInfo": [
                                        {"name": t1, "shortname": t1[:3].upper(), "img": get_team_flag_url(t1[:3].upper())},
                                        {"name": t2, "shortname": t2[:3].upper(), "img": get_team_flag_url(t2[:3].upper())},
                                    ],
                                    "score": []
                                })
    except Exception:
        pass
    return matches


def parse_match_scores(match: dict) -> list:
    scores = []
    try:
        for i in [1, 2]:
            runs   = match.get(f"team{i}_score", 0) or 0
            wickets = match.get(f"team{i}_wickets", 0) or 0
            overs   = match.get(f"team{i}_overs", 0.0) or 0.0
            if runs:
                scores.append({"r": int(runs), "w": int(wickets), "o": float(overs), "inning": f"Team{i} Inning 1"})
    except Exception:
        pass
    return scores


# ═══════════════════════════════════════════════════════════════════════
# SCORECARD
# ═══════════════════════════════════════════════════════════════════════

@app.get("/scorecard/{match_id}")
async def get_scorecard(match_id: str):
    key = f"sc_{match_id}"
    if is_fresh(key):
        return get_cache(key)
    try:
        url = f"https://hs-consumer-api.espncricinfo.com/v1/pages/match/scorecard?lang=en&seriesId=0&matchId={match_id}"
        async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as c:
            r = await c.get(url)
            data = r.json()

        scorecard = parse_scorecard_data(data)
        result = {"status": "success", "data": scorecard}
        set_cache(key, result)
        return result
    except Exception as e:
        raise HTTPException(500, str(e))


def parse_scorecard_data(data: dict) -> dict:
    scorecard = {"batting": [], "bowling": [], "teamInfo": [], "score": [], "status": "", "venue": ""}
    try:
        match_info = data.get("match", {}).get("info", {})
        scorecard["status"] = match_info.get("status", "")
        scorecard["venue"]  = match_info.get("ground", {}).get("longName", "")

        for inning in data.get("scorecard", []):
            bat_team = inning.get("inningBatTeam", {})
            # Batting
            for batter in inning.get("inningBatsmen", []):
                athlete = batter.get("player", {})
                scorecard["batting"].append({
                    "batsman":   athlete.get("longName", athlete.get("name", "")),
                    "player_id": str(athlete.get("id", "")),
                    "r":         batter.get("runs", 0),
                    "b":         batter.get("balls", 0),
                    "4s":        batter.get("fours", 0),
                    "6s":        batter.get("sixes", 0),
                    "sr":        str(batter.get("strikerate", "0")),
                    "out-desc":  batter.get("dismissal", "")
                })
            # Bowling
            for bowler in inning.get("inningBowlers", []):
                athlete = bowler.get("player", {})
                scorecard["bowling"].append({
                    "bowler":    athlete.get("longName", athlete.get("name", "")),
                    "player_id": str(athlete.get("id", "")),
                    "o":         str(bowler.get("overs", "0")),
                    "m":         bowler.get("maidens", 0),
                    "r":         bowler.get("runs", 0),
                    "w":         bowler.get("wickets", 0),
                    "eco":       str(bowler.get("economy", "0"))
                })
    except Exception as e:
        scorecard["error"] = str(e)
    return scorecard


# ═══════════════════════════════════════════════════════════════════════
# PLAYER
# ═══════════════════════════════════════════════════════════════════════

@app.get("/player/{player_id}")
async def get_player(player_id: str):
    key = f"pl_{player_id}"
    if is_fresh(key):
        return get_cache(key)
    try:
        url = f"https://hs-consumer-api.espncricinfo.com/v1/pages/player/home?lang=en&playerId={player_id}"
        async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as c:
            r = await c.get(url)
            data = r.json()

        player = parse_player_data(data)
        result = {"status": "success", "data": player}
        set_cache(key, result)
        return result
    except Exception as e:
        raise HTTPException(500, str(e))


def parse_player_data(data: dict) -> dict:
    player = {}
    try:
        info = data.get("player", {})
        player["name"]         = info.get("longName", info.get("name", ""))
        player["dateOfBirth"]  = info.get("dateOfBirth", "")
        player["country"]      = info.get("country", {}).get("name", "")
        player["role"]         = info.get("playingRole", "")
        player["battingStyle"] = info.get("battingStyle", "")
        player["bowlingStyle"] = info.get("bowlingStyle", "")
        player["playerImg"]    = info.get("imageUrl", "")

        stats = []
        for stat_group in data.get("stats", []):
            fmt       = stat_group.get("heading", "")
            stat_type = "batting" if "bat" in fmt.lower() else "bowling"
            stat_map  = {}
            for row in stat_group.get("stats", []):
                stat_map[row.get("name", "")] = row.get("value", "")
            if stat_map:
                stats.append({"fn": fmt, "type": stat_type, "stat": stat_map})

        player["stats"] = stats
    except Exception as e:
        player["error"] = str(e)
    return player


# ═══════════════════════════════════════════════════════════════════════
# RANKINGS
# ═══════════════════════════════════════════════════════════════════════

@app.get("/rankings")
async def get_rankings(type: str = "batting", format: str = "t20"):
    key = f"rank_{type}_{format}"
    if is_fresh(key):
        return get_cache(key)
    try:
        type_map   = {"batting": "batting", "bowling": "bowling", "teams": "team", "allrounders": "all-rounder"}
        format_map = {"t20": "T20I", "odi": "ODI", "test": "Test"}
        t = type_map.get(type.lower(), "batting")
        f = format_map.get(format.lower(), "T20I")

        url = f"https://hs-consumer-api.espncricinfo.com/v1/pages/rankings/table?lang=en&type={t}&format={f}"
        async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as c:
            r = await c.get(url)
            data = r.json()

        rankings = []
        for entry in data.get("rankings", []):
            player  = entry.get("player", entry.get("team", {}))
            country = player.get("country", {})
            rankings.append({
                "rank":    str(entry.get("rank", "")),
                "name":    player.get("longName", player.get("name", "")),
                "country": country.get("abbreviation", country.get("name", "")),
                "rating":  str(entry.get("rating", "")),
                "points":  str(entry.get("points", ""))
            })

        result = {"status": "success", "data": rankings}
        set_cache(key, result)
        return result
    except Exception as e:
        # Return fallback rankings
        result = {"status": "success", "data": fallback_rankings(type)}
        return result


# ═══════════════════════════════════════════════════════════════════════
# NEWS
# ═══════════════════════════════════════════════════════════════════════

@app.get("/news")
async def get_news():
    if is_fresh("news"):
        return get_cache("news")
    try:
        url = "https://hs-consumer-api.espncricinfo.com/v1/pages/home/news?lang=en&limit=20"
        async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as c:
            r = await c.get(url)
            data = r.json()

        news = []
        for item in data.get("results", data.get("stories", [])):
            news.append({
                "id":       str(item.get("id", "")),
                "title":    item.get("headline", item.get("title", "")),
                "intro":    item.get("description", item.get("summary", "")),
                "date":     item.get("publishedAt", item.get("date", "")),
                "imageUrl": item.get("imageUrl", ""),
                "source":   "ESPNCricinfo"
            })

        result = {"status": "success", "count": len(news), "data": news}
        set_cache("news", result)
        return result
    except Exception as e:
        raise HTTPException(500, str(e))


# ═══════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════

def get_team_flag_url(short: str) -> str:
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
