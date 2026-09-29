"""Score cards and player cards: real scores and headshots from ESPN's public site data.

Claude only says which game or player comes up and when. The score and the
headshot always come from ESPN, and a card is dropped rather than guessed
when the lookup fails, so a wrong score never goes out.
"""

import hashlib
import json
import re
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

from .pick import TEAMS

ESPN = "https://site.api.espn.com/apis/site/v2/sports/football/{league}"
LEAGUES = {"NFL": "nfl", "CFB": "college-football"}
BY_NAME = {t["name"]: t for t in TEAMS}


class LookupFailed(Exception):
    pass


def attach_cards(clips: list[dict], day: date, cache: Path) -> None:
    """Turn each clip's flagged games and players into cards, looked up for real."""
    cache.mkdir(parents=True, exist_ok=True)
    warned = False
    for clip in clips:
        cards = []
        try:
            for g in clip.get("games", []):
                score = find_score(g["team"], g.get("opponent", ""), day, cache)
                if score:
                    a, h = score["sides"]
                    label = f"{a['abbr']} {a['score']}, {h['abbr']} {h['score']} ({score['status'].title()})"
                    cards.append({"kind": "score", "at": g["at"], "keep": True, "label": label, **score})
            for p in clip.get("players", []):
                player = find_player(p["name"], p.get("team", ""), cache)
                if player:
                    cards.append({"kind": "player", "at": p["at"], "keep": True, **player})
        except LookupFailed as e:
            if not warned:
                print(f"Note: couldn't reach ESPN for scores and headshots ({e}), so clips go without those cards.")
                warned = True
        clip["cards"] = sorted(cards, key=lambda c: c["at"])


def find_score(team: str, opponent: str, day: date, cache: Path) -> dict | None:
    """The latest finished game for `team` (against `opponent`, if given) in the 10 days up to `day`."""
    info = BY_NAME.get(team)
    if not info:
        return None
    league = LEAGUES[info["league"]]
    span = f"{day - timedelta(days=10):%Y%m%d}-{day:%Y%m%d}"
    extra = "&groups=80" if league == "college-football" else ""
    board = fetch_json(f"{ESPN.format(league=league)}/scoreboard?dates={span}&limit=500{extra}", cache)
    for event in sorted(board.get("events", []), key=lambda e: e.get("date", ""), reverse=True):
        comp = event["competitions"][0]
        status = comp.get("status", event.get("status", {})).get("type", {})
        if not status.get("completed"):
            continue
        sides = comp["competitors"]
        ours = [s for s in sides if matches(team, s["team"])]
        if not ours or (opponent and not any(matches(opponent, s["team"]) for s in sides)):
            continue
        away = next((s for s in sides if s.get("homeAway") == "away"), sides[0])
        home = next(s for s in sides if s is not away)
        return {
            "status": (status.get("shortDetail") or "Final").upper(),
            "sides": [side(away, info["league"]), side(home, info["league"])],
        }
    return None


def side(s: dict, league: str) -> dict:
    ours = next((t for t in TEAMS if t["league"] == league and matches(t["name"], s["team"])), None)
    return {
        "abbr": s["team"].get("abbreviation", "")[:4].upper(),
        "score": int(float(s.get("score") or 0)),
        "logo": ours["logo"] if ours else None,
        "winner": bool(s.get("winner")),
    }


def find_player(name: str, team: str, cache: Path) -> dict | None:
    info = BY_NAME.get(team)
    if not info:
        return None
    league = LEAGUES[info["league"]]
    teams = fetch_json(f"{ESPN.format(league=league)}/teams?limit=1000", cache)
    listed = [t["team"] for s in teams.get("sports", []) for lg in s.get("leagues", []) for t in lg.get("teams", [])]
    espn_team = next((t for t in listed if matches(team, t)), None)
    if not espn_team:
        return None
    roster = fetch_json(f"{ESPN.format(league=league)}/teams/{espn_team['id']}/roster", cache)
    people = []
    for a in roster.get("athletes", []):
        people += a.get("items", [a]) if "items" in a else [a]
    want = norm(name)
    found = [p for p in people if norm(p.get("fullName") or p.get("displayName", "")) == want]
    if not found:
        last = want.split()[-1] if want else ""
        found = [p for p in people if norm(p.get("lastName", "")) == last]
        if len(found) != 1:
            return None
    p = found[0]
    headshot = None
    href = (p.get("headshot") or {}).get("href")
    if href:
        try:
            headshot = str(fetch_file(href, cache, ".png"))
        except LookupFailed:
            headshot = None
    return {
        "name": p.get("fullName") or p.get("displayName") or name,
        "position": (p.get("position") or {}).get("abbreviation", ""),
        "team": team,
        "logo": info["logo"],
        "headshot": headshot,
        "label": p.get("fullName") or name,
    }


def matches(ours: str, espn_team: dict) -> bool:
    """Does one of our team names refer to this ESPN team?"""
    info = BY_NAME.get(ours)
    names = {norm(ours)} | {norm(a) for a in (info or {}).get("aliases", [])}
    theirs = {norm(espn_team.get(k, "")) for k in ("displayName", "location", "shortDisplayName")}
    return bool(names & theirs) or bool(info and info.get("abbr")
                                        and info["abbr"].upper() == espn_team.get("abbreviation", "").upper())


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower().replace("&", "and")).strip()


def fetch_json(url: str, cache: Path) -> dict:
    return json.loads(fetch_file(url, cache, ".json").read_text())


def fetch_file(url: str, cache: Path, suffix: str) -> Path:
    """Download once per day and keep it, so re-runs are instant and work offline."""
    key = hashlib.sha1(url.encode()).hexdigest()[:16]
    path = cache / f"{datetime.now():%Y%m%d}-{key}{suffix}"
    if path.exists():
        return path
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "f9yclip"})
        with urllib.request.urlopen(req, timeout=20) as r:
            path.write_bytes(r.read())
    except Exception as e:  # network down, blocked, or ESPN changed something
        raise LookupFailed(str(e)) from e
    return path
