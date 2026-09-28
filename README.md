# SprintMind: engineering delivery intelligence on top of Hindsight

SprintMind doesn't replace Jira, GitHub or your meeting tool. It sits on top of them and connects
three things that usually live apart:

1. **What was promised**: commitments and decisions from customer calls, planning and standups,
   including who owns each one and when it's due.
2. **What actually happened in engineering**: pull requests, merges and CI results from GitHub webhooks.
3. **What the team remembers**: every statement, update and correction, retained in a
   [Hindsight](https://github.com/vectorize-io/hindsight) memory bank with its date and source.

From those, it flags **delivery risks** such as "the Acme sandbox is due in 2 business days and CI on
its PR just failed". Each flag comes with the evidence that triggered it, a plain explanation and a
recommended next step. Risks are detected by deterministic rules; the LLM only explains verified evidence.

```
 meetings (recorded / pasted)   standups · Jira digests · SOPs     GitHub webhooks (HMAC-verified)
            │                               │                                 │
            ▼                               ▼                                 ▼
   transcribe + summarise        LLM extraction (gold for evals)      normalise PR / CI events
            └───────────────┬───────────────┘                                 │
                            ▼                                                 ▼
          ┌─────────────────────────── delivery ledger (SQLite) ───────────────────────────┐
          │ events: deduplicated, timestamped, traceable to their source                   │
          │ items: tickets · customer commitments · meeting actions (field history kept)   │
          │ dependencies & approvals · source conflicts · risks (opened / resolved history)│
          └───────┬───────────────────────────────┬───────────────────────────┬────────────┘
                  │ retain() every statement,      │ deterministic risk rules  │ current verified state
                  ▼ GitHub event, risk change      ▼                           ▼
          Hindsight memory bank ──── recall() / reflect() ────►  answers & manager insights with citations
```

## What's in the app (<http://localhost:8000/>)

| Page | What it's for |
|---|---|
| **Today** | Your open work; managers also see the delivery risks that need attention. |
| **Ask SprintMind** | Personal briefing plus chat. Answers combine the ledger's **current state** with **Hindsight memories** and cite both; click a citation to see the evidence. 👍 / 👎 / *Correct* feeds the learning loop. |
| **Action board** | Tickets, customer commitments and meeting actions in one board, with CI / PR / risk badges. Drag to change status, or open an item for its full history, dependencies, GitHub activity and evidence trail. |
| **Meetings** | Record (mic + optional Teams/Meet tab audio), upload or paste. The summary and action events land in the same ledger. |
| **Delivery radar** (managers) | Commitments at risk with evidence and next steps, dependencies and blockers, pending approvals and decisions, GitHub activity, workload from actual assignments, commitment and risk history, and missing information. Includes an "Explain from evidence" button, the Hindsight `reflect()` narrative and a cited Q&A. |
| **SOP vault** | Read SOPs and ask how-to questions. |
| **Sources** | Everything ingested, plus a form to add a standup, ticket update or SOP. |
| **Memory inspector** | Live retain / recall / reflect stream, a memory browser and a recall tester (raw access is manager-only). |
| **Demo** | The end-to-end delivery walkthrough, plus memory before / after. |
| **Settings** | Health (memory, LLM, transcriber, GitHub webhook, auth mode), bank setup, dataset, reset, theme. |

## How Hindsight memory is used

| Hindsight feature | What SprintMind does with it |
|---|---|
| **retain()** | Every source is retained as raw text plus atomic, tagged facts (`person:`, `ticket:`, `customer:`, `item:`, `kind:`). GitHub events, approvals, dashboard edits, corrections and risk openings or resolutions are retained too, so memory tracks what actually happened, not only what was said. Each memory carries its real event time. |
| **recall()** | Answers use a personal recall, a team recall and a dedicated **corrections** recall. Results are filtered by who is asking (private Q&A logs stay private) and presented chronologically next to the ledger's current state. |
| **reflect()** | The *Hindsight narrative* on the Delivery radar reflects over the whole bank with a JSON schema, grounded with the current verified state as context. |
| **Bank mission, directives, mental models** | Configured as a skeptical, literal delivery tracker: cite sources, never invent status, flag blockers, latest wins. |
| **Learning loop** | Q&A is retained as experience. Corrections are retained as shared `kind:correction` memories, recalled first, and applied to the ledger as authoritative statements. |

## Temporal and source-aware reasoning

The ledger decides what is true **now**; Hindsight remembers what was said and when.

- **Newest wins between reported sources** (meetings, standups, Jira digests). Every earlier value
  stays in the item's history.
- **Verified values are protected.** A value set by GitHub, an approval, a dashboard edit or a
  correction can't be overwritten within 3 days by a less authoritative statement. The disagreement
  becomes an **open conflict** and a `conflicting_evidence` flag instead.
- **Late-arriving old statements** (e.g. last week's standup imported today) are kept in history as
  *not applied*, with the reason.
- **Sub-steps don't close tickets.** "Wireframes reviewed: done" in a standup doesn't mark NW-244 done;
  Jira or GitHub saying the ticket is done does.
- **Owners change only through assignments** (a meeting such as planning, a dashboard edit or a
  correction), not because someone else reported progress on a shared ticket.
- **Approvals** are recognised only from affirmative statements ("approved", "signed off"); "approval
  still pending" doesn't count. They resolve the matching dependency, and a ticket that was blocked only
  by that approval moves to *todo*, not to an invented *in progress*.
- Answers get numbered evidence: **CURRENT STATE** entries first, then memories with their dates. The
  model is told to prefer current state, to say what changed and when, and to answer
  "Not recorded in SprintMind" instead of guessing. Citation numbers are checked after generation, and
  citations to evidence that doesn't exist are removed.

## Delivery risk rules

Rules run after every event (and on demand via `POST /risks/recompute`). Each finding has a severity
(high / medium / low, with no scores), an explanation, the evidence events and a recommended action.
Risk transitions are logged as events and retained in Hindsight.

| Rule | Fires when | Severity |
|---|---|---|
| `overdue` | due date passed, not done | high |
| `ci_failing` | latest CI run on the item's (or its linked ticket's) PR failed | high if due ≤ 3 business days, else medium |
| `pending_approval` | an approval dependency is open | high if due ≤ 2 business days, else medium |
| `unresolved_blocker` | blocked, or waiting on an external dependency | high if blocked ≥ 3 business days or due ≤ 3, else medium |
| `dependency_incomplete` | depends on another ticket that isn't done | high if due ≤ 3 business days, else medium |
| `deadline_at_risk` | due within 2 business days and not started / no progress for 2+ days | high if ≤ 1 day, else medium |
| `stale_progress` | due within 7 business days, no recorded activity for 3+ | medium |
| `conflicting_evidence` | an open conflict between sources on a field | medium |
| `missing_owner`, `missing_due`, `missing_blocker_reason` | a gap in the record (commitments) | **info**, shown separately and never counted as a risk |

A ticket that backs an open customer commitment reports its CI, approval and blocker problems once,
on the commitment. A commitment with no ticket is linked to one only when their titles share at least
two keywords, and the link is shown as **inferred** so a person can confirm or change it.

## GitHub integration

1. Set `GITHUB_WEBHOOK_SECRET` (any long random string) and `TICKET_PREFIXES` (your project keys,
   e.g. `NW`) in `.env`.
2. In the repo: *Settings → Webhooks → Add webhook*. Use payload URL `https://<host>/integrations/github/webhook`,
   content type `application/json` and the same secret. Select the events **Pull requests**,
   **Workflow runs**, **Check runs** and **Statuses**.
3. PRs and CI runs are linked to tickets from the PR title, branch name (`feature/nw-231-limits`) and
   body. A CI run without a ticket in its branch is linked through the PR numbers GitHub reports.

Signatures are verified (HMAC-SHA256, constant-time compare), and unsigned requests are refused
(`503` when no secret is configured, `401` on mismatch). Payloads over 5 MB are rejected. Redeliveries
and duplicate events are dropped by a deterministic event key (`duplicate` in the response).
Without credentials, the **Demo** page and `POST /demo/github` build GitHub-shaped payloads and send
them through the same normalise → ledger → risk pipeline.

## Access control

| `AUTH_MODE` | How callers are identified |
|---|---|
| `demo` (default) | The `X-SprintMind-User: <person id>` header, set by the UI's **Viewing as** switcher. Convenient for demos; **not secure**. |
| `token` | `Authorization: Bearer <token>`, with `API_TOKENS="priya=<token>,neha=<token>"` in `.env` (16+ characters each). |

Roles come from `data/team.json` (`"access": "manager"`). The UI shell, `/health`, `/team`,
`/meetings/options` and the signed webhook are public. Every other endpoint needs an identified team
member. Employees can ask only as themselves, open only their own briefing, and edit items they own
(or unassigned ones). The Delivery radar, `/risks`, `/events`, raw memory access, admin and demo
triggers are **manager-only**. Q&A logs and ratings are private to their author (employees never
recall other people's), and inspector events are redacted for employees. Each deployment talks to one
Hindsight bank (`BANK_ID`); there is no API parameter to reach another project's bank. Credentials live
only in environment variables.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # add HINDSIGHT_API_KEY and GROQ_API_KEY (GitHub secret optional)
uvicorn app.main:app --reload --port 8000       # app: http://localhost:8000  API docs: /docs
```

Then in the app: **Viewing as → Neha Kulkarni (manager)** and **Settings → Load Sprint 14 dataset**
(about 12 LLM extractions, 2–4 minutes on Groq's free tier), or `python -m scripts.seed`.

- **Keys:** Hindsight Cloud at <https://ui.hindsight.vectorize.io> (promo code `MEMHACK99` under
  Billing); Groq at <https://console.groq.com/keys>.
- **Offline mode:** `MEMORY_BACKEND=local` swaps Hindsight for an in-process keyword store, for UI work
  and tests only. Hindsight remains the memory layer of the product.
- **Groq free-tier limits:** 8k tokens per minute and 200k tokens per day on `gpt-oss-120b`.
  SprintMind caps replies (`LLM_MAX_TOKENS`), asks gpt-oss for low reasoning effort, trims evidence to a
  prompt budget and falls back to `gpt-oss-20b`. Re-seeding several times in one day can still
  exhaust the daily quota; the app then answers with a clear 503.
- **Tests:** `pytest -q` (offline, no keys). **Evaluation:** see below.

## The end-to-end demo (about 3 minutes)

Open **Demo** as Neha (manager), or run `python -m scripts.demo_flow` against a running server.

1. **A commitment is made.** An Acme check-in goes through the meeting pipeline: Priya commits to the
   sandbox at 1,200 req/min in 2 business days, linked to NW-231. A PR is opened on GitHub.
2. **GitHub: CI fails.** A `workflow_run` failure on PR #488 arrives through the webhook pipeline and is
   linked to NW-231 by branch name.
3. **Manager alert.** The Delivery radar shows a **high** `ci_failing` risk on the commitment: due date,
   the failed run (with a GitHub link), and the next step. *Explain from evidence* has the LLM summarise
   only that evidence plus recalled memories, with `[E#]` / `[M#]` citations.
4. **Fix and merge.** CI passes and the PR merges. The risk resolves on its own, NW-231 is *done*, and
   the risk history shows *opened → resolved* with the triggering event.
5. **Deliver.** Priya closes the commitment. The item's history and evidence trail keep every step.

Then show memory on the same page under *Before / after*: the same question without memory and with
Hindsight memory. On **Ask**, correct an answer and ask again to show the correction being used.

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

## Evaluation

`evals/cases.py` holds 25 scenarios. Each replays a short history through the real HTTP API
(ingestion, signed GitHub webhooks, dashboard edits, approvals, corrections) into a fresh app. They cover:

- historical commitment retrieval
- newer updates overriding older ones, including GitHub over meetings and stale late arrivals
- blocker creation and resolution
- customer approvals, including "still pending" statements that must not count
- deadline and owner changes
- CI failures and recoveries
- duplicate webhooks and duplicate ingestion
- conflicting sources
- missing information and abstention
- corrections
- private Q&A across users

```bash
python -m evals.run                                   # deterministic checks (no keys)
python -m evals.run --llm                             # + answers with GROQ_MODEL from .env
python -m evals.run --llm --model openai/gpt-oss-20b --json evals/results/run.json
python -m evals.run --llm --backend hindsight         # recall from Hindsight Cloud (temporary banks)
```

Documents are ingested with their gold extraction, so ledger state is deterministic and the LLM is
only measured on answers. Metrics:

- **Deterministic checks:** state, risks, history, dependencies, events and visibility after the scenario.
- **Factual correctness:** every expected fact is in the answer and nothing forbidden is.
- **Temporal correctness:** the same, on questions whose answer changed over time.
- **Abstention:** the answer says the information isn't recorded, without inventing it.
- **Citation support:** the answer cites evidence, all citations exist, and the cited evidence contains
  the supporting fact.

Answer checks are keyword-based, so a correct answer phrased unexpectedly can be scored as wrong. The
deterministic checks also run in `pytest` (`tests/test_evals.py`).

**Latest recorded run** (`evals/results/llm-gpt-oss-20b.json`, 28 Sep 2026: one run, answer model
`openai/gpt-oss-20b` via Groq, offline `LocalStore` recall, synthetic scenarios):

| Metric | Result |
|---|---|
| Deterministic checks | 47 / 47 |
| Factual correctness | 14 / 14 |
| Temporal correctness | 8 / 8 |
| Abstention on missing information | 3 / 3 |
| Citation support | 14 / 14 |

These numbers are small samples from one run on scenarios written for this project. They show the
pipeline behaves as designed; they are not a benchmark. The default model (`gpt-oss-120b`) and the
Hindsight Cloud backend (`--backend hindsight`) weren't part of this run; the commands above run them.
An earlier run of the same suite found two real bugs, both fixed:
- gpt-oss writes citations as `【1】`, which bypassed citation checking.
- The eval harness shared one HTTP client across event loops.

## API reference

| Method | Path | Access | Purpose |
|---|---|---|---|
| GET | `/health`, `/team`, `/meetings/options` | public | Status, roster, enums and risk rules |
| GET | `/me` | any | Who the API thinks you are |
| POST | `/ingest`, `/ingest/batch` | member | Retain a standup / meeting / SOP / ticket update; statements go to the ledger |
| GET | `/sources` | member | Documents ingested so far |
| POST | `/employee/ask` | member (self) | Answer from current state + memories, with checked citations |
| GET | `/employee/{id}/briefing` | self / manager | Personal "What's next?" card |
| GET | `/actions?status=&owner=&stage=&kind=&ticket=` | member | Board: tickets, commitments, meeting actions |
| PATCH | `/actions/{id}` | owner / manager | `{status?, owner?, due?, ticket?, note?}`; history kept, retained, risks re-evaluated |
| GET | `/items/{id}` | member | Full item: history (applied and not applied), dependencies, conflicts, GitHub state, risks, evidence trail |
| GET / POST | `/commitments` | member | List / record customer commitments |
| POST | `/items/{id}/dependencies` | owner / manager | Record a dependency or pending approval |
| POST | `/dependencies/{id}/resolve` | owner / manager | Resolve it; approvals become `approval.granted` events |
| GET | `/manager/dashboard` | manager | Delivery radar data |
| GET | `/manager/radar` | manager | Hindsight `reflect()` narrative |
| POST | `/manager/ask` | manager | Team question with citations |
| GET | `/risks?status=&kind=` | manager | Risks and missing information |
| POST | `/risks/recompute` | manager | Re-evaluate every rule |
| POST | `/risks/{id}/explain` | owner / manager | LLM explanation from the risk's evidence only (cached) |
| GET | `/events?source=&kind=&item_id=` | manager | Unified event log |
| POST | `/integrations/github/webhook` | HMAC signature | GitHub deliveries |
| POST | `/demo/github` | manager | GitHub-shaped demo event through the real pipeline |
| GET / POST | `/demo/delivery`, `/demo/delivery/{step}` | manager | Scripted walkthrough: `commitment`, `ci_failure`, `fix`, `close` |
| POST | `/meetings/record`, `/meetings`, `/meetings/transcribe` | member | Meeting pipeline (audio or text) |
| GET | `/meetings`, `/meetings/{id}` | member | Saved meetings with their actions' current state |
| POST | `/feedback` | author / manager | Rate or correct an answer |
| GET | `/sops` · POST `/sops/ask` | member | SOP vault |
| POST | `/demo/baseline`, `/demo/compare` | member | Memory before / after |
| POST | `/memory/recall` · GET `/memory/list` | manager | Raw Hindsight access |
| GET | `/inspector/events`, `/inspector/stream` | member (redacted for employees) | Live memory operations |
| POST | `/admin/setup`, `/admin/seed`, `/admin/reset` | manager | Bank configuration, dataset, reset |

## Project layout

```
app/
  main.py        FastAPI routes, access checks, wiring
  delivery.py    Delivery service: statements, GitHub, approvals, corrections -> ledger -> risks -> Hindsight
  ledger.py      SQLite ledger: events, items, field history, dependencies, conflicts, risks
  risk.py        Deterministic risk rules
  github.py      Webhook signature check, payload normalisation, ticket linking, demo payloads
  auth.py        Callers, roles, memory visibility
  agent.py       Answers (state + recall + citations), briefing, reflect narrative, corrections
  ingest.py      Document extraction -> ledger + Hindsight retain
  meetings.py    Meeting pipeline (transcribe, summarise, actions as ledger items)
  demo.py        Scripted delivery walkthrough (runs the real pipelines)
  memory.py      Hindsight wrapper and offline LocalStore
  llm.py         Groq client: retries, fallback, token caps, tolerant JSON
  transcribe.py  Free Whisper transcription (Groq or local)
  static/        Web app (no build step)
evals/           cases.py (25 scenarios), run.py (runner + metrics), results/
tests/           test_api.py, test_delivery.py, test_integration.py, test_evals.py
data/            team.json (roles), seed/ (Sprint 14), demo/ (Acme sync)
```

## Limitations

- **Demo auth mode trusts a header.** Use `AUTH_MODE=token` anywhere real, and put TLS in front.
  There is no SSO or user management; tokens are static in `.env`.
- **GitHub only.** Jira, Slack and Teams arrive as pasted or recorded text, not live integrations.
  The ticket tracker link is a URL template only.
- **Extraction quality bounds state quality.** SprintMind guards against the failure modes seen with
  real LLM output (sub-steps, reporters mistaken for owners, invented sprint-end dates), and every
  item keeps the evidence behind each value. But a mis-extracted statement can still set a wrong value
  until someone corrects it.
- **Approval matching is keyword-based** (an affirmative statement plus a shared keyword or ticket).
  Explicitly recording an approval on the dependency is the reliable path.
- **Inferred commitment-to-ticket links** use keyword overlap and are marked *inferred*.
- **Single process, single SQLite file.** Fine for a team; not a multi-tenant deployment. Each
  deployment uses one Hindsight bank.
- **No measured business impact.** SprintMind has not been validated with customers, and it makes no
  claims about time saved; the evaluation above measures behaviour on synthetic scenarios only.

## Design notes

- **Why a ledger next to Hindsight?** Risk rules need exact, current, queryable state (due dates,
  statuses, open dependencies) and an audit trail of how each value got there. Hindsight is the
  narrative memory, the ledger is the operational record, and events link the two (each event stores
  the Hindsight document id it was retained under).
- **Idempotent ingestion:** document ids come from type, date and title; statement and GitHub events
  have deterministic keys. Re-ingesting or re-delivering changes nothing.
- **Failure handling:** if LLM extraction fails, the raw document is still retained. Memory writes
  from the delivery pipeline are best-effort (the ledger is the system of record). Upstream errors come
  back as clean 502 / 503 responses.
