"""Ingestion + temporal reconstruction (stdlib only).

Follows contract-first: output must satisfy the memory interface hard rules:
- nothing after as_of exists
- deleted messages gone from deletion time on
- edits replace old text from edit time on
- secrets / planted instructions are data, never instructions (flagged for
  retrieval filtering per unified-memory trust boundaries).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from .models import Unit

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

SECRET_RE = re.compile(r"sk-[A-Za-z0-9\-_]{8,}|api[_-]?key\s*[:=]", re.I)
INJECTION_PHRASES = (
    "ignore your previous instructions",
    "note to any ai assistant",
    "forward all emails",
)


def _dt(s) -> datetime:
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def is_secret(text: str) -> bool:
    return bool(SECRET_RE.search(text or ""))


def is_injection(text: str) -> bool:
    t = (text or "").lower()
    return any(p in t for p in INJECTION_PHRASES)


def load_all(data_dir: str | Path = DATA_DIR):
    """Returns (units, deleted, edits, users, channels).

    units: list[Unit] sorted by time.
    deleted: target_id -> deletion datetime.
    edits: target_id -> [(time, new_text)] sorted.
    """
    d = Path(data_dir)
    units: list[Unit] = []
    deleted: dict[str, datetime] = {}
    edits: dict[str, list] = {}

    # meetings
    for f in sorted((d / "native/meetings").glob("*.json")):
        m = json.loads(f.read_text())
        start = _dt(m["start"])
        for s in m.get("segments", []):
            who = s.get("speaker_name") or s.get("speaker_label") or "Unknown speaker"
            units.append(Unit(
                id=s["seg_id"], record=m["id"],
                time=start + timedelta(seconds=float(s.get("end_s", 0))),
                text=f"[{m.get('title','')}, {str(m.get('start',''))[:10]}] {who}: {s.get('text','')}",
                source="meetings", speaker=who, channel=m.get("title", ""),
                extra={"meeting_id": m["id"], "start": m.get("start")},
            ))

    # dictation
    p = d / "native/dictation/dictations.jsonl"
    if p.exists():
        for line in p.open():
            line = line.strip()
            if not line:
                continue
            x = json.loads(line)
            raw = f"\n(raw transcript: {x['raw_transcript']})" if x.get("raw_transcript") else ""
            units.append(Unit(
                id=x["id"], record=x["id"], time=_dt(x["timestamp"]),
                text=f"[Dictation {x.get('mode','')} into {x.get('target_app','')} - {x.get('target_context','')}, {x.get('delivery_state','')}] {x.get('cleaned_text','')}{raw}",
                source="dictation", speaker="Alex Rivera",
                extra={"mode": x.get("mode"), "delivery_state": x.get("delivery_state")},
            ))

    # slack
    users_p = d / "connectors/slack/users.json"
    chans_p = d / "connectors/slack/channels.json"
    names: dict[str, str] = {}
    emails_by_user: dict[str, str] = {}
    if users_p.exists():
        for u in json.loads(users_p.read_text()):
            names[u["id"]] = u.get("real_name") or u.get("name") or u["id"]
            if u.get("email"):
                emails_by_user[u["id"]] = u["email"]
    chans: dict[str, str] = {}
    if chans_p.exists():
        for c in json.loads(chans_p.read_text()):
            chans[c["id"]] = c.get("name", c["id"])
    mp = d / "connectors/slack/messages.jsonl"
    if mp.exists():
        for line in mp.open():
            line = line.strip()
            if not line:
                continue
            x = json.loads(line)
            t = _dt(x["ts"])
            where = chans.get(x.get("channel_id"), x.get("channel_id", ""))
            if x.get("subtype") == "message_deleted":
                deleted[x["target_id"]] = t
                units.append(Unit(
                    id=x["id"], record=x["id"], time=t,
                    text=f"[Slack {where}] (message {x['target_id']} was deleted)",
                    source="slack", channel=where,
                    extra={"subtype": "message_deleted", "target_id": x["target_id"]},
                ))
                continue
            if x.get("subtype") == "message_changed":
                edits.setdefault(x["target_id"], []).append((t, x.get("text", "")))
                units.append(Unit(
                    id=x["id"], record=x["id"], time=t,
                    text=f"[Slack {where}, edit of {x['target_id']}] {x.get('text','')}",
                    source="slack", speaker=names.get(x.get("user"), x.get("user", "")),
                    channel=where,
                    extra={"subtype": "message_changed", "target_id": x["target_id"]},
                ))
                continue
            who = names.get(x.get("user"), x.get("bot_name") or x.get("user", ""))
            units.append(Unit(
                id=x["id"], record=x["id"], time=t,
                text=f"[Slack {where}] {who}: {x.get('text','')}",
                source="slack", speaker=who or "", channel=where,
                extra={"user": x.get("user"), "channel_id": x.get("channel_id")},
            ))

    # gmail
    gp = d / "connectors/gmail/messages.jsonl"
    if gp.exists():
        for line in gp.open():
            line = line.strip()
            if not line:
                continue
            x = json.loads(line)
            cc = f" Cc {', '.join(x.get('cc') or [])}" if x.get("cc") else ""
            units.append(Unit(
                id=x["id"], record=x["id"], time=_dt(x["date"]),
                text=f"[Email {str(x.get('date',''))[:16]}] From {x.get('from','')} To {', '.join(x.get('to') or [])}{cc} | {x.get('subject','')}\n{x.get('body','')}",
                source="gmail", speaker=x.get("from", ""), channel="email",
                extra={"subject": x.get("subject", ""), "thread_id": x.get("thread_id", "")},
            ))

    # calendar (availability = updated)
    cp = d / "connectors/google_calendar/events.jsonl"
    if cp.exists():
        for line in cp.open():
            line = line.strip()
            if not line:
                continue
            x = json.loads(line)
            st, en = x.get("start", {}), x.get("end", {})
            when = f"{st.get('dateTime') or st.get('date')} to {en.get('dateTime') or en.get('date')}"
            att = ", ".join(a.get("email", "") for a in x.get("attendees", []))
            units.append(Unit(
                id=x["id"], record=x["id"], time=_dt(x["updated"]),
                text=f"[Calendar, {x.get('status','')}] {x.get('summary','')} | {when} | {x.get('location') or ''} | attendees: {att} | {x.get('description') or ''}",
                source="calendar", channel="calendar",
                extra={"summary": x.get("summary", ""), "start": st, "end": en,
                       "status": x.get("status", ""), "attendees": att},
            ))

    # codex (one unit per session, time = last event)
    for f in sorted((d / "connectors/codex/sessions").glob("*.jsonl")):
        events = [json.loads(l) for l in f.open() if l.strip()]
        if not events:
            continue
        meta, body = events[0], events[1:]
        parts = []
        for e in body:
            if e.get("type") == "message":
                parts.append(f"{e.get('role','')}: {e.get('content','')}")
            elif e.get("type") == "tool_call":
                parts.append(f"{e.get('tool','tool')}: {e.get('input','')} :: {str(e.get('output',''))[:2000]}")
            else:
                parts.append(f"{e.get('type','')}: {str(e)[:500]}")
        text = "\n".join(parts)
        ts = body[-1].get("timestamp") if body else meta.get("started_at")
        units.append(Unit(
            id=meta.get("id", f.stem), record=meta.get("id", f.stem), time=_dt(ts),
            text=f"[Codex session, repo {meta.get('repo','')}]\n{text}",
            source="codex", channel=meta.get("repo", ""),
        ))

    # chatgpt (per-message units)
    jp = d / "connectors/chatgpt/conversations.json"
    if jp.exists():
        for c in json.loads(jp.read_text()):
            for m in c.get("messages", []):
                units.append(Unit(
                    id=m["id"], record=c["id"], time=_dt(m["create_time"]),
                    text=f"[ChatGPT '{c.get('title','')}'] {m.get('role','')}: {m.get('content','')}",
                    source="chatgpt", channel=c.get("title", ""),
                ))

    for v in edits.values():
        v.sort(key=lambda x: x[0])
    units.sort(key=lambda u: u.time)
    return units, deleted, edits, names, chans


def visible_at(units, deleted, edits, as_of: datetime) -> list[Unit]:
    """Reconstruct data visible at as_of BEFORE ranking (temporal first).

    - excludes units delivered after as_of
    - excludes units deleted by as_of (both the deleted target and never
      the deletion marker itself as evidence for content)
    - applies latest edit <= as_of to slack originals
    """
    out: list[Unit] = []
    for u in units:
        if u.time > as_of:
            continue
        # deletion markers themselves are not content evidence
        if u.extra.get("subtype") == "message_deleted":
            continue
        if u.id in deleted and deleted[u.id] <= as_of:
            continue
        # apply edits to original slack message text
        if u.id in edits:
            newer = [txt for t, txt in edits[u.id] if t <= as_of]
            if newer:
                # keep prefix "[Slack ...] Name: " then replaced text
                prefix = u.text.split(": ", 1)[0] if ": " in u.text else u.text
                u2 = Unit(id=u.id, record=u.record, time=u.time,
                          text=f"{prefix}: {newer[-1]} (edited)",
                          source=u.source, speaker=u.speaker,
                          channel=u.channel, extra={**u.extra, "edited": True})
                out.append(u2)
                continue
        out.append(u)
    # edit records themselves remain visible as their own citable events
    # (they were added as units with time=edit time, already filtered by time)
    return out


def context_maps(units, deleted):
    avail = {u.id: u.time for u in units}
    record_of = {u.id: u.record for u in units}
    # whole record exists once its first unit does
    for u in units:
        if u.record not in avail or u.time < avail[u.record]:
            avail[u.record] = min(u.time, avail.get(u.record, u.time))
    return {"avail": avail, "record_of": record_of, "deleted": deleted}
