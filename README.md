# Candor take-home: temporal workplace memory

Standard-library-only implementation of the required Candor memory interface plus the optional dry-run action assistant.

## Deadline note

The email supplied with the task says Friday 2 October at 11:30 AM IST while the supplied `BRIEF.md` says Friday 2 October 2026 at 10:30 AM IST. Use the earlier 10:30 AM deadline unless the recruiter confirms otherwise.

## What it does

Ingests meetings, dictations, Slack, Gmail, Calendar, Codex and ChatGPT into one normalized unit model. Each unit keeps:

- stable citation id and containing record id
- source metadata and speaker/recipient/channel fields
- delivery/availability time
- Slack edit and deletion history

At query time it reconstructs the data visible at `as_of` **before** retrieval. Future records and deleted messages never enter the retrieval set; edited messages are reconstructed from the edit timeline.

Retrieval is hybrid and deterministic:

- lexical BM25-like token scoring with lightweight stemming and synonym expansion
- entity/name/date overlap (critical for Sarah Kim vs Sarah Patel, Marcus vs John)
- source and intent boosts plus exact-phrase boosts (e.g. “test plan”)
- explicit cross-source joins (flight date → calendar events on that date, dictation ↔ email, edit companions, board-prep calendar + email)
- diversity limits so one long meeting transcript does not crowd out distinct evidence
- prompt-injection and secret filtering (planted instructions treated as content, excluded from evidence)

The answer layer is concise and deterministic with handlers for temporal state changes, commitments, ownership, disagreements, reported speech, corrections, travel/calendar cross-source queries and abstention. No external API key needed.

## Files

```
main.py                  memory CLI (required interface)
actions.py               dry-run action CLI (bonus interface)
run_all.py               one-command train eval
selftest.py              temporal/deletion/injection regression tests
src/models.py            normalized unit model + glossary
src/ingest.py            source ingestion + temporal reconstruction
src/retrieval.py         hybrid retrieval + joins + trust filtering
src/answers.py           deterministic answer handlers + abstention
src/memory.py            orchestration: visible -> retrieve -> answer
src/actions.py           dry-run action parsing
tests/test_core.py       pytest mirror of selftest
evals/                   supplied train questions
eval_harness/            supplied official scorer
outputs/                 generated train answers/predictions/results
```

## Run

Python 3.10+ only. No third-party dependencies.

Full evaluation:

```bash
python3 selftest.py
python3 run_all.py
```

Produces:

```
outputs/memory_train_answers.jsonl
outputs/actions_train_predictions.jsonl
outputs/results_retrieval.json
outputs/results_memory.json
outputs/results_actions.json
```

Memory on another question file:

```bash
python3 main.py --questions path/to/questions.jsonl --output answers.jsonl
```

Actions:

```bash
python3 actions.py --commands path/to/actions.jsonl --output actions.jsonl
```

Regression tests:

```bash
python3 selftest.py
# or
python3 -m pytest tests/test_core.py -q
```

## Train results

Honest general-purpose system — no per-question scripts, no hardcoded record
ids, no train answer key. Produced by this commit with the supplied offline
harness (`--judge none`, reproducible without paid APIs):

- **memory retrieval: 84.0%** primary (everything needed in top 10 + nothing
  forbidden in top 10) on 25 scored + 2 harm-only; **0 forbidden** in top 10 /
  top 20
- **memory answers: 59.3% strict / 66.7% lenient**, 2 unverified (history
  mentions needing a judge), **0 hard failures** (no future leaks, no secrets,
  no injections)
- **actions: 75.0% pass rate, 89.2% argument accuracy** on 12 cases

Official answer scoring can add an LLM judge (`--judge claude-cli` /
`anthropic` / `openai`); the judge confirms or downgrades rule passes, which
resolves the unverified history mentions. Previous submission reached
100%/100%/100% on train with per-question handlers but dropped to 71%
retrieval and 1/13 actions on hidden questions; this rewrite removes those
handlers so train and hidden should track closely instead.

## Key decisions

**Temporal visibility first.** `as_of` is applied before ranking, not after.
Future evidence never reaches retrieval or answers; verified by hidden test
(no future/deleted leaks).

**Unit-level citations.** Meeting segments and ChatGPT messages retain
specific ids. Whole-meeting ids are not substituted when passage ids exist.

**Edits and deletions are events.** A Slack edit is its own citable event;
the original reconstructs to edited text from edit time onward. Deleted
messages disappear at deletion time; deletion markers are not evidence.

**Evidence-driven answers, no scripts.** Answers synthesize the top ranked
evidence: speaker attribution preserved (second-hand vs direct stays
separate), competing views included rather than picking a winner, most recent
evidence preferred for current values (e.g. corrected NRR 112 over outdated
118), simple date differences computed from evidence dates. Weak evidence
abstains with “I don’t have that in memory” instead of guessing.

**Abstention is explicit.** Unsupported queries abstain when best evidence
shares too little with the question — no hardcoded topic list.

**Prompt injection is data.** Planted instructions filtered by content
patterns (not ids); claims excluded and never repeated. Secrets never
retrieved or repeated.

## What didn’t work / known limits

An earlier version used per-question answer handlers and id-specific
retrieval/action rules. It hit 100%/100%/100% on train but fell to 71%
retrieval and 1/13 actions on hidden questions (e.g. “What NRR should go in
the board deck?” fired the board-prep handler; unseen hotel/candidate
questions got “I don’t have it” despite evidence in data). This rewrite
deletes all of that; train scores are lower but honest and should generalize.

Remaining limits (lexical, stdlib-only, no embeddings): paraphrases with
almost no shared tokens (e.g. “which database” vs “Postgres/PostGIS”
without the word database nearby) can miss; minimal general-English
expansion covers contract/pricing/database/delay verbs only. Dense retrieval
would help there at the cost of reproducibility. The action parser is
general intent + data lookups (people/channels/events resolved from data/,
times relative to `as_of`); ambiguous people clarify, destructive confirms.

Action assistant is dry-run only. Relative dates (“tomorrow at 2”, “an hour
before board meeting”) resolved against `as_of` in America/Los_Angeles using
calendar data.

## Tools and cost

- Python 3.10+ stdlib only
- Supplied official Python scoring harness
- No external APIs, no paid models
- Runtime tool cost: ₹0
