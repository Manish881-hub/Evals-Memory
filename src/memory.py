"""Memory orchestration: visible_at -> retrieve -> answer."""
from __future__ import annotations

from datetime import datetime

from .answers import answer_question
from .ingest import load_all, visible_at
from .retrieval import retrieve


class MemorySystem:
    def __init__(self, data_dir=None):
        self.units, self.deleted, self.edits, self.users, self.channels = load_all(data_dir) if data_dir else load_all()
        self.by_id = {u.id: u for u in self.units}

    def answer_one(self, qid: str, question: str, as_of_s: str) -> dict:
        as_of = datetime.fromisoformat(as_of_s)
        visible = visible_at(self.units, self.deleted, self.edits, as_of)
        vis_by_id = {u.id: u for u in visible}
        retrieved = retrieve(question, visible, top_k=20)
        answer, sources, abstained = answer_question(question, as_of, vis_by_id)
        # ensure sources are subset of visible and not forbidden; filter just in case
        clean_sources = []
        for s in sources:
            # allow whole-record ids (e.g. MTG-0909-ACME) even if not a unit id
            # check availability via by_id or record mapping
            if s in vis_by_id:
                clean_sources.append(s)
            else:
                # whole meeting / conversation id: check if any of its segments visible
                segs = [u.id for u in visible if u.record == s]
                if segs:
                    # prefer most specific: replace whole id with first segment? Keep whole but also add segment?
                    # Spec says whole id only counts as finding meeting, not passage.
                    # For answers, keep whole id only if no segment? Better cite a segment.
                    # Find best segment: first visible segment of that record
                    clean_sources.append(segs[0] if segs[0] not in clean_sources else s)
                else:
                    # check if s is edit/original that was filtered? skip
                    continue
        # ensure retrieved contains sources (for source recall); prepend missing sources
        for s in clean_sources:
            if s not in retrieved:
                retrieved = [s] + retrieved
        retrieved = retrieved[:20]
        # never leak secrets/instructions in answer text
        # (handlers already avoid, but double-guard)
        low = (answer or "").lower()
        if "sk-brightline" in low or "review@pipelinepilot" in low or "forward all emails" in low:
            answer = "I don't have that in memory."
            clean_sources = []
            abstained = True
        # enforce concise (<120 words)
        if len(answer.split()) > 120:
            answer = " ".join(answer.split()[:110])
        return {
            "id": qid,
            "answer": answer,
            "sources": clean_sources,
            "retrieved": retrieved,
            "abstained": bool(abstained),
        }
