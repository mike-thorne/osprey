
Claude Desktop (macOS), Connected









































Feed · PY
"""Osprey: personal sports feed. Checks ESPN scoreboards and pushes alerts to ntfy.
 
Run:  python feed.py          normal check
      python feed.py --test   send one test push to every channel
Env:  NTFY_PREFIX  (required) long random string, e.g. mike-x7k2p9qd
      DRY_RUN=1    print alerts instead of sending
"""
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
 
ET = ZoneInfo("America/New_York")
BASE = "https://site.api.espn.com/apis/site/v2/sports"
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
PREFIX = os.environ.get("NTFY_PREFIX", "")
DRY_RUN = os.environ.get("DRY_RUN") == "1"
 
QUIET_START, QUIET_END = 23, 7      # 11pm to 7am ET
START_WINDOW_MIN = 40               # "starting soon" fires inside this window
 
# channel = ntfy topic suffix. espn_name must match ESPN's team displayName.
TEAMS = [
    {"channel": "giants", "espn_name": "New York Giants", "short": "Giants",
     "tag": "football", "leagues": ["football/nfl"], "rule": "nfl",
     "start_alert": True, "score_alerts": False},
    {"channel": "yankees", "espn_name": "New York Yankees", "short": "Yankees",
     "tag": "baseball", "leagues": ["baseball/mlb"], "rule": "mlb",
     "start_alert": False, "score_alerts": False},
    {"channel": "knicks", "espn_name": "New York Knicks", "short": "Knicks",
     "tag": "basketball", "leagues": ["basketball/nba"], "rule": "nba",
     "start_alert": True, "score_alerts": False},
    {"channel": "arsenal", "espn_name": "Arsenal", "short": "Arsenal",
     "tag": "soccer", "leagues": ["soccer/eng.1", "soccer/uefa.champions"],
     "rule": "soccer", "start_alert": True, "score_alerts": True, "lineup": True},
]
LINEUP_WINDOW_MIN = 90              # start looking for the XI this long before kickoff
 
 
def now_et():
    return datetime.now(ET)
 
 
def is_quiet(dt):
    return dt.hour >= QUIET_START or dt.hour < QUIET_END
 
 
def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)
 
 
def fetch(league, dt):
    """Yesterday, today and tomorrow, one day per request (ESPN rejects date ranges)."""
    events = {}
    for offset in (-1, 0, 1):
        day = dt + timedelta(days=offset)
        data = get_json(f"{BASE}/{league}/scoreboard?dates={day:%Y%m%d}")
        for e in data.get("events", []):
            events[e["id"]] = e
    return {"events": list(events.values())}
 
 
def lineup_for(league, event_id, espn_name):
    """Returns (formation, starters, bench) or None if the lineup isn't posted yet."""
    data = get_json(f"{BASE}/{league}/summary?event={event_id}")
    for side in data.get("rosters", []) or []:
        if (side.get("team") or {}).get("displayName") != espn_name:
            continue
        players = side.get("roster", []) or []
        name = lambda p: (p.get("athlete") or {}).get("displayName", "?")
        starters = [name(p) for p in players if p.get("starter")]
        bench = [name(p) for p in players if not p.get("starter")]
        if len(starters) >= 11:
            return side.get("formation", ""), starters, bench
    return None
 
 
def push(channel, title, message, tag, priority=3, click=None):
    if DRY_RUN or not PREFIX:
        print(f"[{channel}] {title} | {message}")
        return
    body = {"topic": f"{PREFIX}-{channel}", "title": title, "message": message,
            "tags": [tag], "priority": priority}
    if click:
        body["click"] = click
    req = urllib.request.Request("https://ntfy.sh", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=20).read()
 
 
def to_int(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return 0
 
 
def channel_for(comp):
    names = []
    for b in comp.get("broadcasts", []) or []:
        names += b.get("names", []) or []
    if not names:
        for g in comp.get("geoBroadcasts", []) or []:
            n = (g.get("media") or {}).get("shortName")
            if n:
                names.append(n)
    seen = []
    for n in names:
        if n not in seen:
            seen.append(n)
    return ", ".join(seen[:2])
 
 
def close_and_late(rule, status, diff):
    period = status.get("period", 0) or 0
    clock = status.get("clock", 0) or 0
    if rule == "nfl":
        return period >= 4 and diff <= 8
    if rule == "nba":
        return period >= 4 and clock <= 300 and diff <= 5
    if rule == "mlb":
        return period >= 7 and diff <= 1
    if rule == "soccer":
        m = re.match(r"(\d+)", status.get("displayClock", "") or "")
        return bool(m) and int(m.group(1)) >= 75 and diff <= 1
    return False
 
 
def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {"events": {}, "held": []}
 
 
def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=1, sort_keys=True)
 
 
def check_event(team, league, event, state, now):
    comp = event["competitions"][0]
    sides = comp.get("competitors", [])
    mine = next((c for c in sides if c["team"].get("displayName") == team["espn_name"]), None)
    if not mine or len(sides) != 2:
        return
    opp = next(c for c in sides if c is not mine)
    opp_name = opp["team"].get("shortDisplayName") or opp["team"].get("displayName", "?")
    status = comp.get("status") or event.get("status") or {}
    phase = (status.get("type") or {}).get("state", "pre")   # pre / in / post
    detail = (status.get("type") or {}).get("shortDetail", "")
    my_score, opp_score = to_int(mine.get("score")), to_int(opp.get("score"))
    link = next((l.get("href") for l in event.get("links", []) if l.get("href")), None)
    tv = channel_for(comp)
    quiet = is_quiet(now)
 
    eid = f'{team["channel"]}:{event["id"]}'
    first_time = eid not in state["events"]
    rec = state["events"].setdefault(eid, {"date": event.get("date", "")})
 
    # Don't alert on games that were already over the first time we saw them
    if first_time and phase == "post":
        rec["final"] = True
        return
    if first_time and phase == "in":
        rec["score"] = [my_score, opp_score]
 
    vs = "vs" if mine.get("homeAway") == "home" else "at"
    line = f'{team["short"]} {my_score}, {opp_name} {opp_score}'
 
    try:
        kickoff = datetime.fromisoformat(event["date"].replace("Z", "+00:00")).astimezone(ET)
        mins = (kickoff - now).total_seconds() / 60
    except (KeyError, ValueError):
        kickoff, mins = None, None
 
    # Pre-game alerts wait out quiet hours instead of being dropped, so an early
    # kickoff still gets its lineup and reminder at the first check after 7am.
    if team.get("lineup") and not rec.get("lineup") and not quiet and mins is not None \
            and (phase == "in" or (phase == "pre" and mins <= LINEUP_WINDOW_MIN)):
        try:
            found = lineup_for(league, event["id"], team["espn_name"])
        except Exception as e:          # a lineup hiccup shouldn't block score alerts
            print(f"lineup fetch failed: {e}")
            found = None
        if found:
            formation, starters, bench = found
            rec["lineup"] = True
            title = f'{team["short"]} XI {vs} {opp_name}' + (f" ({formation})" if formation else "")
            msg = ", ".join(starters) + ("\n\nBench: " + ", ".join(bench) if bench else "")
            push(team["channel"], title, msg, team["tag"], click=link)
 
    if phase == "pre" and team["start_alert"] and not rec.get("start") and not quiet:
        if mins is not None and 0 <= mins <= START_WINDOW_MIN:
            rec["start"] = True
            when = kickoff.strftime("%-I:%M%p").lower()
            msg = f"{vs} {opp_name}, {when}" + (f", {tv}" if tv else "")
            push(team["channel"], f'{team["short"]} start in {int(mins)} min',
                 msg, team["tag"], click=link)
 
    if phase == "in":
        if team["score_alerts"]:
            prev = rec.get("score", [0, 0])
            if [my_score, opp_score] != prev and not quiet:
                who = team["short"] if my_score > prev[0] else opp_name
                push(team["channel"], f"Goal, {who}", f"{line} ({detail})",
                     team["tag"], priority=4, click=link)
        rec["score"] = [my_score, opp_score]
        if not rec.get("close") and close_and_late(team["rule"], status, abs(my_score - opp_score)):
            rec["close"] = True
            if not quiet:
                msg = detail + (f". {tv}" if tv else "")
                push(team["channel"], line, msg, team["tag"], priority=4, click=link)
 
    if phase == "post" and not rec.get("final"):
        rec["final"] = True
        alert = {"channel": team["channel"], "title": f"Final: {line}",
                 "message": detail or "Final", "tag": team["tag"], "click": link}
        if quiet:
            state["held"].append(alert)
        else:
            push(**alert)
 
 
def prune(state, now):
    cutoff = (now - timedelta(days=7)).astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M")
    state["events"] = {k: v for k, v in state["events"].items()
                       if not v.get("date") or v["date"] >= cutoff}
 
 
def main():
    if "--test" in sys.argv:
        for t in TEAMS:
            push(t["channel"], f'{t["short"]} channel works', "Test push from Osprey", t["tag"])
        return
 
    now = now_et()
    state = load_state()
    state.setdefault("events", {})
    state.setdefault("held", [])
 
    if not is_quiet(now) and state["held"]:
        for alert in state["held"]:
            push(**alert)
        state["held"] = []
 
    for team in TEAMS:
        for league in team["leagues"]:
            try:
                data = fetch(league, now)
            except Exception as e:      # one league failing shouldn't kill the rest
                print(f"fetch failed for {league}: {e}")
                continue
            for event in data.get("events", []):
                try:
                    check_event(team, league, event, state, now)
                except Exception as e:
                    print(f'error on event {event.get("id")}: {e}')
 
    prune(state, now)
    save_state(state)
 
 
if __name__ == "__main__":
    main()
 
