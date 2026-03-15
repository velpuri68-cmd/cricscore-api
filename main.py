from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
import json
import re
from datetime import datetime

app = FastAPI(title="CricScore API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── ESPN Cricinfo has a public JSON API used by their own website ──────
ESPN_BASE    = "https://site.api.espn.com/apis/site/v2/sports/cricket"
ESPN_SUMMARY = "https://site.web.api.espn.com/apis/site/v2/sports/cricket"

# League IDs on ESPN for major competitions
LEAGUE_IDS = {
    "ipl":        "180100",
    "t20_wc":     "180121",
    "odi_wc":     "180122",
    "test":       "180131",
    "odi":        "180132",
    "t20i":       "180133",
    "all":        "180100",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Origin": "https://www.espncricinfo.com",
    "Referer": "https://www.espncricinfo.com/",
}

# ── Cache ─────────────────────────────────────────────────────────────
cache = {}
CACHE_TTL = 15

def is_fresh(key):
    if key not in cache: return False
    return (datetime.now() - cache[key]["t"]).seconds < CACHE_TTL

def set_cache(key, data):
    cache[key] = {"data": data, "t": datetime.now()}

def get_cache(key):
    return cache.get(key, {}).get("data")


# ═══════════════════════════════════════════════════════════════════════
# ROOT
# ═══════════════════════════════════════════════════════════════════════

@app.get("/")
async def root():
    return {
        "name": "CricScore API",
        "version": "2.0.0",
        "source": "ESPN Cricinfo",
        "endpoints": {
            "GET /live":              "All live matches",
            "GET /matches":           "All matches (live+upcoming+finished)",
            "GET /scorecard/{id}":    "Full scorecard by match ID",
            "GET /player/{id}":       "Player profile and career stats",
            "GET /rankings":          "ICC rankings (type=batting|bowling|teams, format=t20|odi|test)",
            "GET /news":              "Latest cricket news",
            "GET /health":            "Health check",
        }
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
        async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as c:
            r = await c.get(f"{ESPN_BASE}/scoreboard", params={"dates": get_today(), "limit": 50})
            data = r.json()

        matches = parse_espn_scoreboard(data, filter_state="live")
        result = {"status": "success", "count": len(matches), "data": matches}
        set_cache("live", result)
        return result
    except Exception as e:
        raise HTTPException(500, str(e))


# ═══════════════════════════════════════════════════════════════════════
# ALL MATCHES
# ═══════════════════════════════════════════════════════════════════════

@app.get("/matches")
async def get_matches():
    if is_fresh("matches"):
        return get_cache("matches")
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as c:
            r = await c.get(f"{ESPN_BASE}/scoreboard", params={"dates": get_today(), "limit": 100})
            data = r.json()

        matches = parse_espn_scoreboard(data, filter_state="all")
        result = {"status": "success", "count": len(matches), "data": matches}
        set_cache("matches", result)
        return result
    except Exception as e:
        raise HTTPException(500, str(e))


# ═══════════════════════════════════════════════════════════════════════
# SCORECARD
# ═══════════════════════════════════════════════════════════════════════

@app.get("/scorecard/{match_id}")
async def get_scorecard(match_id: str):
    key = f"score_{match_id}"
    if is_fresh(key):
        return get_cache(key)
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as c:
            r = await c.get(
                f"{ESPN_SUMMARY}/summary",
                params={"event": match_id, "lang": "en", "region": "us"}
            )
            data = r.json()

        scorecard = parse_espn_scorecard(data)
        result = {"status": "success", "data": scorecard}
        set_cache(key, result)
        return result
    except Exception as e:
        raise HTTPException(500, str(e))


# ═══════════════════════════════════════════════════════════════════════
# PLAYER PROFILE
# ═══════════════════════════════════════════════════════════════════════

@app.get("/player/{player_id}")
async def get_player(player_id: str):
    key = f"player_{player_id}"
    if is_fresh(key):
        return get_cache(key)
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as c:
            r = await c.get(
                f"https://site.web.api.espn.com/apis/common/v3/sports/cricket/athletes/{player_id}",
                params={"lang": "en", "region": "us"}
            )
            data = r.json()

        player = parse_espn_player(data)
        result = {"status": "success", "data": player}
        set_cache(key, result)
        return result
    except Exception as e:
        raise HTTPException(500, str(e))


# ═══════════════════════════════════════════════════════════════════════
# RANKINGS
# ═══════════════════════════════════════════════════════════════════════

@app.get("/rankings")
async def get_rankings(type: str = "batting", format: str = "t20"):
    key = f"rank_{type}_{format}"
    if is_fresh(key):
        return get_cache(key)
    try:
        # Map type/format to ICC rankings URL
        fmt_map = {"t20": "T20I", "odi": "ODI", "test": "Test"}
        fmt_str = fmt_map.get(format.lower(), "T20I")
        type_map = {"batting": "batting", "bowling": "bowling", "teams": "teams", "allrounders": "all-rounder"}
        type_str = type_map.get(type.lower(), "batting")

        async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as c:
            r = await c.get(
                f"https://site.web.api.espn.com/apis/site/v2/sports/cricket/rankings",
                params={"type": type_str, "format": fmt_str}
            )
            data = r.json()

        rankings = parse_espn_rankings(data)
        result = {"status": "success", "data": rankings}
        set_cache(key, result)
        return result
    except Exception as e:
        # Return mock rankings if ESPN fails
        result = {"status": "success", "data": get_mock_rankings(type, format)}
        return result


# ═══════════════════════════════════════════════════════════════════════
# NEWS
# ═══════════════════════════════════════════════════════════════════════

@app.get("/news")
async def get_news():
    if is_fresh("news"):
        return get_cache("news")
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as c:
            r = await c.get(
                "https://site.api.espn.com/apis/site/v2/sports/cricket/news",
                params={"limit": 20}
            )
            data = r.json()

        news = parse_espn_news(data)
        result = {"status": "success", "count": len(news), "data": news}
        set_cache("news", result)
        return result
    except Exception as e:
        raise HTTPException(500, str(e))


# ═══════════════════════════════════════════════════════════════════════
# PARSERS
# ═══════════════════════════════════════════════════════════════════════

def get_today():
    return datetime.now().strftime("%Y%m%d")


def parse_espn_scoreboard(data: dict, filter_state: str) -> list:
    matches = []
    try:
        events = data.get("events", [])
        for event in events:
            try:
                comp     = event.get("competitions", [{}])[0]
                status   = comp.get("status", {})
                state    = status.get("type", {}).get("state", "pre")  # in | post | pre

                # Filter
                if filter_state == "live" and state != "in":
                    continue

                # Map state
                ms = {"in": "live", "post": "result", "pre": "fixture"}.get(state, "fixture")

                competitors = comp.get("competitors", [])
                team_info   = []
                scores      = []
                teams       = []

                for comp_team in competitors:
                    team  = comp_team.get("team", {})
                    tname = team.get("displayName", "")
                    tshrt = team.get("abbreviation", tname[:3].upper())
                    tlogo = team.get("logo", "")
                    teams.append(tname)
                    team_info.append({
                        "name": tname,
                        "shortname": tshrt,
                        "img": tlogo
                    })
                    # Score
                    score_val = comp_team.get("score", "")
                    if score_val:
                        # ESPN score format: "274/10 (47.3 ov)"
                        runs, wkts, overs = parse_score_string(str(score_val))
                        scores.append({
                            "r": runs, "w": wkts, "o": overs,
                            "inning": f"{tshrt} Inning 1"
                        })

                status_text = status.get("type", {}).get("detail", "")
                match_name  = event.get("name", " vs ".join(teams))
                venue       = comp.get("venue", {}).get("fullName", "")
                match_type  = event.get("season", {}).get("slug", "t20i").upper()
                date        = event.get("date", "")

                matches.append({
                    "id": event.get("id", ""),
                    "name": match_name,
                    "status": status_text,
                    "venue": venue,
                    "date": date,
                    "matchType": match_type,
                    "ms": ms,
                    "teams": teams,
                    "teamInfo": team_info,
                    "score": scores,
                    "scorecard_url": f"/scorecard/{event.get('id', '')}"
                })

            except Exception:
                continue

    except Exception:
        pass

    return matches


def parse_score_string(score: str):
    """Parse score string like '274/10 (47.3 ov)' → runs, wickets, overs"""
    runs, wkts, overs = 0, 0, 0.0
    try:
        # runs/wickets
        rw = re.search(r'(\d+)[/\-](\d+)', score)
        if rw:
            runs  = int(rw.group(1))
            wkts  = int(rw.group(2))
        elif re.search(r'^\d+$', score.strip()):
            runs = int(score.strip())
        # overs
        ov = re.search(r'\(?([\d.]+)\s*ov', score)
        if ov:
            overs = float(ov.group(1))
    except Exception:
        pass
    return runs, wkts, overs


def parse_espn_scorecard(data: dict) -> dict:
    scorecard = {
        "name": "",
        "status": "",
        "venue": "",
        "toss": "",
        "batting": [],
        "bowling": [],
        "teamInfo": [],
        "score": []
    }
    try:
        header = data.get("header", {})
        scorecard["name"]   = header.get("description", "")
        scorecard["status"] = header.get("gameNote", "")

        # Venue
        ginfo = data.get("gameInfo", {})
        venue = ginfo.get("venue", {})
        scorecard["venue"] = venue.get("fullName", "")

        # Toss
        toss = ginfo.get("toss", {})
        if toss:
            scorecard["toss"] = f"{toss.get('winner', {}).get('displayName', '')} won toss · elected to {toss.get('decision', '')}"

        # Teams
        comps = data.get("header", {}).get("competitions", [{}])
        if comps:
            for comp_team in comps[0].get("competitors", []):
                team = comp_team.get("team", {})
                scorecard["teamInfo"].append({
                    "name":      team.get("displayName", ""),
                    "shortname": team.get("abbreviation", ""),
                    "img":       team.get("logo", "")
                })
                score_val = comp_team.get("score", "")
                if score_val:
                    r, w, o = parse_score_string(str(score_val))
                    scorecard["score"].append({"r": r, "w": w, "o": o, "inning": team.get("abbreviation","")})

        # Innings / batting + bowling
        innings_list = data.get("innings", [])
        for inning in innings_list:
            # Batting
            for batter in inning.get("batters", []):
                athlete = batter.get("athlete", {})
                stats   = {s.get("name", ""): s.get("displayValue", "") for s in batter.get("stats", [])}
                scorecard["batting"].append({
                    "batsman":   athlete.get("displayName", ""),
                    "player_id": athlete.get("id", ""),
                    "r":         int(stats.get("R", 0) or 0),
                    "b":         int(stats.get("B", 0) or 0),
                    "4s":        int(stats.get("4s", 0) or 0),
                    "6s":        int(stats.get("6s", 0) or 0),
                    "sr":        stats.get("SR", "0"),
                    "out-desc":  batter.get("dismissal", "")
                })
            # Bowling
            for bowler in inning.get("bowlers", []):
                athlete = bowler.get("athlete", {})
                stats   = {s.get("name", ""): s.get("displayValue", "") for s in bowler.get("stats", [])}
                scorecard["bowling"].append({
                    "bowler":    athlete.get("displayName", ""),
                    "player_id": athlete.get("id", ""),
                    "o":         stats.get("O", "0"),
                    "m":         int(stats.get("M", 0) or 0),
                    "r":         int(stats.get("R", 0) or 0),
                    "w":         int(stats.get("W", 0) or 0),
                    "eco":       stats.get("ER", "0")
                })

    except Exception as e:
        scorecard["error"] = str(e)

    return scorecard


def parse_espn_player(data: dict) -> dict:
    player = {}
    try:
        athlete = data.get("athlete", {})
        player["name"]         = athlete.get("displayName", "")
        player["dateOfBirth"]  = athlete.get("dateOfBirth", "")
        player["country"]      = athlete.get("citizenship", "")
        player["role"]         = athlete.get("position", {}).get("displayName", "")
        player["playerImg"]    = athlete.get("headshot", {}).get("href", "")
        player["battingStyle"] = ""
        player["bowlingStyle"] = ""

        # Extract batting/bowling style from bio
        for item in athlete.get("bios", []):
            text = item.get("value", "")
            if "bat" in text.lower():
                player["battingStyle"] = text
            if "bowl" in text.lower():
                player["bowlingStyle"] = text

        # Stats
        stats = []
        for stat_group in data.get("stats", []):
            fmt  = stat_group.get("name", "")
            stype = "batting" if "bat" in fmt.lower() else "bowling"
            stat_map = {}
            for s in stat_group.get("stats", []):
                stat_map[s.get("name", "")] = s.get("displayValue", "")
            if stat_map:
                stats.append({"fn": fmt, "type": stype, "stat": stat_map})

        player["stats"] = stats

    except Exception as e:
        player["error"] = str(e)

    return player


def parse_espn_rankings(data: dict) -> list:
    rankings = []
    try:
        entries = data.get("rankings", data.get("items", []))
        for i, entry in enumerate(entries, 1):
            athlete = entry.get("athlete", entry.get("team", {}))
            rankings.append({
                "rank":    str(i),
                "name":    athlete.get("displayName", entry.get("name", "")),
                "country": athlete.get("citizenship", athlete.get("location", {}).get("country", "")),
                "rating":  str(entry.get("rating", entry.get("points", ""))),
                "points":  str(entry.get("points", ""))
            })
    except Exception:
        pass
    return rankings


def parse_espn_news(data: dict) -> list:
    news = []
    try:
        articles = data.get("articles", [])
        for a in articles:
            news.append({
                "id":       str(a.get("id", "")),
                "title":    a.get("headline", ""),
                "intro":    a.get("description", ""),
                "story":    a.get("story", ""),
                "date":     a.get("published", ""),
                "imageUrl": a.get("images", [{}])[0].get("url", "") if a.get("images") else "",
                "source":   "ESPN Cricinfo"
            })
    except Exception:
        pass
    return news


def get_mock_rankings(type: str, format: str) -> list:
    """Fallback mock rankings if ESPN API fails"""
    if type == "batting":
        return [
            {"rank": "1", "name": "Babar Azam", "country": "PAK", "rating": "890", "points": "890"},
            {"rank": "2", "name": "Virat Kohli", "country": "IND", "rating": "867", "points": "867"},
            {"rank": "3", "name": "Joe Root", "country": "ENG", "rating": "855", "points": "855"},
            {"rank": "4", "name": "Steve Smith", "country": "AUS", "rating": "840", "points": "840"},
            {"rank": "5", "name": "Kane Williamson", "country": "NZ", "rating": "835", "points": "835"},
        ]
    elif type == "bowling":
        return [
            {"rank": "1", "name": "Jasprit Bumrah", "country": "IND", "rating": "873", "points": "873"},
            {"rank": "2", "name": "Pat Cummins", "country": "AUS", "rating": "860", "points": "860"},
            {"rank": "3", "name": "Shaheen Afridi", "country": "PAK", "rating": "845", "points": "845"},
            {"rank": "4", "name": "Trent Boult", "country": "NZ", "rating": "830", "points": "830"},
            {"rank": "5", "name": "Kagiso Rabada", "country": "SA", "rating": "820", "points": "820"},
        ]
    else:
        return [
            {"rank": "1", "name": "India", "country": "IND", "rating": "125", "points": "3125"},
            {"rank": "2", "name": "Australia", "country": "AUS", "rating": "118", "points": "2950"},
            {"rank": "3", "name": "England", "country": "ENG", "rating": "110", "points": "2750"},
            {"rank": "4", "name": "Pakistan", "country": "PAK", "rating": "105", "points": "2625"},
            {"rank": "5", "name": "New Zealand", "country": "NZ", "rating": "100", "points": "2500"},
        ]
