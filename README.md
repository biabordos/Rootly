# Rootly

**AI-powered incident triage agent for IT operations.**
Rootly receives a monitoring alert, investigates it through a multi-agent ReAct loop (CMDB + logs + historical incidents), and produces a structured diagnosis package ready for L2 escalation — in seconds instead of 15-30 minutes of manual work.

---

## Architecture

Rootly runs four LLM agents on a single LangGraph `StateGraph`, coordinated through shared structured state and checkpointed to SQLite:

```
Alert
  │
  ▼
┌──────────────┐
│  Orchestrator │──── routes to ────┬── CMDB Agent ────────┐
│  (no tools)   │                   ├── Log Agent ─────────┤
│               │◄── findings ──────┘                      │
│               │──── routes to ────── Synthesis Agent ────┤
└──────────────┘                         │                 │
                                   submit_diagnosis        │
                                         │                 │
                                    ┌────▼─────┐           │
                                    │ Guardrail │───rejected──► back to Orchestrator
                                    └────┬─────┘
                                         │ accepted
                                  ┌──────▼───────┐
                                  │  Escalation   │
                                  │    Policy     │
                                  └──────┬───────┘
                                         │
                              ┌──────────▼──────────┐
                              │  urgent?  human     │
                              │  approval (pause)   │
                              └──────────┬──────────┘
                                         │
                                        END
```

### Agents

| Agent | Tools | Role |
|---|---|---|
| **Orchestrator** | `route_to_specialist` | Reads the alert + all findings so far, decides which specialist runs next. Holds no investigation tools. |
| **CMDB Agent** | `cmdb_lookup` | Looks up components in the Configuration Management Database. Reports dependency chains (`depends_on`), blast radius (`depended_by`), owner team, deploy/config metadata. |
| **Log Agent** | `log_search` | Searches service logs in a time window. Classifies each service as **LOCAL** (origin of failure), **RELAY** (reporting upstream failure), or **HEALTHY**. Fills `investigated_services`, which the guardrail's origin and reachability checks depend on. |
| **Synthesis Agent** | `similar_incidents_search`, `submit_diagnosis` | Searches historical incidents via RAG, then assembles and submits the final diagnosis package. |

Each agent gets its own system prompt and a focused briefing built from structured state — no agent sees the full message history. The shared `messages` log is an audit trail; the guardrail reads the synthesis agent's `submit_diagnosis` call off it.

### Guardrail

Every `submit_diagnosis` call passes through `check_package()` before being accepted:

- **CMDB existence** — `affected_component` and `critical_dependencies` must be real CMDB components
- **Log evidence** — cited lines must match actual entries in the log corpus, attributed to the correct service
- **Origin check** — if the affected component's error logs blame a dependency, that dependency's logs must have been searched first
- **Transitive reachability** — `affected_component` must be reachable from the alerted service through a chain of investigated services in the CMDB dependency graph
- **Stop-too-early check** — blaming the alerted service itself requires searching at least one of its dependencies' logs
- **Historical search** — `similar_incidents_search` must have been called
- **Semantic checks** — confidence > 0.85 needs evidence from 2+ services and at least one similar incident; critical severity needs blast radius >= 2; evidence must mention the affected component; cited lines must trace back to actual `log_search` results from this run
- **Circuit breaker** — after 3 rejections, force-accepts with confidence capped at 0.30

A rejected package goes back to the **orchestrator** (not synthesis), so the log agent can fill the gaps the guardrail identified.

### Escalation policy

Deterministic, no LLM call. Four independent triggers for `escalate_urgent_needs_approval`:

1. Critical severity
2. Blast radius >= 2 CMDB dependents
3. High severity on a business-critical component
4. Confidence < 0.70

Confident low/medium diagnoses are auto-resolved. Everything else gets a normal escalation.

### Human-in-the-loop

Urgent escalations pause the graph with `interrupt()`. State is checkpointed to SQLite (`.checkpoints/rootly.sqlite`), so a human can approve or downgrade later — from the CLI, the Streamlit UI, or the API, even from a different process.

---

## Tools

| Tool | Source | Description |
|---|---|---|
| `cmdb_lookup` | `src/tools/cmdb_lookup.py` | Component metadata + dependencies from mock CMDB (11 components) |
| `log_search` | `src/tools/log_search.py` | Filter logs by service, time window, optional level (201 log entries across services) |
| `similar_incidents_search` | `src/tools/incident_search.py` | RAG over historical incidents. Primary: ChromaDB + all-MiniLM-L6-v2 embeddings. Fallback: BM25 keyword ranking |

All tools operate on static mock data (`data/*.json`). Tool dispatch goes through `ToolRegistry` — a call to a tool outside an agent's role is refused as an error observation.

---

## Mock data

| Dataset | File | Size |
|---|---|---|
| Alerts | `data/alerts.json` | 10 scenarios (latency, errors, unavailability, resource exhaustion) |
| CMDB | `data/cmdb.json` | 11 components with dependencies, criticality tiers, deploy timestamps |
| Logs | `data/logs.json` | 201 log entries across services, with timestamps and severity levels |
| Historical incidents | `data/incidents.json` | 10 resolved incidents for RAG search |

Each scenario has a ground truth (expected root component, involved dependencies, matching historical incident) documented in [`docs/MOCK_DATA_README.md`](./docs/MOCK_DATA_README.md).

---

## Interfaces

### Streamlit UI

Full web interface with:
- **Landing page** with animated botanical texture
- **Alerts page** — select an alert, run the agent, watch the live investigation trace (Thought / Action / Observation steps), view the diagnosis package, export as JSON/Markdown
- **System Map** — interactive CMDB dependency graph visualization, with node health derived from log evidence
- **Human approval** — approve or downgrade urgent escalations directly from the UI
- **Day/Night theme** toggle
- **Responsive layout** (desktop + mobile)

### CLI (Rich)

```bash
python run_cli.py                     # list scenarios
python run_cli.py ALRT-001            # live trace + diagnosis
python run_cli.py ALRT-001 --verbose  # include full tool observations
python run_cli.py ALRT-001 --export   # save to exports/
```

Resume a paused escalation:
```bash
python run_cli.py --resume <thread_id> --decision approve
python run_cli.py --resume <thread_id> --decision downgrade --note "known load test"
```

### FastAPI

| Endpoint | Method | Description |
|---|---|---|
| `/alerts` | GET | List all alert scenarios |
| `/diagnose` | POST | Start a diagnosis (runs in background, returns `thread_id`) |
| `/diagnose/{thread_id}` | GET | Poll for results |
| `/diagnose/{thread_id}/resume` | POST | Approve or downgrade an urgent escalation |

Swagger UI at `http://localhost:8000/docs`.

---

## Quickstart

### Local

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux
pip install -r requirements.txt

cp .env.example .env          # set MISTRAL_API_KEY
```

A free Mistral API key is available at [console.mistral.ai](https://console.mistral.ai) (Experiment plan, phone verification required). The default model is `codestral-latest`.

```bash
python run_cli.py ALRT-001                   # run one scenario
streamlit run src/ui/streamlit_app.py        # start the web UI
python -m pytest                             # offline tests (no API key needed)
```

### Docker

```bash
cp .env.example .env   # set MISTRAL_API_KEY
docker compose up --build
```

| Service | URL |
|---|---|
| Streamlit UI | http://localhost:8501 |
| FastAPI (Swagger) | http://localhost:8000/docs |
| Phoenix (traces) | http://localhost:6006 |

Requires Docker Compose >= 2.24.

---

## Evaluation

```bash
python evaluate.py                        # smart retry (up to 3 retries per scenario)
python evaluate.py --runs 10              # statistical mode: 10 runs per scenario
python evaluate.py --runs 10 ALRT-005     # single scenario, 10 runs
python evaluate.py --json report.json     # machine-readable output
```

- **Smart retry** stops at the first correct root component and reports which attempt succeeded
- **`--runs N`** never short-circuits; reports success rates per scenario — the honest measure since routing is LLM-decided
- Both modes write `EVAL_RESULTS.md` and compare against manual baselines (15-30 min per incident)

The first run downloads the embedding model (~80 MB) and builds the vector index in `.chroma/`.

---

## Observability

Three tracing backends, all optional:

| Backend | Config | Dashboard |
|---|---|---|
| **Arize Phoenix** | `PHOENIX_COLLECTOR_ENDPOINT` in `.env` | http://localhost:6006 (auto-started with Docker) |
| **LangSmith** | `LANGSMITH_TRACING=true` + `LANGSMITH_API_KEY` | smith.langchain.com |
| **AI Observability SDK** | `AI_OBSERVABILITY_API_KEY` + `AI_OBSERVABILITY_ENDPOINT` | Custom endpoint |

Without any tracing env vars, tracing is disabled and nothing changes.

---

## Configuration

All configuration lives in `.env`:

| Variable | Default | Description |
|---|---|---|
| `MISTRAL_API_KEY` | (required) | Mistral API key |
| `MISTRAL_MODEL` | `codestral-latest` | Model to use for all agents |
| `MAX_REACT_STEPS` | `32` | Total model turns budget per run (across all 4 agents) |
| `LOG_LEVEL` | `INFO` | Python logging level |

---

## Project structure

```
Rootly/
├── data/                           # alerts, CMDB, logs, historical incidents (JSON)
├── src/
│   ├── models/schemas.py           # Pydantic schemas (Alert, DiagnosisPackage, etc.)
│   ├── data_loader.py              # Cached, validated dataset loading
│   ├── tools/                      # cmdb_lookup, log_search, incident_search (RAG)
│   ├── agent/
│   │   ├── graph.py                # LangGraph StateGraph, run_diagnosis(), resume_diagnosis()
│   │   ├── graph_nodes.py          # Node functions, guardrail, model helpers
│   │   ├── graph_state.py          # RootlyState + RootlyContext schemas
│   │   ├── system_prompt.py        # Per-agent prompts + briefing formatters
│   │   ├── escalation_policy.py    # Deterministic escalation rules
│   │   ├── tool_registry.py        # Tool dispatch with role enforcement
│   │   ├── report.py               # JSON/Markdown export
│   │   ├── observability.py        # Phoenix + LangSmith + AI Obs SDK setup
│   │   ├── specialists/
│   │   │   ├── cmdb_agent.py       # CMDB analysis specialist
│   │   │   ├── log_agent.py        # Log analysis specialist
│   │   │   └── synthesis_agent.py  # Diagnosis synthesis specialist
│   │   └── mistral_compat.py       # Mistral API compatibility patches
│   ├── api/main.py                 # FastAPI endpoints
│   └── ui/
│       ├── streamlit_app.py        # Streamlit UI (landing, alerts, map, about)
│       └── system_map.py           # CMDB dependency graph visualization
├── tests/                          # Offline tests (tool, escalation, multi-agent graph)
├── docs/
│   ├── MOCK_DATA_README.md         # Scenario design and ground truth
│   └── ROADMAP.md                  # Implementation roadmap
├── run_cli.py                      # CLI entry point (Rich)
├── evaluate.py                     # Evaluation harness (smart retry + statistical mode)
├── docker-compose.yml              # 3 services: API, UI, Phoenix
├── Dockerfile
├── requirements.txt
└── .env.example
```

---

## Tech stack

| Layer | Technology |
|---|---|
| LLM | Mistral (via `langchain-mistralai`) |
| Agent framework | LangGraph (`StateGraph`, checkpointing, `interrupt()`) |
| RAG | ChromaDB + sentence-transformers (all-MiniLM-L6-v2), BM25 fallback |
| API | FastAPI + Uvicorn |
| UI | Streamlit (custom HTML/CSS, Streamlit Components v2) |
| CLI | Rich |
| Data validation | Pydantic |
| Tracing | OpenTelemetry, Arize Phoenix, LangSmith |
| Tests | pytest |
| Containerization | Docker Compose |

---

## Known limitations

- **LLM routing** — the orchestrator's routing is LLM-decided and can make mistakes. Deterministic guards catch invalid specialists, duplicate CMDB lookups, and redundant log searches. After 2 failed routing attempts, a deterministic fallback takes over.
- **Step budget** — multi-agent costs 9-25 model turns per scenario (median 12) vs. 4-8 for the former single agent. The last 3 turns are reserved for synthesis so the run always attempts a diagnosis.
- **Rate limiting** — Mistral's free tier allows ~1 req/s. Backoff (2s, 4s, 8s, 16s) absorbs 429s. Budget ~15 min for a full evaluation pass.
- **Shared SQLite** — `.checkpoints/` and `.chroma/` are shared between the API and UI containers. Safe for demo scale; production should route Streamlit through the API.
- **Phoenix persistence** — the Phoenix container has no persistent volume; traces are lost on restart.
- **RAGAS evaluation** — uses `codestral-latest` as the NLI judge (a code model, not a reasoning one). Scores are indicative.

---

## Documentation

- [`docs/MOCK_DATA_README.md`](./docs/MOCK_DATA_README.md) — the 10 scenarios, CMDB topology, ground truth
- [`docs/ROADMAP.md`](./docs/ROADMAP.md) — phased implementation plan
