# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

---

# PROJECT CONTEXT — YClients Push Automation

**Goal:** Server-side automation that reads leads from a Google Sheet and sends push
notifications to clients through the YClients web cabinet, then writes the result back
to the same sheet.

**Why a browser, not pure API:** YClients exposes client-base operations via API, but
push broadcasts are documented only through the web UI (push reaches clients who have the
YClients app installed with notifications enabled). So client lookup/create *may* use the
API, but the push-send step MUST go through the UI via Playwright.

**Flow:**
```
Google Sheets (rows: phone, push text, extra fields, status)
  → Python backend (polling every 15–60s; optional Apps Script webhook)
  → task queue (1 task = 1 row; processed strictly serially)
  → Playwright worker (persistent Chromium profile, logged-in YClients session)
  → YClients UI: find client by phone → create if missing (no dupes) → send push
  → write status back to the sheet
```
Result statuses written back: sent · client-created · client-already-existed · error
(+message) · skipped · no-push-channel · reprocessed.

**Stack:** Python · Playwright (persistent context, Chromium) · Google Sheets API (gspread
or google-api-python-client) · FastAPI (webhook + manual trigger) · PostgreSQL (queue +
state) · Docker / Docker Compose · loguru · healthcheck endpoint + Docker restart policy.
Deployed on a Russian VPS (Ubuntu, Docker Compose).

**Key invariants / risks:**
- Process strictly one row at a time in the browser — prevents duplicate clients and UI
  conflicts.
- Idempotency via row status + internal `task_id`.
- Persistent browser profile in a Docker volume so a container restart restores the
  logged-in session.
- Main fragility: YClients markup changes, modal dialogs, and slow loads — error handling
  here is the core of the work.

**Chosen approach (locked; detail in the Obsidian project note):** Variant 3 — a single
persistent browser worker + task queue, with **every** action done through the YClients UI
via Playwright (no API; variants 1 and 2 are dropped — the client's whole process is
UI-based and testing runs on the client's own accounts). Built incrementally: MVP = Sheets
polling + persistent Playwright worker + Docker, processing rows strictly one at a time;
then add the Apps Script webhook (a reaction accelerator, not a replacement for polling)
and a durable queue. Concurrency is always 1 to prevent duplicate clients and UI conflicts;
idempotency via row status + internal `task_id`; the logged-in session lives in a persistent
Chromium profile in a Docker volume.

---

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

# PROJECT WORKFLOW & CONVENTIONS

This project runs as a three-role pipeline. This file is the shared contract:
it is loaded into every agent (orchestrator, coder, tester), so the conventions
below apply to all of them. Role-specific behavior lives in each agent's own
definition file; the rules here are the common ground.

## 5. Roles

- **Orchestrator** — the main session (Opus). Plans, owns all Obsidian notes,
  delegates work, decides what to do on failures. The ONLY role that edits notes.
- **coder** — subagent (Sonnet). Implements exactly one stage from its stage note,
  then commits. Never edits notes.
- **tester** — subagent (Sonnet). Read-only. Verifies the last commit against the
  stage note and returns a PASS/FAIL verdict. Never edits code or notes.

Hard rules for all roles:
- Subagents cannot spawn other subagents. coder and tester report back to the
  orchestrator only via their final message.
- On an ambiguous, contradictory, or underspecified plan: STOP. Do not improvise.
  coder/tester surface the problem to the orchestrator; the orchestrator re-reads
  context and resolves it (see §1).
- **A stage is "done" ONLY after tester returns PASS.** Nothing else marks completion.

## 6. Obsidian Vault Conventions (via the Obsidian MCP server)

The Obsidian vault is the single source of truth and the only reliable channel
between orchestrator and subagents (a subagent starts with fresh context and sees
only the prompt it's handed). The orchestrator passes a stage-note path; coder and
tester open and read that note themselves before doing anything.

The vault has its OWN authoritative `CLAUDE.md` at its root with the full
conventions (complete tag taxonomy, folder→frontmatter table, templates, move
rules). The agents run from the CODE repo and reach the vault only over MCP, so
the vault's `CLAUDE.md` is NOT auto-loaded into their context — the rules the
pipeline depends on are reproduced below. If anything about vault mechanics is
unclear, read the vault's own `CLAUDE.md` over MCP first; it wins on conflicts.

### Where pipeline notes live
Freelance/client projects go under `Projects/Work/<Project>/`; personal projects
under `Projects/Personal/<Project>/` (same layout, omit `client`).

**Note layout — root vs wave-folders.** The project root holds only durable,
*current* notes: the main project note, the live architecture/tech doc(s), decision
notes, and key references. Stage/implementation notes are grouped into **wave
subfolders** — one folder per development wave or redesign (e.g. SkillUp has
`pipeline-v1-frequency-core/`, `mvp-gamification/`, `redesign-v2-topdown/`). This
keeps the root a clean snapshot of the system as it is now, while superseded
build-logs stay preserved but out of the way. A new stage note goes into the
**current** wave's folder.

**MCP mechanics for filing a stage note:** `create_note` cannot target a subfolder —
it always lands the note flat in the project root. So filing is two steps:
`create_note` (root) → `move_note` into the current wave folder. Moving only changes
the folder, so `[[wiki-links]]` are untouched (Obsidian resolves them by filename).
There is no `create_folder` over MCP: if the current wave folder doesn't exist yet,
ask the human to create it, then move into it. Don't let stage notes pile up in the
root.

- **Main project note** — title `<project-slug>`, `type: project`
  - frontmatter: `type: project`, `project_status`, `client: "[[…]]"` (Work only),
    >=1 Тематика tag
  - body: purpose, overall architecture, key decisions, data model, and
    `[[links]]` to every stage note
- **Stage notes** — title `NN-stage-slug` (e.g. `01-foundation`), `type: note`
  - frontmatter: `type: note`, `project: "[[<project-slug>]]"`, >=1 Тематика tag
  - body: stage goal · architecture for the stage · surface-level logic ·
    concrete details (what/how to use, available data, interfaces) ·
    **Acceptance criteria (testable)** · implementation checklist
- **Decision notes** — title `decision-<slug>`, `type: decision`
  - Logged whenever the orchestrator resolves a FAIL or makes a non-trivial
    architectural call, so the reasoning survives across sessions.
  - frontmatter: `type: decision`, `decision_status: proposed|accepted|rejected`,
    `date`, `project: "[[…]]"`, >=1 Тематика tag
  - any numbers in a decision (e.g. a contrast target) must be code-computed (§10),
    never estimated
- Prefer the matching `note_type` template if the vault applies one.

**Acceptance criteria** are explicit, binary, testable statements — e.g.
"button text contrast >= 4.5:1 (AA normal), computed via code", "invalid email is
rejected before submit", "yearly toggle recomputes all three prices". They are
exactly what the tester checks, and they are what lets delegation prompts stay
thin (§11). A vague criterion ("looks good") is a bug in the note — fix the note.

### Vault mechanics the agents MUST follow
- **Links**: always `[[Note name without extension]]` (or `[[Note|display text]]`).
  Never markdown `[text](path.md)` — Obsidian won't show it in the graph.
- **Filenames**: cyrillic/latin, digits, spaces, hyphens only. Forbidden chars:
  `: / \ * ? " < > | # ^ [ ]`. So note titles use hyphens/spaces and NEVER a colon
  (e.g. `02-auth-flow`, not `stage-02: auth`). The git commit message in §8 may use
  a colon; that restriction is for filenames only.
- **Tags**: only from the vault's approved list, in YAML `tags:`. The Тематика
  category — required for project/note/decision notes — is: `ml`, `ai`,
  `education`, `dev-tools`, `prompt-engineering`, `obsidian`, `social`, `youtube`,
  `gamedev`, `dataset`, `object-detection`, `startup`, `web`.
- **Never touch `.obsidian/`** — don't read or write it.
- **Never hard-delete** a note. Soft-move to `Archive/<original-folder>/` and add
  `archived_at: <ISO date>`.
- Before mass edits (>10 notes), show the plan first.

### Checklist semantics (inside stage notes)
- `- [ ]` = pending
- `- [x]` = done — set by the orchestrator ONLY after tester returns PASS

### Note discipline
- Only the orchestrator writes/edits notes. coder and tester are read-only on notes.
- Keep heavy detail in the notes, not in chat. This keeps the orchestrator's context
  clean and lets any fresh subagent reconstruct full context from the note alone.
- Each stage note must be self-contained enough that coder/tester need nothing beyond
  it plus the codebase to do their job.

## 7. Stage Loop (run once per stage)

Delegate with the thin contract in §11 — point coder/tester at the note, don't
re-paste it. Delegations are idempotent: coder commits are atomic per stage and the
tester is read-only, so any delegation can be safely re-run if interrupted (e.g. a
usage limit) without corrupting state.

1. Orchestrator delegates to **coder** with the stage-note path.
   → coder implements ONLY that stage (§2, §3, §9), then commits (§8).
2. Orchestrator delegates to **tester** with the same stage-note path and the commit hash.
   → tester diffs that commit against the note's acceptance criteria and returns PASS or FAIL.
3. **PASS** → orchestrator checks off completed items in the stage note, then moves
   to the next stage.
4. **FAIL** → orchestrator re-reads the project context and the stage note, identifies
   the root cause, fixes/clarifies the note if the plan was at fault, and logs any
   non-trivial architectural decision as a decision note (§6; numbers in it computed
   via code, §10). Then re-delegates to coder (thin re-pass prompt, §11), then to
   tester again. Repeat until PASS — or, if blocked by genuine ambiguity, stop and
   ask the human.

## 8. Commits

- coder commits per stage with a clear message that names the stage
  (e.g. `stage-02: <slug> — <what>`), so tester can reliably review "the last commit."
- One stage = one coherent commit (or a small, related set). Don't bundle multiple
  stages into one commit; tester verifies against a single stage note.

## 9. Scope Discipline (reinforces §2 and §3 for the pipeline)

- coder implements only the current stage. No future-stage work, no speculative
  abstractions, no touching code outside what the stage requires.
- coder must NOT knowingly commit code that violates the stage note's acceptance
  criteria. If a criterion cannot be met within the stage's scope, coder STOPS and
  reports to the orchestrator (with code-computed numbers where relevant) instead of
  committing a known-failing result. A known violation is not a "deviation note" — it's
  a stop.
- tester reports issues precisely (file/line, expected vs actual, computed numbers).
  It never fixes code and never edits notes — fixing is the next coder pass, decided
  by the orchestrator.

## 10. Numeric & Factual Verification (all roles)

Any numeric or factual claim that can be computed or checked MUST be produced by
running code — never estimated from intuition. This covers contrast ratios, font
sizes, percentages, timings, element counts, and similar. Always state the computed
value next to the threshold/expected value it is compared against.

- The **coder** computes (e.g. python via Bash) before claiming a number; it never
  writes a figure it didn't compute.
- The **tester** recomputes independently and NEVER trusts numbers reported by the
  coder or written in commit messages — if the coder says "3.6:1", the tester
  derives its own value and judges against that.
- The **orchestrator** computes (or has the tester compute) any number it puts into
  a decision note.

(Rationale: a single contrast value was once hand-estimated four different ways
across the roles; only the code-computed value was correct.)

## 11. Thin Delegation Contract

The stage note already carries the full plan and acceptance criteria, so the
orchestrator must NOT copy that content into delegation prompts. Re-pasting bloats
the expensive (Opus) context and duplicates the source of truth. Delegate with the
minimum the subagent can't derive itself:

- **To coder:** `Implement stage <NN> per its note: <note-path>. Repo: <repo-path>.`
  On a re-pass after FAIL, add ONLY: the specific failed criterion, a one-line
  pointer to the tester's finding, and the decision-note path if one exists.
- **To tester:** `Verify commit <hash> against stage <NN> note: <note-path>. Repo: <repo-path>.`
  Nothing else — the tester reads the acceptance criteria itself and uses its own
  response format.

Add a line of genuinely out-of-note context only if needed (e.g. an environment
quirk). Never paste the note's checklist into the prompt.
---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer
rewrites due to overcomplication, clarifying questions come before implementation
rather than after mistakes, every stage in Obsidian is checked off only after a real
PASS, every number that gates a PASS was computed by code (not estimated), and
ClickUp receives only end-of-run follow-ups rather than per-stage noise.

main.html - начальная страница на которую попадаешь при переходе по ссылке до авторизации
login.html - страница с полями для логина
dashboard.html - страница на которую попадаешь после авторизации
clients_base.html - старница клиентской базы
add_client.html - страница с виджетом добавления клиента
client_data.html - страница с подробными данными клиентов
actions_choose.html - виджет выбора действий с пользователем
push.html - виджет пуш уведомления

сервер
ssh root@5.42.99.152
kXoELV+Vm-e4Ut

