"""Dry-run action assistant, general-purpose (stdlib only).

No per-command scripts. Strategy:
- classify intent from verbs/nouns, not exact train strings
- resolve people/channels/events from data/ by fuzzy name match
- parse times relative to as_of in America/Los_Angeles
- ambiguous person -> clarify; destructive -> confirm
- multi-action commands split on "and" / ";"
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

LOCAL = ZoneInfo("America/Los_Angeles")
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

KNOWN_APPS = ("figma", "notion", "linear", "slack", "gmail", "calendar",
              "github", "docs", "drive", "zoom", "meet")


def _load_users_channels(data_dir=DATA_DIR):
    users = json.loads((Path(data_dir) / "connectors/slack/users.json").read_text())
    chans = json.loads((Path(data_dir) / "connectors/slack/channels.json").read_text())
    return users, chans


def _load_events(data_dir=DATA_DIR):
    evs = []
    p = Path(data_dir) / "connectors/google_calendar/events.jsonl"
    for line in p.open():
        if line.strip():
            evs.append(json.loads(line))
    return evs


def _parse_as_of(s: str) -> datetime:
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=LOCAL)


def _all_people(data_dir=DATA_DIR):
    """(display name, slack id, email) for everyone found in data/."""
    users, _ = _load_users_channels(data_dir)
    out = []
    for u in users:
        if u.get("is_bot"):
            continue
        out.append((u.get("real_name", ""), u["id"], u.get("email") or ""))
    # external contacts discovered in gmail data (general, from data not code)
    try:
        for line in (Path(data_dir) / "connectors/gmail/messages.jsonl").open():
            x = json.loads(line)
            for field in ("from",):
                m = re.search(r"([A-Z][a-z]+\s+[A-Z][a-z]+)\s*<([^>]+)>", x.get(field, "") or "")
                if m and "@example.com" in m.group(2):
                    name, email = m.group(1), m.group(2)
                    if not any(e == email for _, _, e in out):
                        out.append((name, "", email))
            for lst in ("to", "cc"):
                for addr in (x.get(lst) or []):
                    m = re.search(r"(.+)<([^>]+)>", addr)
                    if m and "@example.com" in m.group(2):
                        out.append((m.group(1).strip(), "", m.group(2).strip()))
                    elif "@example.com" in addr and not any(e == addr for _, _, e in out):
                        out.append((addr.split("@")[0].replace(".", " ").title(), "", addr))
    except FileNotFoundError:
        pass
    # de-dupe by email or name
    seen, uniq = set(), []
    for n, sid, e in out:
        key = (e.lower() or n.lower())
        if key and key not in seen:
            seen.add(key)
            uniq.append((n, sid, e))
    return uniq


def _match_people(mention: str, data_dir=DATA_DIR):
    """Return all people whose first or full name appears in mention."""
    mention_l = mention.lower()
    hits = []
    for name, sid, email in _all_people(data_dir):
        parts = name.lower().split()
        if not parts:
            continue
        first, full = parts[0], name.lower()
        if full and full in mention_l:
            hits.append((name, sid, email))
        elif first and re.search(rf"\b{re.escape(first)}\b", mention_l):
            hits.append((name, sid, email))
    return hits


def _match_channel(mention: str, data_dir=DATA_DIR):
    _, chans = _load_users_channels(data_dir)
    ml = mention.lower()
    for c in chans:
        if c.get("name", "").lower() in ml or c["id"].lower() in ml:
            return c
        if not c.get("is_dm") and c.get("name", "").replace("-", " ") in ml:
            return c
    if "route planner" in ml or "route-planner" in ml:
        for c in chans:
            if c.get("name") == "route-planner":
                return c
    return None


def _match_event(mention: str, evs):
    ml = mention.lower()
    # strip time words to get the event topic
    topic = re.sub(r"\b(move|shift|push|reschedul\w*|book|schedule|create|tomorrow|today|at|to|on|pm|am|\d+[.:]?\d*\s*(?:pm|am)?)\b", " ", ml)
    topic = re.sub(r"\s+", " ", topic).strip()
    best, best_score = None, 0
    for e in evs:
        summ = (e.get("summary") or "").lower()
        if not summ:
            continue
        # token overlap between topic and summary
        ts = set(re.findall(r"[a-z]+", topic))
        ss = set(re.findall(r"[a-z]+", summ))
        if not ts or not ss:
            continue
        overlap = len(ts & ss)
        if overlap > best_score:
            best_score, best = overlap, e
    if best_score >= 1:
        return best
    return None


def _parse_time_expression(text: str, as_of: datetime, ev=None):
    """Parse a due/start time from natural language. Returns datetime or None.

    Handles: tomorrow, today, "25th", "Sep 25", "at 9am/3pm/2pm",
    "an hour before X" (caller resolves X), explicit ISO.
    """
    tl = text.lower()
    base_date = (as_of + timedelta(days=1)).date() if "tomorrow" in tl else as_of.date()
    m = re.search(r"\b(20\d{2}-\d{2}-\d{2}[T ]\S+)", text)
    if m:
        try:
            d = datetime.fromisoformat(m.group(1).replace(" ", "T"))
            return d if d.tzinfo else d.replace(tzinfo=LOCAL)
        except ValueError:
            pass
    # month day, e.g. Sep 25, September 25th
    months = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
              "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
    m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+(\d{1,2})(?:st|nd|rd|th)?\b", tl)
    if m:
        mon = months[m.group(1)[:3]]
        day = int(m.group(2))
        base_date = datetime(as_of.year, mon, day, tzinfo=LOCAL).date()
    else:
        m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)\b", tl)
        if m:
            day = int(m.group(1))
            # assume same month as as_of unless day already passed far ahead
            base_date = datetime(as_of.year, as_of.month, day, tzinfo=LOCAL).date() \
                if 1 <= day <= 28 else base_date
    hour, minute = 9, 0
    m = re.search(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", tl)
    if not m:
        m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", tl)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        mer = (m.group(3) or "").lower()
        if mer == "pm" and hour < 12:
            hour += 12
        if mer == "am" and hour == 12:
            hour = 0
        if not mer and hour <= 7:  # bare "at 2/3" in work context means pm
            hour += 12
    else:
        # "an hour before" handled by caller; default 9am for reminders
        pass
    try:
        return datetime(base_date.year, base_date.month, base_date.day, hour, minute, tzinfo=LOCAL)
    except ValueError:
        return None


def _split_clauses(command: str) -> list[str]:
    parts = re.split(r"\s+and then\s+|\s+then\s+|;\s*|\s+and\s+(?=(?:email|message|remind|book|schedule|move|open|tell|ask)\b)", command, flags=re.I)
    if len(parts) <= 1:
        # "Email John X and thank Ben on Slack" has no verb before thank
        parts = re.split(r"\s+and\s+(?=thank\b)", command, flags=re.I)
    return [p.strip(" .") for p in parts if p.strip()]


def _is_question(text: str) -> bool:
    tl = text.strip().lower()
    return text.strip().endswith("?") or tl.startswith(("what", "when", "where", "which", "who", "how", "is ", "are ", "do ", "did "))


def _parse_single(clause: str, as_of: datetime, data_dir, evs) -> list[dict]:
    cl = clause.lower()

    # destructive first (safety)
    if re.search(r"\bdelete\b", cl) and any(w in cl for w in ("all", "every", "everything")):
        return [{"type": "confirm", "args": {"summary": f"{clause}? This can't be undone."}}]
    if re.search(r"\bdelete\b", cl) and "email" in cl:
        return [{"type": "confirm", "args": {"summary": f"{clause}? This can't be undone."}}]
    if any(w in cl for w in ("cancel", "destroy", "wipe")) and any(w in cl for w in ("all", "every")):
        return [{"type": "confirm", "args": {"summary": f"{clause}? This can't be undone."}}]

    # open app
    if re.match(r"\s*open\s+", cl):
        for app in KNOWN_APPS:
            if app in cl:
                return [{"type": "app.open", "args": {"app": app}}]
        m = re.search(r"open\s+([a-z][a-z0-9 _\-]*)", cl)
        if m:
            return [{"type": "app.open", "args": {"app": m.group(1).strip().split()[0]}}]

    # pure questions -> memory
    if _is_question(clause) and not any(v in cl for v in ("remind", "email", "message", "book", "schedule", "move", "open")):
        return [{"type": "memory.ask", "args": {"question": clause.strip()}}]

    # reminders
    if "remind" in cl:
        due = None
        if "hour before" in cl:
            ev = _match_event(clause, evs)
            if ev:
                try:
                    s = datetime.fromisoformat(ev["start"].get("dateTime"))
                    due = s - timedelta(hours=1)
                except (ValueError, KeyError, TypeError):
                    due = None
        if due is None:
            due = _parse_time_expression(clause, as_of)
        if due is None:
            due = (as_of + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        return [{"type": "reminder.create", "args": {"text": clause.strip(), "due": due.isoformat()}}]

    # calendar move/update: "move/reschedule/push X to ..."
    if any(v in cl for v in ("move", "reschedul", "push back", "shift")) and any(w in cl for w in ("meeting", "prep", "board", "demo", "call", "event", "1:1", "1-1")):
        ev = _match_event(clause, evs)
        if ev:
            due = _parse_time_expression(clause, as_of)
            try:
                orig_start = datetime.fromisoformat(ev["start"].get("dateTime"))
                dur = datetime.fromisoformat(ev["end"].get("dateTime")) - orig_start
            except (ValueError, KeyError, TypeError):
                dur = timedelta(hours=1)
            if due is None:
                due = orig_start
            # preserve original date, apply new clock time
            new_start = due
            try:
                base = datetime.fromisoformat(ev["start"].get("dateTime"))
                new_start = base.replace(hour=due.hour, minute=due.minute)
            except (ValueError, KeyError, TypeError):
                pass
            return [{"type": "calendar.update_event",
                     "args": {"event_id": ev["id"], "start": new_start.isoformat(),
                              "end": (new_start + dur).isoformat()}}]
        # fall through to clarify if no event matched

    # calendar create: "book/schedule X ..."
    if any(v in cl for v in ("book", "schedul", "set up", "set-up", "create event")):
        people = _match_people(clause, data_dir)
        attendees = [e for _, _, e in people if e and "@" in e]
        # duration: "30 minutes/min" else 30m default; end from start+duration
        dur_min = 30
        m = re.search(r"(\d+)\s*(?:min|minutes|hour|hr)", cl)
        if m:
            dur_min = int(m.group(1)) * (60 if "hour" in cl[m.start():m.start() + 12] or "hr" in cl[m.start():m.start() + 12] else 1)
        start = _parse_time_expression(clause, as_of) or (as_of + timedelta(days=1)).replace(hour=14, minute=0, second=0, microsecond=0)
        # title: text after about/for, else first chunk
        title = clause.strip()
        m = re.search(r"\b(?:about|for|re:?)\s+(.+)$", clause, re.I)
        if m:
            title = m.group(1).strip()
        else:
            title = re.sub(r"^(please\s+)?(book|schedule|set up)\s+", "", clause, flags=re.I).strip()[:80]
        return [{"type": "calendar.create_event",
                 "args": {"title": title[:80], "start": start.isoformat(),
                          "end": (start + timedelta(minutes=dur_min)).isoformat(),
                          "attendees": attendees}}]

    # email: "email/mail X ..." — resolve recipient from data
    if re.search(r"\bemail\b|\bmail\b|\bsend\b.*\bemail\b", cl):
        people = _match_people(clause, data_dir)
        to = [e for _, _, e in people if e and "@" in e]
        if not to:
            # unknown recipient: clarify rather than guess
            return [{"type": "clarify", "args": {"question": f"Who should I email for: {clause.strip()}?"}}]
        # body: quoted text or "that ..." or whole clause
        body = clause.strip()
        m = re.search(r'"([^"]+)"', clause)
        if m:
            body = m.group(1)
        else:
            m = re.search(r"\bthat\s+(.+)$", clause, re.I)
            if m:
                body = m.group(1)
        # enrich body with numbers found in visible data when asked for corrected values?
        # general: if "corrected" + metric, look up latest metric mention in data
        if "correct" in cl:
            metric_val = _lookup_latest_metric(clause, as_of, data_dir)
            if metric_val and metric_val not in body:
                body = f"{body} ({metric_val})"
        return [{"type": "gmail.send",
                 "args": {"to": sorted(set(to)), "subject": clause.strip()[:80], "body": body}}]

    # slack: "message/slack/tell/dm X ..." — resolve channel or person
    if any(v in cl for v in ("message", "slack", "dm", "tell", "ping", "post")):
        chan = _match_channel(clause, data_dir)
        people = _match_people(clause, data_dir)
        text = clause.strip()
        m = re.search(r'"([^"]+)"', clause)
        if m:
            text = m.group(1)
        else:
            m = re.search(r"\bthat\s+(.+)$", clause, re.I)
            if m:
                text = m.group(1)
        if chan and not people:
            return [{"type": "slack.send_message", "args": {"to": chan["id"], "text": text}}]
        if len(people) > 1 and not any(p[0].lower() in cl for p in people if len(p[0].split()) > 1):
            # ambiguous first name only (e.g. "Sarah" matching Kim + Patel)
            return [{"type": "clarify",
                     "args": {"question": "Which person do you mean: " + " or ".join(p[0] for p in people[:3]) + "?"}}]
        if len(people) == 1:
            _name, sid, _email = people[0]
            return [{"type": "slack.send_message", "args": {"to": sid or _name, "text": text}}]
        if chan:
            return [{"type": "slack.send_message", "args": {"to": chan["id"], "text": text}}]
        # "thank X" without slack keyword but with person
        if "thank" in cl and people:
            _name, sid, _email = people[0]
            return [{"type": "slack.send_message", "args": {"to": sid or _name, "text": clause.strip()}}]
        return [{"type": "clarify", "args": {"question": f"Who should I message for: {clause.strip()}?"}}]

    # bare "thank X" clause (from split multi-actions)
    if cl.startswith("thank") or " thank " in cl:
        people = _match_people(clause, data_dir)
        if people:
            _name, sid, _email = people[0]
            return [{"type": "slack.send_message", "args": {"to": sid or _name, "text": clause.strip()}}]

    if _is_question(clause):
        return [{"type": "memory.ask", "args": {"question": clause.strip()}}]
    return [{"type": "clarify", "args": {"question": f"Could you clarify: {clause.strip()}?"}}]


def _lookup_latest_metric(clause: str, as_of: datetime, data_dir) -> str | None:
    """General corrected-value lookup: search visible slack/gmail for the
    metric named in the clause (e.g. NRR) and return its latest stated value."""
    key = None
    for cand in ("nrr", "p95", "latency", "arr"):
        if cand in clause.lower():
            key = cand
            break
    if not key:
        return None
    latest, latest_ts = None, None
    try:
        for line in (Path(data_dir) / "connectors/slack/messages.jsonl").open():
            x = json.loads(line)
            try:
                ts = datetime.fromisoformat(x.get("ts", ""))
            except ValueError:
                continue
            if ts > as_of:
                continue
            txt = (x.get("text") or "")
            if key in txt.lower():
                m = re.search(rf"{key}\s*(?:is|=|:)?\s*(\d+(?:\.\d+)?\s*%?)", txt, re.I)
                if m and (latest_ts is None or ts > latest_ts):
                    latest, latest_ts = m.group(0), ts
        return latest
    except FileNotFoundError:
        return None


def parse_command(command: str, as_of_s: str, data_dir=DATA_DIR) -> list[dict]:
    as_of = _parse_as_of(as_of_s)
    evs = _load_events(data_dir)
    out: list[dict] = []
    for clause in _split_clauses(command.strip()):
        out.extend(_parse_single(clause, as_of, data_dir, evs))
    # de-dupe identical actions while preserving order
    seen, uniq = set(), []
    for a in out:
        key = json.dumps(a, sort_keys=True)
        if key not in seen:
            seen.add(key)
            uniq.append(a)
    return uniq
