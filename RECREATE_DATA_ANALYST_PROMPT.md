# One-Shot Build Prompt — Data Analysis Agent

> Copy everything inside the fenced block below into a fresh Claude Code session
> (in an empty git repo). It is self-contained: it tells Claude what to build,
> the tech stack, the full data model / API / agent graph, all 32 capabilities,
> the UI, and how to work (spec-driven, phased, tested).

````text
You are building a production app from scratch called the **Data Analysis Agent**.
Work spec-driven and in phases. Before writing any application code, write the full
spec under `spec/`, then implement phase by phase. Commit every logical unit of work;
never leave the tree dirty. Keep a running session report in `reports/sessions/`.
After each phase, run the test suite and the golden-path smoke test before moving on.

=====================================================================
1. WHAT IT IS
=====================================================================
A local, single-user web app. The user uploads a CSV (or TSV/TXT/JSON/Excel) through
the browser and asks questions about the data in plain English. A LangGraph ReAct loop
(Reason + Act) powered by Google Gemini reasons over the data with pandas, iterating
until it can give a confident natural-language answer with optional inline Plotly charts.
No code or SQL required from the user.

Users: data analysts, PMs, and non-technical stakeholders with structured data in files.
Core value: upload once, ask in plain English, get correct explainable answers in seconds.

Out of scope: auth / multi-user, database querying (files only), proactive auto-insights.

=====================================================================
2. TECH STACK & HARD CONSTRAINTS
=====================================================================
- Python 3.12+, managed with `uv` + `pyproject.toml`. ALL commands prefixed `uv run`.
- Backend: FastAPI + uvicorn; Jinja2 server-rendered HTML (NO JS framework, NO bundler).
  All front-end interactivity is vanilla JS inside the template files.
- Agent framework: LangGraph `StateGraph` (ReAct loop). Do NOT use LangChain.
- LLM: Google Gemini via `google-genai`; default model `gemini-3.1-flash-lite`.
  Also support an OpenRouter provider and a local Stub provider behind one uniform
  `complete(prompt) -> LLMResponse` interface.
- DB: SQLite via SQLAlchemy 2.0 declarative ORM (`DeclarativeBase`, `Mapped` types).
  Schema managed by Alembic migrations; app also calls `init_db()` on startup.
- Data: pandas 2.x, numpy, pyarrow (Parquet), openpyxl/xlrd (Excel).
- Charts/stats available in the agent sandbox: plotly, matplotlib, seaborn, scipy,
  scikit-learn, statsmodels.
- Markdown→HTML: markdown-it-py. Logging: structlog. Tables: tabulate.
- Dev port: **8001** (not 8000). Run with `uv run python -m data_analyst`.
- Python package name: `data_analyst`. All source under `src/data_analyst/`.
- Env vars (prefix `DATA_ANALYST_`):
    DATA_ANALYST_LLM_PROVIDER   = auto | gemini | openrouter | stub   (default auto)
    DATA_ANALYST_GEMINI_API_KEY = ...   (absent -> automatic stub mode)
    DATA_ANALYST_OPENROUTER_API_KEY = ...
    DATA_ANALYST_LLM_MODEL      = gemini-3.1-flash-lite
    DATA_ANALYST_MAX_ITERATIONS = 6
- STUB MODE: when no Gemini key is set (and no provider configured), run automatically
  in stub mode, show a visible yellow banner, and return plausible canned answers so the
  app is fully usable with zero API keys / zero network. Phase 2 tests must pass with no
  env vars set (in-memory SQLite, stubs only, no network I/O).

=====================================================================
3. PROJECT LAYOUT (repo root IS the project — no nested app dir)
=====================================================================
src/data_analyst/
  __init__.py            (__version__)
  __main__.py            (uvicorn entry on :8001)
  api/        __init__.py(create_app() factory + lifespan), _common.py (ok()/api_error()),
              one router module per resource (upload, datasets, ask, sessions, runs,
              stats, memory, health)
  config/settings.py     (Pydantic BaseSettings, env_prefix="DATA_ANALYST_")
  db/         models.py (SQLAlchemy 2.0), session.py (engine + sessionmaker + init_db)
  domain/                (Pydantic models per entity)
  graph/      state.py (AgentState TypedDict), nodes.py, edges.py, agent.py
              (StateGraph compiled at startup), runner.py (run_agent()),
              describe.py (C30), compress.py (C31)
  llm/        client.py, providers/{base,factory,gemini,openrouter,stub}.py
  prompts/               (.md prompt files loaded at runtime)
  templates/             (base.html, index.html — server-rendered UI)
  observability/events.py (structlog config)
tests/  (at repo ROOT, not under src) unit/ + integration/; pyproject testpaths=["tests"]
alembic/ env.py, script.py.mako, versions/0001_initial.py
pyproject.toml, alembic.ini, .env.example, README.md
Conventions: TypedDict state (not dataclass/Pydantic); no repository pattern (direct
SQLAlchemy in nodes/handlers); tools are pure functions; every route returns ok(data)
or raises api_error(); never call a provider SDK directly in nodes — go through LLMClient.
Uploaded files saved to `uploads/{dataset_id}.csv` (+ `.parquet`). SQLite at `data_analyst.db`.
README: state "all commands run from repo root", prefix every command with `uv run`,
and include `uv run alembic current` after `upgrade head` to verify tables exist.

=====================================================================
4. DATA MODEL (SQLite; 4 tables)
=====================================================================
datasets: id(uuid PK), filename, file_path, row_count, col_count, columns_json,
  content_hash(sha256 of bytes), format(csv|tsv|txt|json|excel), context(notes, <=4000),
  origin(uploaded|derived), derived_from_run_id, derived_from_dataset_ids(json),
  derivation_code, parquet_path, auto_notes_status(pending|done|failed|null),
  context_facts(json), created_at, updated_at(onupdate).
query_runs: id(uuid PK), dataset_id, session_id(nullable), question, answer(md),
  status(pending|running|completed|failed|clarification), error_message,
  action_history(json of {action,result,is_error}), iteration_count,
  tokens_input, tokens_output, prompt_breakdown(json), dataset_ids_json,
  selector_reasoning, created_at, updated_at.
conversation_sessions: id(uuid PK), dataset_id, dataset_ids_json, name, created_at, updated_at.
settings: key(PK), value, updated_at. Reserved keys: global_memory, global_memory_facts,
  llm_model, max_iterations.
Relationships (enforced in code, no FK constraints): run.dataset_id->datasets.id;
run.session_id->sessions.id; session.dataset_id->datasets.id. Deleting a dataset cascades
to its sessions, runs, and on-disk files AND recursively deletes derived datasets whose
parents include it. Datasets persist indefinitely (no TTL).

=====================================================================
5. AGENT GRAPH (LangGraph StateGraph — ReAct loop)
=====================================================================
AgentState(TypedDict,total=False): run_id, dataset_ids:list[str], dataset_context:str|None,
  session_id:str|None, question, conversation_history:list[dict], action_history:list[dict],
  iteration_count:int, llm_response:str, tokens_input:int, tokens_output:int,
  charts:list[str], answer:str|None, error:str|None, status:str, selector_reasoning:str|None.

Nodes:
- setup: for each dataset_id check session DataFrame cache (C27) keyed by session_id; on
  hit reuse + LRU-touch; on miss load Parquet (preferred) or CSV (fallback) and cache.
  Single-turn (no session_id) uses a run-scoped _dataframes[run_id] dict instead. Fatal
  load/lookup error -> handle_error.
- plan_action: build prompt from question + action_history + conversation_history +
  dataset_context + persistent memory (+ column schema), prepend the CURRENT DATE
  ("Today's date is <YYYY-MM-DD>", so unqualified dates like "June 23rd" resolve to the
  right year instead of a guess), inject `<node:plan>` tag, call LLM;
  write llm_response, increment iteration_count, add token counts. When
  iteration_count >= max_iterations-2, append a wrap-up instruction telling the model to
  produce a FINAL ANSWER now from its best findings (no extra LLM call).
- execute_action: eval the pandas expression in a sandbox namespace; capture any Plotly
  figures as JSON; convert result to string; append {action,result,is_error} to
  action_history; write iteration_count to DB each step for live progress polling. On
  exception mark is_error and route back to plan_action to self-correct.
- finalize: strip leading `FINAL ANSWER:` prefix, append chart divs, set status=completed,
  persist answer+action_history, release run-scoped DataFrame.
- force_finalize: fires on max-iter OR 3 consecutive errors; ONE synthesis LLM call with
  `<node:finalize>` tag; status ALWAYS completed; error_message = "max_iterations" or
  "consecutive_errors". Falls back to a static message if the call fails.
- handle_error: fatal errors -> status=failed; persist error; release DataFrame.

Edges:
  START->setup; setup->(error)handle_error | (ok)plan_action;
  plan_action->(FINAL ANSWER)finalize | (fatal)handle_error | (action)execute_action;
  execute_action->(3 consec errors OR max_iter)force_finalize | (fatal)handle_error |
    (ok/recoverable error)plan_action;
  finalize/handle_error/force_finalize -> END.
Termination signal: case-insensitive substring `FINAL ANSWER:` in llm_response (tolerate
preamble before it). MAX_ITERATIONS=6 (env-configurable).

Sandbox namespace for execute_action (eval/exec): df (first DataFrame), df1/df2/...
(per-dataset), <filename_stem> alias, pd, np, px, go, plt, sns, scipy, stats, sklearn, sm,
and save_dataset(df,name,desc) which materializes a DataFrame as a registered DERIVED
dataset (writes CSV+Parquet, records derivation_code + parents + producing run) and returns
a confirmation string (C25).

Graph-adjacent single LLM calls (not graph nodes):
- generate_suggestions(question,answer) -> up to 3 short follow-up questions (JSON array;
  []
 on failure); tokens added to the run total.
- describe.py generate_dataset_notes() (C30): sample 50 rows, ask LLM for <=300-word plain
  notes, write to dataset.context, track auto_notes_status, then trigger C31.
- compress.py extract_facts() (C31): one LLM call -> JSON array of <=20 facts; used to fill
  dataset.context_facts and settings.global_memory_facts; async fire-and-forget self-heal
  variants with an in-flight lock; failures return [].

Pre-flight (before the graph, both one-shot LLM calls, skipped when explicit dataset_ids
are supplied):
- C26 clarification: check_clarification() -> either a clarifying question (return early,
  no agent run) or proceed.
- C19 selector: select_datasets() with all dataset schemas -> subset of dataset IDs to load;
  falls back to ALL datasets on failure. selector_reasoning persisted.

Stub provider branches on the prompt tag: `<node:finalize>` -> canned best-effort summary;
`<node:select>` -> first dataset id from the schema block as a 1-element JSON array;
`<node:plan>` -> 1st call returns `df.describe().to_string()`, 2nd call returns a
`FINAL ANSWER:` markdown summary (iteration counted from Result:/Error: markers so repeated
calls differ); missing plan tag -> `FINAL ANSWER: [stub] Unable to process`.

=====================================================================
6. REST API (envelope: {"data":...,"error":null} or HTTP 4xx/5xx with
   {"detail":{"code","message"}})
=====================================================================
GET  /health                          -> {status:"ok"}
GET  /                                -> main Jinja2 UI
POST /upload (multipart: file; form: context?, notes_file?; query: force=false)
     parse, sha256, duplicate-check (409 duplicate_dataset w/ match_type+existing_*),
     save CSV + Parquet, create dataset. 400 bad ext / unparseable / empty; 500 write fail.
     Accept .csv/.tsv/.txt/.json/.xlsx/.xls. Returns dataset_id, filename, format,
     row_count, col_count, columns, context, auto_notes_status.
GET  /datasets                        -> list (incl. origin, stale, derived_* , derivation_description)
GET  /datasets/{id}                   -> full metadata incl. columns_schema[{name,dtype-alias}]
     (dtype aliases: object/string->text, int->integer, float->float, datetime->datetime,
      bool->boolean, timedelta->duration, category->category), context, derivation_code,
      auto_notes_status. 404 if missing.
GET  /datasets/{id}/preview?rows=10   -> {columns, rows} first N rows (clamp 1..50), per-cell
     formatting (floats round 4, whole floats->int, NaN->null). 404/500.
GET  /datasets/{id}/sessions          -> sessions scoped to that dataset.
DELETE /datasets/{id}                 -> cascade delete (+recursive derived). 404; 409 dataset_in_use.
DELETE /datasets                      -> delete all + cascade.
PATCH  /datasets/{id}/context         -> {context} (<=4000). 400 context_too_long; 404.
POST   /datasets/{id}/describe        -> C30 trigger notes generation (auto_notes_status=pending).
POST   /datasets/{id}/re-derive       -> C25 re-run derivation_code vs current parents; clears
       stale. 400 not_derived; 404 parent_not_found; 400 re_derive_error.
POST   /datasets/{id}/clean           -> C24 NL cleaning PREVIEW: LLM generates pandas code,
       run on a copy, return code + before/after row/col counts + previews. 422 on exec error.
POST   /datasets/{id}/clean/apply     -> C24 apply code in place; rewrite CSV+Parquet, update counts.
POST /ask  {dataset_id?|dataset_ids?, question, session_id?, skip_clarification:false}
     Pre-flight C26 (unless skip) -> may return {type:"clarification", clarification_question,
     run_id, session_id} (thin run, status=clarification). Else resolve datasets (explicit or
     C19), create run(status=running), run agent, return {type:"answer", run_id, session_id,
     dataset_ids, derived_dataset_ids, datasets_used, selector_reasoning, answer_markdown,
     answer_html, iteration_count, tokens_input, tokens_output, status, is_best_effort, steps,
     suggested_questions, prompt_breakdown}. Errors: 404 dataset/session; 400 empty question /
     session mismatch / >20 turns / no datasets uploaded.
GET  /sessions                        -> all sessions (most-recently-updated first; turn_count, first_question).
GET  /sessions/{id}                   -> session + turns[] (each turn carries prompt_breakdown).
PATCH  /sessions/{id}/name            -> rename. DELETE /sessions/{id} and DELETE /sessions.
GET  /runs/current                    -> most recent run {run_id,status,iteration_count,max_iterations}
     (status "idle" when none). Used for live progress polling (~1/s).
GET  /stats/daily                     -> {date, model, tokens_input, tokens_output, query_count,
     context_limit} aggregated over today's completed runs (server-local day); context_limit
     from a hard-coded model table (unknown -> 128000). Always 200.
GET  /memory  / PATCH /memory         -> read/replace global persistent memory text; PATCH
     triggers C31 compression. Memory is injected into every plan_action prompt as authoritative.

=====================================================================
7. CAPABILITIES (implement all; reference IDs)
=====================================================================
C1  CSV upload                         C2  NL Q&A ReAct loop
C3  multi-turn conversation history    C4  inline Plotly charts (captured as JSON)
C6  Markdown->HTML answer rendering    C7  per-run token tracking
C8  expanded result display (100 rows/20 cols)   C9  session management UI
C10 duplicate detection (content hash + filename)
C11 multi-format ingest (CSV/TSV/TXT/JSON/Excel) C12 dataset context notes injected into prompts
C13 multi-file / folder drop           C14 multi-dataset querying
C15 dataset deletion + cascade         C16 notes file (.txt/.md) on upload
C17 staged client-side upload queue (concurrent, 409 inline resolve)
C18 token usage widget + daily stats   C19 automatic dataset selection (pre-flight)
C20 early exit / force-finalize        C21 multi-provider LLM (Gemini/OpenRouter/stub)
C22 query timer + live progress bar    C23 agent steps inspector
C24 NL data cleaning (preview + apply) C25 autonomous derived-dataset persistence (save_dataset),
    lineage tracking, staleness detection
C26 pre-flight clarification check      C27 session-scoped DataFrame cache (LRU ~1GB) + Parquet
    pre-conversion on upload
C29 live context-window display: sidebar context-budget BAR at rest (most recent single
    prompt size vs model limit) + per-turn prompt_breakdown in the steps inspector (NOT a
    separate sidebar breakdown). The breakdown ACCUMULATES across every LLM call in the run
    — each plan_action call plus an `auxiliary` bucket for the selector / suggestion /
    force-finalize calls — so `total_prompt` == run.tokens_input (the headline "tokens in");
    `last_prompt` holds the most recent single-call size (used by the bar); the panel also
    shows a "Tokens out" total. Keys: system_overhead, dataset_schemas, dataset_notes,
    memory, history, action_history, auxiliary, total_prompt, last_prompt.
C30 on-demand dataset notes generation  C31 semantic context compression (fact extraction)
C32 collapsible conversation turns (client-side, sessionStorage)

=====================================================================
8. UI (server-rendered base.html + index.html, vanilla JS, two tabs)
=====================================================================
Header: app name + tagline; "Project notes" button opens the global-memory modal; yellow
stub-mode banner when provider==stub. Two-panel shell with responsive breakpoints (<=960px
stack, <=640px stack cards).
Tabs: [Analyse] (default) and [Database].
ANALYSE tab:
- Sidebar: Sessions panel (list w/ name/first-question, turn count, relative time; checkbox
  bulk-select, click to resume, inline rename, +New, Delete selected, Clear all) and Token
  usage widget (model name; Last query In/Out/Cost and Today In/Out/Queries/Cost from
  /stats/daily; storage row = dataset count + total rows; client-side pricing table, "N/A"
  unknown). Sidebar also shows the C29 token-budget bar at rest.
- Datasets card titled "Tables": filter tabs All|Uploaded|Derived|This session; per row
  checkbox + filename + rows×cols + "▸ cols" toggle + 🧹 clean (uploaded only) + 🗑 delete;
  Derived/⚠Stale badges; Re-derive button on stale rows; inline delete-confirm (mentions N
  derived children); re-fetch datasets after each completed query to surface new derived sets.
- Upload card: drag-drop (files AND folders; reads _notes/context/readme as folder notes,
  <stem>.notes.txt per file), Choose files, staged file Map (rename, typed notes, attach
  notes file, remove), Upload N file(s) (<=3 concurrent; per-file ⏳/✓/✗; 409 -> "Use existing
  / Upload anyway").
- Conversation card: thread (role=log, aria-live=polite); per turn = question (blue) +
  timestamp/elapsed, optional ⚠Best effort badge, rendered Markdown answer (tables/bold/
  bullets/code/headings), iteration + token counts, "Datasets used" disclosure, Steps
  inspector (collapsible; per-step dark code block + result/error + Copy; red ✕ Error badge),
  2-3 follow-up suggestion chips, Export MD; clarification turns render amber w/ "Needs
  clarification" and re-submit with skip_clarification:true; C32 per-turn collapse/expand +
  Collapse all/Expand all (sessionStorage). Question textarea (Enter submit, Shift+Enter
  newline), Ask button (spinner/disabled while running), Stop button (AbortController),
  Progress row (elapsed timer + Step N/M bar from /runs/current; "Checking…" during C26).
DATABASE tab (session-scoped data universe):
- Header: session name + "N uploaded · M derived" + Clear database (danger, DELETE /datasets).
- Schema panel = full ER diagram in SVG (#lineage-svg) via renderERDiagram(datasets): each
  dataset is a table card listing columns (first 8 + "+N more", zebra, dtype color dot:
  number=blue text=purple date=amber boolean=green other=grey); uploaded=blue header,
  derived=green header + "derived" tag. Inferred FK crow's-foot edges via _erFkLinks(datasets)
  — THE single source of truth for both edges and the right-panel PK/FK badges: links columns
  ending _id shared by >=2 tables, a zip_code_prefix family normalized to a geolocation hub,
  exact-name shared specific columns, with a denylist of generic columns; canonical PK table
  chosen by filename. Edges are column-anchored orthogonal elbows routed around cards (dodgeX),
  crow's-foot at the many/FK end, tick at the one/PK end; derived edges dashed green; hover-only
  join-key pill. Force-directed layout + overlap resolution + aspect-aware x-stretch + auto-fit;
  controls Fit/+/- , drag-pan, wheel-zoom (0.5x–3x); click card -> selectDataset(id); hover card
  highlights its relationships and dims others to ~0.3.
- Table Description panel: filename + origin/stale badges; "rows × cols · FORMAT"; derived block
  (parent chips + collapsible derivation code); Keys block (PK/FK from _erFkLinks); columns table
  (Name|Type with PK/FK badges); Context notes textarea auto-saving via PATCH context + "Generate
  notes" (C30 poll); data preview (~10 rows); actions Clean/Re-derive/Delete.
- Dataset strip: horizontal chips (📄 uploaded / 🧩 derived + ⚠ stale), uploaded first; click =
  selectDataset.
Modals (all .modal-overlay, backdrop+Escape close, focus first element): memory-modal,
clean-modal, clear-sessions-modal, del-sel-sessions-modal, clear-all-datasets-modal,
del-sel-modal, clear-db-modal.
Accessibility: role=log + aria-live on thread; role=list/listitem; aria-label on icon buttons;
aria-modal+aria-labelledby on modals; role=progressbar + aria-valuenow on the bar; hidden
#aria-live region + announce(); type="button" on non-submit buttons.

=====================================================================
9. SUCCESS CRITERIA / GATES
=====================================================================
- Upload a CSV via browser in <5s; ask a question and get a correct text answer in <30s.
- ReAct loop correctly answers aggregation/filter/comparison questions on datasets up to 100MB.
- Runs fully offline except Gemini calls; single `uv run python -m data_analyst` start.
- Stub mode auto-engages without an API key, shows the banner, returns plausible stub answers.
Build phases (complete N before N+1; commit per unit; update reports/sessions/):
  P1 domain models + SQLite schema + alembic migration -> `uv run pytest tests/unit/` all pass,
     `uv run alembic current` shows a revision.
  P2 ReAct loop (stubbed) + FastAPI routes + Jinja2 UI -> `uv run pytest` all pass + golden-path
     smoke green, with ZERO env vars.
  P3 live Gemini integration + multi-turn -> end-to-end test with a real key.
  P4 charts + derived datasets + Database tab polish -> visual smoke test.

Start by scaffolding the repo + spec/, then implement P1. Ask me only if a requirement is
genuinely ambiguous; otherwise pick the obvious choice, state it, and proceed.
````
