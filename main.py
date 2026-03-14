from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
from bs4 import BeautifulSoup
import json
import re
from datetime import datetime
import asyncio

app = FastAPI(title="CricScore API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Headers that mimic a real browser so crex.com doesn't block us ────
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

BASE_URL = "https://crex.com"

# ── In-memory cache (avoid hammering crex.com) ────────────────────────
cache = {}
CACHE_TTL = 15  # seconds


def is_cache_valid(key: str) -> bool:
    if key not in cache:
        return False
    age = (datetime.now() - cache[key]["time"]).seconds
    return age < CACHE_TTL


def set_cache(key: str, data):
    cache[key] = {"data": data, "time": datetime.now()}


def get_cache(key: str):
    return cache[key]["data"] if key in cache else None


# ═══════════════════════════════════════════════════════════════════════
# ENDPOINT 1 — Live Matches
# GET /live
# ═══════════════════════════════════════════════════════════════════════

@app.get("/live")
async def get_live_matches():
    """Returns all currently live cricket matches"""
    if is_cache_valid("live"):
        return get_cache("live")

    try:
        async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=20) as client:
            response = await client.get(f"{BASE_URL}/live-matches")
            soup = BeautifulSoup(response.text, "html.parser")

        matches = parse_match_cards(soup, "live")
        result = {"status": "success", "count": len(matches), "data": matches}
        set_cache("live", result)
        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════════════════
# ENDPOINT 2 — All Matches (Live + Upcoming + Finished)
# GET /matches
# ═══════════════════════════════════════════════════════════════════════

@app.get("/matches")
async def get_all_matches():
    """Returns all matches — live, upcoming, finished"""
    if is_cache_valid("matches"):
        return get_cache("matches")

    try:
        async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=20) as client:
            response = await client.get(BASE_URL)
            soup = BeautifulSoup(response.text, "html.parser")

        matches = parse_match_cards(soup, "all")
        result = {"status": "success", "count": len(matches), "data": matches}
        set_cache("matches", result)
        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════════════════
# ENDPOINT 3 — Match Scorecard
# GET /scorecard?url=/scoreboard/...
# ═══════════════════════════════════════════════════════════════════════

@app.get("/scorecard")
async def get_scorecard(url: str):
    """Returns full scorecard for a match. Pass the crex.com match path as url param"""
    cache_key = f"scorecard_{url}"
    if is_cache_valid(cache_key):
        return get_cache(cache_key)

    try:
        full_url = f"{BASE_URL}{url}" if url.startswith("/") else url
        async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=20) as client:
            response = await client.get(full_url)
            soup = BeautifulSoup(response.text, "html.parser")

        scorecard = parse_scorecard(soup)
        result = {"status": "success", "data": scorecard}
        set_cache(cache_key, result)
        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════════════════
# ENDPOINT 4 — Player Profile
# GET /player?url=/player/virat-kohli-J3
# ═══════════════════════════════════════════════════════════════════════

@app.get("/player")
async def get_player(url: str):
    """Returns player profile and career stats"""
    cache_key = f"player_{url}"
    if is_cache_valid(cache_key):
        return get_cache(cache_key)

    try:
        full_url = f"{BASE_URL}{url}" if url.startswith("/") else url
        async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=20) as client:
            response = await client.get(full_url)
            soup = BeautifulSoup(response.text, "html.parser")

        player = parse_player(soup)
        result = {"status": "success", "data": player}
        set_cache(cache_key, result)
        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════════════════
# ENDPOINT 5 — ICC Rankings
# GET /rankings?type=batting&format=t20
# ═══════════════════════════════════════════════════════════════════════

@app.get("/rankings")
async def get_rankings(type: str = "batting", format: str = "t20"):
    """Returns ICC rankings. type=batting|bowling|teams, format=test|odi|t20"""
    cache_key = f"rankings_{type}_{format}"
    if is_cache_valid(cache_key):
        return get_cache(cache_key)

    try:
        url = f"{BASE_URL}/rankings/men/{type}"
        async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=20) as client:
            response = await client.get(url)
            soup = BeautifulSoup(response.text, "html.parser")

        rankings = parse_rankings(soup)
        result = {"status": "success", "data": rankings}
        set_cache(cache_key, result)
        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════════════════
# ENDPOINT 6 — Health Check
# GET /health
# ═══════════════════════════════════════════════════════════════════════

@app.get("/health")
async def health():
    return {"status": "ok", "message": "CricScore API is running"}


@app.get("/")
async def root():
    return {
        "name": "CricScore API",
        "version": "1.0.0",
        "endpoints": [
            "GET /live          — Live matches",
            "GET /matches       — All matches",
            "GET /scorecard?url — Full scorecard",
            "GET /player?url    — Player profile",
            "GET /rankings      — ICC rankings",
            "GET /health        — Health check",
        ]
    }


# ═══════════════════════════════════════════════════════════════════════
# PARSERS — Extract data from crex.com HTML
# ═══════════════════════════════════════════════════════════════════════

def parse_match_cards(soup: BeautifulSoup, mode: str) -> list:
    matches = []

    # crex.com match cards are anchor tags with match data
    cards = soup.find_all("a", href=re.compile(r"/scoreboard/"))

    for card in cards:
        try:
            href = card.get("href", "")

            # Extract team images (flags)
            imgs = card.find_all("img")
            team_imgs = [img.get("src", "") for img in imgs if "cricketvectors" in img.get("src", "")]

            # Extract team names
            team_names = []
            for img in imgs:
                if "cricketvectors" in img.get("src", ""):
                    alt = img.get("alt", "").replace(" flag", "").strip()
                    if alt:
                        team_names.append(alt)

            # Extract scores — look for score patterns like "274-10" or "114-10"
            text = card.get_text(" ", strip=True)
            score_pattern = re.findall(r'(\d{1,3})-(\d{1,2})', text)
            overs_pattern = re.findall(r'(\d{1,2}\.\d)', text)

            # Match state
            is_live = "Live" in text or "live" in card.get("class", [])
            state = "live" if is_live else ("result" if any(w in text for w in ["won", "Won", "draw", "Draw"]) else "fixture")

            # Status text — last meaningful text chunk
            status = ""
            for s in [text[i:i+80] for i in range(0, len(text), 80)]:
                if any(w in s for w in ["won", "Won", "needed", "opt", "Yet"]):
                    status = s.strip()
                    break

            # Extract match type from URL
            match_type = "T20"
            if "ODI" in href or "ODI" in text:
                match_type = "ODI"
            elif "Test" in href or "Test" in text:
                match_type = "Test"

            # Build score entries
            scores = []
            for i, (runs, wkts) in enumerate(score_pattern[:2]):
                overs = overs_pattern[i] if i < len(overs_pattern) else "0.0"
                inning_team = team_names[i] if i < len(team_names) else f"Team {i+1}"
                scores.append({
                    "r": int(runs),
                    "w": int(wkts),
                    "o": float(overs),
                    "inning": f"{inning_team} Inning {i+1}"
                })

            # Build teamInfo
            team_info = []
            for i, name in enumerate(team_names[:2]):
                short = name[:3].upper() if len(name) >= 3 else name.upper()
                img_url = team_imgs[i] if i < len(team_imgs) else ""
                team_info.append({
                    "name": name,
                    "shortname": short,
                    "img": img_url
                })

            if len(team_names) >= 2:
                matches.append({
                    "id": href,           # use URL as ID
                    "name": f"{team_names[0]} vs {team_names[1]}",
                    "status": status,
                    "venue": "",
                    "matchType": match_type,
                    "ms": state,
                    "teams": team_names[:2],
                    "teamInfo": team_info,
                    "score": scores,
                    "scorecard_url": href
                })

        except Exception:
            continue

    # Deduplicate by id
    seen = set()
    unique = []
    for m in matches:
        if m["id"] not in seen:
            seen.add(m["id"])
            unique.append(m)

    return unique


def parse_scorecard(soup: BeautifulSoup) -> dict:
    scorecard = {
        "batting": [],
        "bowling": [],
        "matchInfo": {}
    }

    try:
        # ── Match info ────────────────────────────────────────────────
        title = soup.find("h1")
        if title:
            scorecard["matchInfo"]["title"] = title.get_text(strip=True)

        # ── Batting table ─────────────────────────────────────────────
        tables = soup.find_all("table")
        for table in tables:
            headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]

            # Batting table has R, B, 4s, 6s, SR columns
            if "r" in headers and "b" in headers and "sr" in headers:
                for row in table.find_all("tr")[1:]:  # skip header
                    cols = row.find_all("td")
                    if len(cols) >= 6:
                        # Player name — first col, may have a link
                        name_el = cols[0].find("a")
                        name = name_el.get_text(strip=True) if name_el else cols[0].get_text(strip=True)
                        player_url = name_el.get("href", "") if name_el else ""

                        out_desc_el = cols[0].find_all("span")
                        out_desc = out_desc_el[-1].get_text(strip=True) if len(out_desc_el) > 1 else ""

                        try:
                            scorecard["batting"].append({
                                "batsman": name,
                                "player_url": player_url,
                                "r": int(cols[1].get_text(strip=True) or 0),
                                "b": int(cols[2].get_text(strip=True) or 0),
                                "4s": int(cols[3].get_text(strip=True) or 0),
                                "6s": int(cols[4].get_text(strip=True) or 0),
                                "sr": cols[5].get_text(strip=True),
                                "out-desc": out_desc
                            })
                        except (ValueError, IndexError):
                            continue

            # Bowling table has O, M, R, W, ER columns
            elif "o" in headers and "m" in headers and "w" in headers and "er" in headers:
                for row in table.find_all("tr")[1:]:
                    cols = row.find_all("td")
                    if len(cols) >= 6:
                        name_el = cols[0].find("a")
                        name = name_el.get_text(strip=True) if name_el else cols[0].get_text(strip=True)
                        player_url = name_el.get("href", "") if name_el else ""

                        try:
                            scorecard["bowling"].append({
                                "bowler": name,
                                "player_url": player_url,
                                "o": cols[1].get_text(strip=True),
                                "m": int(cols[2].get_text(strip=True) or 0),
                                "r": int(cols[3].get_text(strip=True) or 0),
                                "w": int(cols[4].get_text(strip=True) or 0),
                                "eco": cols[5].get_text(strip=True)
                            })
                        except (ValueError, IndexError):
                            continue

    except Exception as e:
        scorecard["error"] = str(e)

    return scorecard


def parse_player(soup: BeautifulSoup) -> dict:
    player = {}
    try:
        # Name
        h1 = soup.find("h1")
        if h1:
            player["name"] = h1.get_text(strip=True)

        # All text content for bio info
        text = soup.get_text(" ", strip=True)

        # Look for DOB
        dob_match = re.search(r'Born[:\s]+([A-Za-z]+ \d{1,2},? \d{4})', text)
        if dob_match:
            player["dateOfBirth"] = dob_match.group(1)

        # Role
        for role in ["Batsman", "Bowler", "All-rounder", "Wicket-keeper", "Batting"]:
            if role in text:
                player["role"] = role
                break

        # Batting style
        bat_match = re.search(r'Batting[:\s]+(Right|Left)[- ]?[Hh]and', text)
        if bat_match:
            player["battingStyle"] = f"{bat_match.group(1)}-hand bat"

        # Bowling style
        bowl_match = re.search(r'Bowling[:\s]+([A-Za-z\- ]+(?:medium|fast|spin|off|leg)[A-Za-z\- ]*)', text, re.I)
        if bowl_match:
            player["bowlingStyle"] = bowl_match.group(1).strip()

        # Stats tables
        tables = soup.find_all("table")
        stats = []
        for table in tables:
            headers = [th.get_text(strip=True) for th in table.find_all("th")]
            if not headers:
                continue
            rows = table.find_all("tr")[1:]
            for row in rows:
                cols = row.find_all("td")
                if cols:
                    stat_map = {headers[i]: cols[i].get_text(strip=True) for i in range(min(len(headers), len(cols)))}
                    fmt = stat_map.get("Format", stat_map.get(headers[0], ""))
                    stat_type = "batting" if any(k in headers for k in ["Runs", "Ave", "SR"]) else "bowling"
                    stats.append({"fn": fmt, "type": stat_type, "stat": stat_map})

        player["stats"] = stats

        # Player image
        img = soup.find("img", {"class": re.compile(r"player", re.I)})
        if img:
            player["playerImg"] = img.get("src", "")

    except Exception as e:
        player["error"] = str(e)

    return player


def parse_rankings(soup: BeautifulSoup) -> list:
    rankings = []
    try:
        tables = soup.find_all("table")
        for table in tables:
            rows = table.find_all("tr")
            for i, row in enumerate(rows[1:], 1):  # skip header
                cols = row.find_all("td")
                if len(cols) >= 3:
                    # Flag image → country
                    img = cols[0].find("img") or (cols[1].find("img") if len(cols) > 1 else None)
                    country = img.get("alt", "").replace(" flag", "") if img else ""

                    # Name
                    name_el = row.find("a")
                    name = name_el.get_text(strip=True) if name_el else cols[1].get_text(strip=True) if len(cols) > 1 else ""

                    # Rating and points from last columns
                    rating = cols[-2].get_text(strip=True) if len(cols) >= 2 else ""
                    points = cols[-1].get_text(strip=True) if len(cols) >= 1 else ""

                    if name:
                        rankings.append({
                            "rank": str(i),
                            "name": name,
                            "country": country,
                            "rating": rating,
                            "points": points
                        })

    except Exception as e:
        pass

    return rankings
