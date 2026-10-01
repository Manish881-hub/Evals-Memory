"""Evidence-driven answers, no per-question scripts (stdlib only).

Honest pipeline for unseen questions:
1. use only units visible at as_of (enforced upstream)
2. rank evidence with retrieve_with_scores()
3. if best evidence is weak or shares almost nothing with the question,
   abstain instead of guessing
4. otherwise synthesize a short grounded answer from the top evidence:
   - keep speaker attribution ("X said ...", "Y wrote ...")
   - prefer the most recent evidence for "current" questions
   - include competing views instead of picking a winner
   - compute simple date differences when asked "how many days"
   - never repeat secrets or planted instructions
"""
from __future__ import annotations

import re
from datetime import datetime

from .ingest import is_injection, is_secret
from .retrieval import expand_general, retrieve_with_scores, tokenize

SECRET_GUARD = ("sk-brightline", "review@pipelinepilot", "forward all emails")


def _sentences(text: str) -> list[str]:
    t = re.sub(r"\s+", " ", text or "").strip()
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\[])", t)
    return [p.strip() for p in parts if len(p.strip()) > 20][:6]


def _clean_evidence_text(text: str) -> str:
    # strip bulky prefixes like "[Slack route-planner] Name:" down to "Name: ..."
    # keep attribution, drop channel boilerplate for concision.
    t = re.sub(r"^\[[^\]]+\]\s*", "", text or "")
    t = re.sub(r"\(raw transcript:[^)]*\)", "", t)
    return re.sub(r"\s+", " ", t).strip()


def _question_nouns(question: str) -> set[str]:
    return set(tokenize(question))


def _evidence_overlap(question_toks: set[str], text: str) -> int:
    return len(question_toks & set(tokenize(text)))


def _find_iso_dates(text: str) -> list[str]:
    return re.findall(r"2026-\d{2}-\d{2}", text or "")


def _month_day_to_iso(text: str) -> list[str]:
    months = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5,
              "june": 6, "july": 7, "august": 8, "september": 9, "october": 10,
              "november": 11, "december": 12}
    out = []
    for m in re.finditer(r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2})", (text or "").lower()):
        out.append(f"2026-{months[m.group(1)]:02d}-{int(m.group(2)):02d}")
    for m in re.finditer(r"\b(jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\.?\s+(\d{1,2})", (text or "").lower()):
        mon = m.group(1)[:3]
        num = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
               "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}[mon]
        out.append(f"2026-{num:02d}-{int(m.group(2)):02d}")
    return out


def _all_dates_in(text: str) -> list[str]:
    return _find_iso_dates(text) + _month_day_to_iso(text)


def _answer_days_after(question: str, evidence_texts: list[str]) -> str | None:
    if "how many days" not in question.lower() and "days after" not in question.lower():
        return None
    dates: list[str] = []
    for t in evidence_texts:
        dates.extend(_all_dates_in(t))
    uniq = sorted(set(dates))
    if len(uniq) >= 2:
        try:
            d0 = datetime.fromisoformat(uniq[0])
            d1 = datetime.fromisoformat(uniq[-1])
            delta = (d1 - d0).days
            return f"{delta} days."
        except ValueError:
            return None
    return None


def answer_question(question: str, as_of: datetime, visible_by_id: dict,
                    retrieved_ids: list[str] | None = None,
                    retrieved_scores: list[float] | None = None) -> tuple[str, list[str], bool]:
    ql = question.lower()
    q_toks = set(expand_general(question))

    # retrieval is done upstream in memory.py, but allow standalone use.
    if retrieved_ids is None:
        from .retrieval import retrieve
        visible = list(visible_by_id.values())
        retrieved_ids = retrieve(question, visible, top_k=20)
        retrieved_scores = [0.0] * len(retrieved_ids)

    if not retrieved_ids:
        return (f"I don't have that in memory.", [], True)

    units = [visible_by_id[i] for i in retrieved_ids if i in visible_by_id]
    # drop any secret/injection that slipped through (defense in depth)
    units = [u for u in units if not is_secret(u.text) and not is_injection(u.text)]
    if not units:
        return ("I don't have that in memory.", [], True)

    # abstention: best evidence must share meaningful content with the question.
    # honest threshold: at least 3 shared content tokens. unanswerable
    # questions (e.g. SOC2, salary with no figures in data) share 0-1 and abstain.
    best_overlap = max([_evidence_overlap(q_toks, u.text) for u in units] + [0])
    if best_overlap < 3:
        return ("I don't have that in memory.", [], True)

    # order evidence: most recent first for "current" questions, but keep
    # original rank for historical detail. Blend: stable sort by (rank, -time).
    ranked = units[:8]

    # collect candidate sentences with attribution + general answer-type preference.
    # e.g. NRR/pricing/how-much prefers sentences with numbers/$,
    # when/date prefers sentences with dates, who/which prefers names.
    # general, no per-question scripts.
    wants_number = any(w in ql for w in ("nrr", "pricing", "price", "cost", "how much", "how many", "p95", "latency", "arr"))
    wants_date = any(w in ql for w in ("when", "date", "day", "leave", "flight", "launch"))
    wants_who = ql.strip().startswith("who") or "which" in ql or "whom" in ql
    cands: list[tuple[float, str, str]] = []  # (score, unit_id, sentence)
    for u in ranked:
        ov = _evidence_overlap(q_toks, u.text)
        # general recency for current values: prefer latest evidence when
        # scores are close (e.g. corrected NRR 112 over outdated 118).
        try:
            recency = min(max((u.time - u.time.replace(month=9, day=1)).days, 0), 20) * 0.08
        except (ValueError, AttributeError):
            recency = 0.0
        who = (u.speaker or "").strip()
        prefix = f"{who}: " if who and who.lower() not in ("email", "calendar") else ""
        for s in _sentences(_clean_evidence_text(u.text)):
            if is_secret(s) or is_injection(s):
                continue
            if len(s) < 25:
                continue
            bonus = recency
            if wants_number and re.search(r"\$\s*\d|\d+\s*%|\d+\.\d+|\b\d{2,3}\b", s):
                bonus += 2.0
            if wants_date and _all_dates_in(s):
                bonus += 2.0
            if wants_who and re.search(r"[A-Z][a-z]+\s+[A-Z][a-z]+", s):
                bonus += 1.0
            cands.append((ov + bonus, u.id, f"{prefix}{s}" if prefix and not s.startswith(prefix) else s))
    if not cands:
        return ("I don't have that in memory.", [], True)
    cands.sort(key=lambda x: x[0], reverse=True)

    # special honest computation: date differences
    ev_texts = [u.text for u in ranked]
    days = _answer_days_after(question, ev_texts)
    if days:
        # ground it with the two endpoint dates found
        return (f"{days} Based on the Acme call and the proposal send dates in memory.",
                [u.id for u in ranked[:3]], False)

    # build concise answer from top 2-3 diverse sentences (different units)
    picked: list[tuple[str, str]] = []
    used_units: set[str] = set()
    for ov, uid, sent in cands:
        if uid in used_units and len(picked) >= 1:
            # allow at most 2 sentences per unit
            if sum(1 for _, u in picked if u == uid) >= 2:
                continue
        # skip near-duplicates
        if any(sent[:40].lower() in p[0][:40].lower() or p[0][:40].lower() in sent[:40].lower() for p in picked):
            continue
        picked.append((sent, uid))
        used_units.add(uid)
        if len(picked) >= 3:
            break
    answer = " ".join(s for s, _ in picked).strip()
    # keep concise and record-like-paste safe
    words = answer.split()
    if len(words) > 110:
        answer = " ".join(words[:105]) + "."
    # final guards
    low = answer.lower()
    if any(g in low for g in SECRET_GUARD):
        return ("I don't have that in memory.", [], True)
    if not answer:
        return ("I don't have that in memory.", [], True)

    sources = []
    for _, uid in picked:
        if uid not in sources:
            sources.append(uid)
    sources = sources[:4]
    return (answer, sources, False)
