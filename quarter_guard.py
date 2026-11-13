"""Fail-closed NBA starting-lineup confirmation; never confuse a roster with starters."""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LINEUPS_URL = "https://stats.nba.com/js/data/leaders/00_daily_lineups_{day}.json"
# ESPN team abbreviations differ from NBA.com's official tricodes.
ESPN_TO_NBA = {"NY": "NYK", "GS": "GSW", "SA": "SAS", "NO": "NOP"}


def normalized(value: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", ascii_name.casefold())


def _get(value: dict, *keys):
    for key in keys:
        if key in value and value[key] is not None:
            return value[key]
    return None


def _team_code(value) -> str | None:
    if isinstance(value, str):
        code = value.upper().strip()
        return ESPN_TO_NBA.get(code, code) if 2 <= len(code) <= 4 else None
    if isinstance(value, dict):
        return _team_code(_get(value, "teamTricode", "teamAbbreviation", "teamCode", "abbreviation", "triCode", "teamAbbr"))
    return None


def confirmed_starters(payload: dict, game: dict) -> dict[str, set[str]] | None:
    """Require five individually Confirmed player records for BOTH scheduled teams."""
    required = {_team_code(t.get("abbreviation")): t.get("name", "") for t in game["teams"]}
    if len(required) != 2 or None in required:
        return None
    found = {code: set() for code in required}

    def walk(node, team=None):
        if isinstance(node, list):
            for item in node:
                walk(item, team)
            return
        if not isinstance(node, dict):
            return
        candidate_team = _team_code(_get(node, "teamTricode", "teamAbbreviation", "teamCode", "teamAbbr", "team"))
        if candidate_team in required:
            team = candidate_team
        name = _get(node, "playerName", "playerFullName", "displayName", "fullName", "name")
        status = _get(node, "lineupStatus", "lineup_status")
        if team in found and isinstance(name, str) and isinstance(status, str) and status.casefold() == "confirmed":
            found[team].add(normalized(name))
        for item in node.values():
            if isinstance(item, (list, dict)):
                walk(item, team)

    walk(payload)
    if any(len(names) != 5 for names in found.values()):
        return None
    return found


def fetch_confirmed_starters(game: dict) -> tuple[dict[str, set[str]] | None, str]:
    local_date = date.fromisoformat(game["date_et"][:10]).strftime("%Y%m%d")
    url = LINEUPS_URL.format(day=local_date)
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; quarterbot/2.0)", "Accept": "application/json"})
        with urlopen(req, timeout=12) as resp:
            payload = json.load(resp)
        return confirmed_starters(payload, game), url
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError, KeyError):
        return None, url


def gate_nba_predictions(game: dict, projections: list[dict], starters: dict[str, set[str]] | None,
                         source: str) -> list[dict]:
    if not starters:
        game["lineup_status"] = "unconfirmed"
        game["withheld_reason"] = "NBA starters are not individually confirmed for both teams; no official first-quarter projections."
        return []
    team_map = {t["name"]: _team_code(t.get("abbreviation", "")) for t in game["teams"]}
    eligible = [p for p in projections if normalized(p["player"]) in starters.get(team_map.get(p["team"]), set())]
    game["lineup_status"] = "confirmed"
    game["lineup_source"] = source
    for p in eligible:
        p["starter_confirmed"] = True
    return eligible
