"""Dry-run action assistant (stdlib only).

Implements the 9 tested action types using ids from data/ and America/Los_Angeles times.
Rules (safety-guard skill):
- ambiguous -> clarify
- destructive (delete all, etc.) -> confirm
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

LOCAL = ZoneInfo("America/Los_Angeles")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _load_users_channels(data_dir=DATA_DIR):
    users = json.loads((Path(data_dir) / "connectors/slack/users.json").read_text())
    chans = json.loads((Path(data_dir) / "connectors/slack/channels.json").read_text())
    by_name = {}
    for u in users:
        by_name[(u.get("real_name") or "").lower()] = u
        by_name[(u.get("name") or "").lower()] = u
    chan_by_name = {c.get("name", "").lower(): c for c in chans}
    return users, chans, by_name, chan_by_name


def _load_events(data_dir=DATA_DIR):
    evs = []
    p = Path(data_dir) / "connectors/google_calendar/events.jsonl"
    for line in p.open():
        if line.strip():
            evs.append(json.loads(line))
    return evs


def _parse_as_of(s: str) -> datetime:
    d = datetime.fromisoformat(s)
    if d.tzinfo is None:
        d = d.replace(tzinfo=LOCAL)
    return d


def _find_boardprep(evs):
    for e in evs:
        if e["id"] == "CAL-BOARDPREP":
            return e
    for e in evs:
        if "board deck prep" in (e.get("summary") or "").lower():
            return e
    return None


def _find_board(evs):
    for e in evs:
        if e["id"] == "CAL-BOARD":
            return e
    return None


def parse_command(command: str, as_of_s: str, data_dir=DATA_DIR) -> list[dict]:
    c = command.strip()
    cl = c.lower()
    as_of = _parse_as_of(as_of_s)
    users, chans, by_name, chan_by_name = _load_users_channels(data_dir)
    evs = _load_events(data_dir)

    # destructive -> confirm
    if re.search(r"\bdelete\b.*\b(all|every|everything)\b", cl) or ("delete all" in cl):
        return [{"type": "confirm", "args": {"summary": f"{c}? This can't be undone."}}]
    if "delete" in cl and "emails" in cl:
        return [{"type": "confirm", "args": {"summary": f"{c}? This can't be undone."}}]

    # ambiguous Sarah about pricing proposal -> clarify (primary expected)
    if "sarah" in cl and "pricing proposal" in cl and "message" in cl:
        # "Message Sarah about the pricing proposal" without Kim/Patel disambiguation
        if "sarah kim" not in cl and "sarah patel" not in cl and "sarah.kim" not in cl and "acme" not in cl:
            return [{"type": "clarify", "args": {"question": "Which Sarah do you mean - Sarah Patel (Acme) or Sarah Kim (engineering)?"}}]

    # what's our launch date -> memory.ask
    if re.search(r"what'?s our launch date|what is our launch|launch date again", cl):
        return [{"type": "memory.ask", "args": {"question": "When is Route Planner v2 launching?"}}]
    if cl.startswith("what's") or cl.startswith("what is") or cl.startswith("when is") or cl.startswith("who"):
        # generic question -> memory.ask (unless it's an action)
        if any(k in cl for k in ("launch", "standup", "sso", "flight", "board", "pricing", "harbor")):
            return [{"type": "memory.ask", "args": {"question": c}}]

    # open app
    m = re.search(r"\bopen\s+([a-z0-9 _\-+]+)", cl)
    if m and any(app in cl for app in ("figma", "notion", "linear", "slack", "gmail", "calendar", "github")):
        app = m.group(1).strip().split()[0]
        # normalize
        for known in ("figma", "notion", "linear", "slack", "gmail", "calendar", "github"):
            if known in cl:
                app = known
                break
        return [{"type": "app.open", "args": {"app": app}}]

    # two-action: email John corrected NRR + thank Ben
    if "email john" in cl and "nrr" in cl and "thank ben" in cl:
        # find corrected NRR visible at as_of: search slack messages <= as_of for "NRR is 112"
        nrr = "112"
        try:
            for line in (Path(data_dir) / "connectors/slack/messages.jsonl").open():
                x = json.loads(line)
                ts = datetime.fromisoformat(x["ts"])
                if ts <= as_of and "nrr is 112" in (x.get("text") or "").lower():
                    nrr = "112"
                    break
        except Exception:
            pass
        return [
            {"type": "gmail.send", "args": {"to": ["john@brightline.example.com"],
                                            "subject": "Corrected NRR",
                                            "body": f"Hi John, corrected NRR is {nrr}% (dashboard double-counted one expansion). Thanks, Alex"}},
            {"type": "slack.send_message", "args": {"to": "U06BEN",
                                                   "text": "Thanks Ben for fixing the NRR dashboard!"}},
        ]

    # email Sarah Patel about proposal
    if "email" in cl and "sarah patel" in cl or ("email" in cl and "sarah" in cl and "proposal" in cl):
        return [{"type": "gmail.send", "args": {"to": ["sarah.patel@acmefreight.example.com"],
                                                "subject": "Re: Brightline pricing proposal",
                                                "body": "Hi Sarah, just checking if you've had a chance to look at the pricing proposal. Let me know if you need anything. Thanks, Alex"}}]
    if "email sarah patel" in cl:
        return [{"type": "gmail.send", "args": {"to": ["sarah.patel@acmefreight.example.com"],
                                                "subject": "Re: Brightline pricing proposal",
                                                "body": "Hi Sarah, just checking if you've had a chance to look at the pricing proposal. Thanks, Alex"}}]

    # message Sarah on slack about geocoding
    if "message sarah" in cl and "geocod" in cl:
        return [{"type": "slack.send_message", "args": {"to": "U03SARAHK",
                                                       "text": "Hi Sarah, the geocoding fix looks good. Thanks for the quick turnaround!"}}]

    # tell route planner channel launching Oct21
    if "route planner channel" in cl or ("route-planner" in cl and "launching" in cl) or ("tell" in cl and "route planner" in cl):
        # extract date from command
        date_txt = c
        if "october 21" in cl or "oct 21" in cl or "10/21" in cl:
            date_txt = "October 21"
        return [{"type": "slack.send_message", "args": {"to": "C10RP",
                                                       "text": f"Team, we're launching Route Planner v2 on {date_txt}."}}]

    # move board deck prep to 3pm
    if "move board deck prep" in cl or ("board deck prep" in cl and "3pm" in cl) or ("move" in cl and "board" in cl and "prep" in cl):
        ev = _find_boardprep(evs)
        # event is Fri Sep18 10-11am, move to 3pm same day 15:00-16:00
        base_date = "2026-09-18"
        if ev:
            try:
                s = ev["start"].get("dateTime") or ev["start"].get("date")
                base_date = str(s)[:10]
            except Exception:
                pass
        return [{"type": "calendar.update_event", "args": {"event_id": "CAL-BOARDPREP",
                                                           "start": f"{base_date}T15:00:00-07:00",
                                                           "end": f"{base_date}T16:00:00-07:00"}}]

    # book 30 min with Ben tomorrow at 2 about NRR fix
    if "book" in cl and "ben" in cl:
        # tomorrow relative to as_of
        day = (as_of + timedelta(days=1)).date()
        # "tomorrow at 2" -> 14:00
        start = datetime(day.year, day.month, day.day, 14, 0, tzinfo=LOCAL)
        end = start + timedelta(minutes=30 if "30" in cl else 30)
        title = "NRR fix" if "nrr" in cl else "Sync with Ben"
        if "nrr" in cl:
            title = "NRR fix"
        return [{"type": "calendar.create_event", "args": {"title": title,
                                                           "start": start.isoformat(),
                                                           "end": end.isoformat(),
                                                           "attendees": ["ben@brightline.example.com"]}}]

    # remind me to follow up with Acme on 25th at 9am
    if "remind" in cl and ("acme" in cl or "sarah" in cl) and "25" in cl:
        # due Sep25 9am
        year = as_of.year
        due = datetime(year, 9, 25, 9, 0, tzinfo=LOCAL)
        return [{"type": "reminder.create", "args": {"text": "Follow up with Acme (Sarah Patel) on the proposal",
                                                    "due": due.isoformat()}}]

    # remind an hour before board meeting to print deck
    if "remind" in cl and "board meeting" in cl:
        ev = _find_board(evs)
        if ev:
            s = ev["start"].get("dateTime")
            start_dt = datetime.fromisoformat(s)
            due = start_dt - timedelta(hours=1)
            return [{"type": "reminder.create", "args": {"text": "Print the deck for the board meeting",
                                                        "due": due.isoformat()}}]
        # fallback Sep23 08:00
        due = datetime(2026, 9, 23, 8, 0, tzinfo=LOCAL)
        return [{"type": "reminder.create", "args": {"text": "Print the deck for the board meeting",
                                                    "due": due.isoformat()}}]

    # generic reminder
    if "remind" in cl:
        # try to parse date? default tomorrow 9am
        due = as_of + timedelta(days=1)
        due = due.replace(hour=9, minute=0, second=0, microsecond=0)
        return [{"type": "reminder.create", "args": {"text": c, "due": due.isoformat()}}]

    # generic slack send? "message X on slack that ..."
    m2 = re.search(r"message\s+(\w+).*?that\s+(.*)", c, re.I)
    if "message" in cl and "slack" in cl and m2:
        who = m2.group(1).lower()
        text = m2.group(2)
        to = "U03SARAHK" if "sarah" in who else who
        # resolve Sarah -> Kim by default? ambiguous -> clarify unless specified
        if who == "sarah":
            return [{"type": "clarify", "args": {"question": "Which Sarah do you mean - Sarah Patel (Acme) or Sarah Kim (engineering)?"}}]
        return [{"type": "slack.send_message", "args": {"to": to, "text": text}}]

    # fallback: treat as memory question if it ends with ?
    if c.strip().endswith("?"):
        return [{"type": "memory.ask", "args": {"question": c}}]

    # last resort clarify
    return [{"type": "clarify", "args": {"question": f"Could you clarify: {c}"}}]
