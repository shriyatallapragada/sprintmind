# SprintMind: a scrum memory copilot built on Hindsight

SprintMind reads your team's standups, customer meeting transcripts, SOPs and ticket updates and
stores them in a [Hindsight](https://github.com/vectorize-io/hindsight) memory bank. From that memory:

- **Employees** can ask *"What's my highest-priority task today, and which SOP applies?"* and get a
  cited answer drawn from their own tasks, blockers and customer commitments.
- **Managers** get a live sprint radar covering done, in-progress and blocked work, customer
  commitments at risk, and each person's workload. No one has to write a status report.
- **Meetings** can be recorded straight from the browser (or uploaded, or pasted). A free Whisper
  transcriber turns them into text, and SprintMind saves a summary plus **action events** sorted
  into the scrum cycle (sprint backlog, product backlog, impediments, sprint review, retro,
  follow-ups). Moving an action to *in progress* or *done* is retained again, so memory tracks it.
- **Everyone** can watch each `retain()`, `recall()` and `reflect()` call as it happens in the Live
  Memory Inspector. This shows the answers come from real task history and aren't made up.

The repo is a FastAPI service plus a **web app served at <http://localhost:8000/>**. It's plain
HTML/CSS/JS with no build step, and it runs on the same HTTP API documented below.

| Page | What it's for |
|---|---|
| **Today** | Greeting, your open actions, blockers, recent meetings. If memory is empty, it offers a one-click dataset load. |
| **Ask SprintMind** | Personal “what's next” briefing plus a chat. Every answer cites `[n]` memories; click a citation to see the exact memory. 👍 / 👎 / *Correct* feeds the learning loop. |
| **Action board** | Kanban of meeting action events (to do → in progress → blocked → done). Drag cards or click to edit owner, due date and notes. Every change is retained. |
| **Meetings** | Record (mic + optional Teams/Meet tab audio), upload or paste. See the summary, decisions, open questions, actions by scrum stage, and the transcript. |
| **Sprint radar** | `reflect()` sprint health: blocked / in progress / done, customer commitments with risk, workload bars, recommendations, evidence. Includes a free-form manager Q&A. |
| **SOP vault** | Read SOPs and ask how-to questions scoped to `source:sop`. |
| **Sources** | Everything ingested, plus a form to paste a standup, ticket update or SOP into memory. |
| **Memory inspector** | Live retain / recall / reflect / llm / transcribe stream (click a row for the raw request and response), a memory browser, and a recall tester with tag filters. |
| **Before / after** | The 3-step demo: plain LLM → retain the Acme sync → same question with memory, side by side. |
| **Settings** | Health, configure the bank, load the dataset, reset (asks you to type the bank id), and light/dark theme. |

Use **Viewing as** (top right) to switch between team members or the whole team. The pill beside it
shows live memory activity, and clicking it opens an activity drawer on any page.

```
Recorded meeting ─► Whisper (Groq free tier or local) ─► transcript ─► summary + action events
        │  POST /meetings/record                                           │
Standups / Teams transcripts / SOPs / Jira digests                         │
        │  POST /ingest  ◄──────────────────────────────────────────────────┘
        ▼
  LLM extraction (Groq) ──► atomic, tagged delivery facts ─┐
        │                                                   ├─► Hindsight retain()
        └──────────────► raw document ──────────────────────┘
                                                             │
  Employee portal ─► recall() (personal scope + team scope) ─┤─► Groq writes a cited answer
  Manager radar   ─► reflect() with a JSON response_schema ──┤─► structured sprint health
  SOP vault       ─► recall() restricted to source:sop ──────┘
  Every Q&A and correction ─► retain() again, so the next answer is better
```

## How Hindsight memory is used

| Hindsight feature | What SprintMind does with it |
|---|---|
| **retain()** | Stores each source document twice: the **raw text** (full context) and **atomic delivery facts** pulled out by the LLM, such as `[blocker] NW-240 … owner: Arjun Mehta … blocked on: Acme approval`. Each item carries the real event `timestamp`, so temporal questions like "yesterday's sync" work. It also gets entity hints (people, tickets, customers) for graph retrieval. |
| **Tags** | `person:<id>`, `source:<type>`, `sprint:<n>`, `customer:<id>`, `ticket:<id>`, `kind:<type>`, `status:blocked`. The same bank answers both "my tasks" (`tags=[person:priya]`, `any_strict`) and team-wide questions. |
| **recall()** | The employee portal runs a personal-scope recall and a team-scope recall in parallel, dedupes the results, then answers **only** from the numbered memories and cites them as `[n]`. |
| **reflect()** | The manager radar calls `reflect` with a `response_schema`, so Hindsight's own reasoning loop returns JSON the dashboard can render directly. Answers include `based_on` facts as evidence. |
| **Bank mission, directives, disposition** | The bank is configured as a skeptical, literal delivery tracker. Directives: cite sources, never invent status, always flag blockers, and prefer the latest update when two conflict. |
| **Observations and mental models** | Hindsight merges repeated status updates into observations that track change over time: *NW-240 in progress → blocked → unblocked*. Two mental models, a *Sprint status board* and *Open customer commitments*, refresh after each consolidation. |
| **Meeting action events** | A recorded meeting is retained as *summary + decisions + transcript*, and each action event as its own fact tagged `scrum:<stage>`, `person:`, `ticket:` and `action:<id>`. Every status change (`PATCH /actions/{id}`) is retained as a new, timestamped `task_update`, so Hindsight's observations see the action move *todo → in progress → done*. |
| **Learning loop** | Every question and answer is retained as experience. `POST /feedback` corrections are retained with "supersedes" wording, so the agent gets better as the team uses it. |

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # add HINDSIGHT_API_KEY and GROQ_API_KEY
uvicorn app.main:app --reload --port 8000       # API docs: http://localhost:8000/docs
python -m scripts.seed      # load Sprint 14 (SOPs, incident, planning, 6 standups, Jira digest)
python -m scripts.demo_flow # run the 3-step judge demo from the terminal
```

- **Keys:** get the Hindsight Cloud key at <https://ui.hindsight.vectorize.io> (add promo code `MEMHACK99`
  under Billing). Get the Groq key at <https://console.groq.com/keys>.
- **Seeding time:** seeding makes about 25 retain calls plus 12 LLM extractions and takes a few minutes.
  Do it **before** the demo. `--reset` wipes the bank first.
- **Offline mode:** `MEMORY_BACKEND=local` swaps Hindsight for an in-process keyword store, so the
  frontend can be built without network access. Don't use it for the real demo.
- **Tests:** `pytest -q` (offline, no keys needed).

## Recording meetings

Open **<http://localhost:8000/#/meetings/new>** (or click **Meetings → New**) while the API is running.

1. Fill in the title, type (standup, sprint planning, customer sync, sprint review, retro) and customer.
2. **Record** from the mic. Tick *Also capture audio from a browser tab* to mix in a Teams, Meet or
   Zoom web call; Chrome asks which tab to share, and you tick *Share tab audio*. You can also
   **Upload** an existing recording or **Paste** a transcript.
3. Click **Transcribe & extract actions**. The live pipeline panel shows `transcribe → llm → retain`.
4. Review the summary and the action events by scrum stage. Change an owner or status in place;
   each change is retained in Hindsight and shows up in `/manager/radar` and the employee briefing.

**Transcriber (free):**

| `TRANSCRIBER=` | What it uses | Notes |
|---|---|---|
| `groq` (default) | `whisper-large-v3-turbo` on Groq | Free tier, reuses `GROQ_API_KEY`. Files up to 25 MB; the recorder uses 32 kbps Opus (about 14 MB per hour). |
| `local` | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | `pip install faster-whisper`. Fully offline, no size limit, and slower on CPU. The first run downloads the model. |
| `none` | – | Audio is disabled. Pasted transcripts still work. |

Whisper is primed with the roster, customer names and ticket format, so names like *Priya* and
*NW-240* come out spelled correctly. Whisper **does not label speakers**, so an owner is assigned only
when the words make it clear (someone is named, volunteers, or is named in a recap); otherwise the
action stays unassigned until you pick an owner. Relative dates ("by Friday") are resolved against
an explicit two-week calendar. Long meetings are summarized in chunks to stay under Groq's
per-minute token limits. Meetings are saved as JSON in `data/meetings/` (gitignored); audio is
kept only when `KEEP_AUDIO=true`.

From Streamlit (≥ 1.39):

```python
audio = st.audio_input("Record the meeting")
if audio and st.button("Transcribe & extract actions"):
    m = requests.post(f"{API}/meetings/record",
                      files={"audio": ("meeting.wav", audio.getvalue(), "audio/wav")},
                      data={"title": "Daily standup", "meeting_type": "standup"}, timeout=300).json()
    st.markdown(m["summary"])
    for a in m["actions"]:
        st.write(f"**[{a['scrum_stage']}]** {a['title']}: {a['owner'] or 'unassigned'} · {a['status']}")
```

## The dataset

`data/seed/` is a realistic two-week sprint for the *Northwind Platform Team* (7 people) delivering
to the customer **Acme Corp**. Dates are computed relative to the day you seed, counting business
days only, so "yesterday" is still yesterday during the live demo.

| Business days ago | Document | What memory should learn from it |
|---|---|---|
| 60 | SOP-002, SOP-004, SOP-007 | PR rules, hotfix canary steps, DB migration approval rules |
| 16 | INC-0917 postmortem | Acme bulk import caused 503s; NW-231 was created as the fix |
| 10 | Sprint 14 planning | 7 tickets, each with owner, points and priority |
| 9 → 1 | Standups plus a Jira digest | Tickets move forward day by day; NW-240 becomes **blocked** on Acme's approval of a maintenance window (still blocked 3 days later) |
| 1 (live) | `data/demo/acme_teams_sync.md` | **Pasted on stage.** New commitments: 1,200 req/min sandbox by Friday, maintenance window approved for Sunday 3–5 AM IST, audit export columns, 24-hour webhook retries |

## The 60-second demo

1. **Without memory:** `POST /demo/baseline` asks the plain LLM about "yesterday's Acme sync". It
   gives a generic answer.
2. **Learning:** paste the transcript (`GET /demo/transcript` returns it) and send it to `POST /ingest`.
   The Inspector shows `llm extract` and then two `retain` calls with the extracted owners and commitments.
3. **With memory:**
   - `POST /employee/ask` as Priya: "highest priority today + SOP?" She gets the Friday sandbox
     commitment (1,200/min, burst 200, integer Retry-After) and the hotfix/PR SOP steps, with citations.
   - `GET /manager/radar` shows NW-240 moving **blocked → unblocked** now that Acme approved Sunday.
     It also flags the rollback-plan deadline and Rahul's growing workload.
   - Optional: `POST /demo/compare` returns before and after side by side in one call.

## API reference

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Hindsight and Groq connectivity |
| POST | `/admin/setup` | Create and configure the bank (mission, directives, mental models). Idempotent. |
| POST | `/admin/seed?include_demo=false` | Load the dataset through the API |
| POST | `/admin/reset` | Delete the bank and the local source registry |
| GET | `/team` | Roster and customers (`data/team.json`) |
| POST | `/ingest` | `{source_type, title, content, occurred_at?, customer?, participants?}`. `source_type` is one of `standup, meeting, sop, task_update, retro, incident, note`. Returns the extracted items. |
| POST | `/ingest/batch` | List of the above |
| GET | `/sources` | Documents ingested so far |
| POST | `/employee/ask` | `{question, person_id?, budget?}`. Returns `answer`, `memories[]`, `interaction_id` |
| GET | `/employee/{person_id}/briefing` | "What's next?" card as JSON: top priority, tasks, blockers, commitments, SOPs |
| GET | `/manager/radar` | Structured sprint health from `reflect()`, plus narrative and `based_on` evidence |
| POST | `/manager/ask` | Free-form manager question, answered with `reflect()` |
| GET | `/sops` · POST `/sops/ask` | SOP vault |
| POST | `/demo/baseline` · `/demo/compare` | No-memory baseline, and a side-by-side comparison |
| GET | `/demo/transcript` | The Acme sync transcript, ready to post to `/ingest` |
| GET | `/` | The web app (`/recorder` redirects to its Meetings page) |
| POST | `/meetings/record` | Multipart: `audio` file + `title, meeting_type, occurred_at?, customer?, participants?, language?`. Runs transcribe → summary + action events → retain. Returns the saved meeting. |
| POST | `/meetings` | Same pipeline for a pasted `transcript` (JSON) |
| POST | `/meetings/transcribe` | Speech-to-text only; nothing is retained |
| GET | `/meetings` · `/meetings/{id}` · `/meetings/options` | Saved meetings (summary, decisions, open questions, actions, transcript), plus the valid types, stages and statuses |
| GET | `/actions?status=&owner=&stage=&meeting_id=` | Action board across all meetings |
| PATCH | `/actions/{id}` | `{status?, owner?, due?, note?, person_id?}`. Updates the action and retains the change |
| POST | `/feedback` | `{interaction_id, helpful, correction?, person_id?}`. Corrections are retained. |
| POST | `/memory/recall` · GET `/memory/list` | Raw memory access for the context verification panel |
| GET | `/inspector/events?after=<id>&op=` | Live Memory Inspector log (poll this) |
| GET | `/inspector/stream` | The same log as Server-Sent Events |

Each inspector event looks like this:
`{id, ts, op: retain|recall|reflect|llm|setup, status: started|ok|error, request{…}, response{…}, duration_ms}`.
Recall and reflect events include the facts they returned, so the verification panel can list them.

### Calling it from Streamlit

```python
import requests, streamlit as st
API = "http://localhost:8000"

q = st.text_input("Ask SprintMind")
if q:
    r = requests.post(f"{API}/employee/ask", json={"question": q, "person_id": "priya"}, timeout=120).json()
    st.markdown(r["answer"])
    with st.expander(f"{r['memory_count']} memories recalled from Hindsight"):
        for i, m in enumerate(r["memories"], 1):
            st.caption(f"[{i}] {m['occurred_start'] or ''} · {m['type']} · {', '.join(m['tags'])}")
            st.write(m["text"])

# Live inspector: keep the last event id in session_state and poll
after = st.session_state.get("after", 0)
ev = requests.get(f"{API}/inspector/events", params={"after": after}).json()
st.session_state["after"] = ev["last_id"]
for e in ev["events"]:
    if e["status"] != "started":
        st.code(f"{e['op']:<8} {e['status']:<5} {e.get('duration_ms', '')}ms  {e['request'].get('label') or e['request'].get('query', '')}")
```

## Project layout

```
app/
  main.py       FastAPI routes and dependency wiring
  memory.py     Hindsight wrapper (bank config, retain/recall/reflect) and an offline LocalStore
  ingest.py     Extraction pipeline: raw document + tagged atomic facts → retain
  transcribe.py Free speech-to-text: Whisper on Groq, or faster-whisper locally
  meetings.py   Meeting pipeline: transcript → summary + scrum action events → retain; action board
  static/       Web app: index.html, app.css, app.js (served at /, no build step)
  agent.py      Employee ask, briefing, manager radar, SOP vault, baseline, feedback loop
  llm.py        Groq client: retries, fallback model, tolerant JSON parsing
  inspector.py  Live Memory Inspector event log and SSE
  team.py       Roster and name resolution
  seed.py       Loads the dataset with dates relative to today
data/           team.json, seed/ (SOPs, standups, planning, incident, Jira), demo/ (Acme sync)
scripts/        seed.py, demo_flow.py
tests/          Offline API tests (LocalStore + fake LLM)
```

## Design notes

- **Why retain raw text and extracted facts?** Hindsight's extraction keeps nuance and wording from
  the raw text. The atomic facts carry exact tags (`person:`, `ticket:`, `status:blocked`), which
  make per-person recall precise. Overlapping facts are merged by Hindsight's observation
  consolidation instead of piling up as duplicates.
- **Idempotent ingestion:** the `document_id` comes from type, date and title. Re-ingesting a document
  replaces its memories.
- **Failure handling:** if LLM extraction fails, the raw document is still retained. Groq JSON
  failures are retried without JSON mode, then on the fallback model. Upstream errors come back as
  clean 502 or 503 responses. Learning-loop writes can never break an answer.
- **Temporal grounding:** facts carry the real event time. Recall sends `query_timestamp=now`, so
  "yesterday" and "last week" resolve correctly.
