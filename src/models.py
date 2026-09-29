"""Normalized unit model. See data/README.md + BRIEF.md.

Domain glossary (domain-modeling skill):
- Unit: smallest citable piece (meeting segment, dictation, slack msg/edit,
  email, calendar event, codex session, chatgpt message).
- Record: container of units (meeting, chatgpt conversation). For most sources
  unit id == record id.
- Delivery time: moment a unit becomes visible (meeting segment: start+end_s,
  slack/email/dictation: timestamp, calendar: updated, codex: last event,
  chatgpt: create_time).
- as_of: query moment. Nothing after as_of exists.
- Edit: replaces target text from edit time on. Deletion: removes target
  from deletion time on.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Unit:
    id: str
    record: str
    time: datetime
    text: str
    source: str = ""  # meetings|dictation|slack|gmail|calendar|codex|chatgpt
    speaker: str = ""
    channel: str = ""
    extra: dict = field(default_factory=dict)

    def __repr__(self) -> str:  # pragma: no cover
        return f"Unit({self.id}@{self.time.isoformat()})"
