# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repo overview

NVIDIA AI-Q Blueprint — a research-agent system built on the **NeMo Agent Toolkit (NAT)** and **LangGraph / Deep Agents**. Configuration-driven: YAML files in `configs/` wire LLMs, tools, and agents together; Python plugins in `src/aiq_agent/` and `frontends/`/`sources/` register implementations with NAT.

Default branch is `develop` (not `main`); CI gates on `develop`. Python 3.11–3.13, package manager is **uv** (workspace-based).

## Environment & install

```bash
./scripts/setup.sh                # one-shot: uv venv, uv sync --dev, install all workspace pkgs, pre-commit
source .venv/bin/activate
cp deploy/.env.example deploy/.env  # then fill in NVIDIA_API_KEY, optional TAVILY_API_KEY / SERPER_API_KEY
```

`deploy/.env` is sourced by every `start_*.sh` script and by `dotenv -f deploy/.env run <cmd>`. Never put secrets anywhere else.

## Common commands

| Task | Command |
|------|---------|
| Run all tests | `./scripts/dev.sh test` (or `uv run pytest`) |
| Run a single test | `uv run pytest tests/aiq_agent/path/to/test_x.py::test_name -v` |
| Lint (ruff + yapf, no edits) | `./scripts/dev.sh lint` |
| Format | `./scripts/dev.sh format` |
| Pre-commit suite | `./scripts/dev.sh pre-commit` then `pre-commit run --all-files` |
| CLI agent (interactive) | `./scripts/start_cli.sh [--verbose] [--config_file <path>]` |
| Web stack (backend + UI) | `./scripts/start_e2e.sh` (API :8000, UI :3000) |
| API server only | `./scripts/start_server_in_debug_mode.sh --config_file configs/config_web_default_llamaindex.yml` |
| Direct NAT invocation | `dotenv -f deploy/.env run nat run --config_file <yml> --input "..."` |
| Eval harness | `dotenv -f deploy/.env run nat eval --config_file <yml>` |
| Docs build | `make -C docs html` |
| Coverage gate (CI) | `pytest --cov=src/aiq_agent --cov-fail-under=65` |

CI (`.github/workflows/ci.yml`) on PRs to `develop` runs ruff (check + format), the non-skipped pre-commit hooks, pytest with 65% coverage floor, helm lint, and `ci/scripts/test_scripts.sh`. Match locally before pushing.

## Architecture

### Agent graph (LangGraph state machine)

`src/aiq_agent/agents/` — one subpackage per agent, all NAT-independent classes that take dependencies via constructor:

- **`chat_researcher/`** — top-level orchestrator. Builds a `StateGraph` that runs: intent classification → (meta reply) or → clarifier → shallow research → optional escalation → deep research. Calls into the other agents via injected `*_fn` callables, not direct imports of their classes.
- **`clarifier/`** — human-in-the-loop clarification + plan approval before deep research starts.
- **`shallow_researcher/`** — bounded tool-calling researcher for fast cited answers.
- **`deep_researcher/`** — multi-phase planning + research loops + citation management using the `deepagents` library (planner, researcher subagents, custom middleware in `custom_middleware.py`).

Each agent directory has the same shape: `agent.py` (the framework-agnostic class), `register.py` (the NAT `@register_function` wrappers that plug into config YAML), `models/`, `prompts/`. Prompts are loaded via `aiq_agent.common.load_prompt` / `render_prompt_template` from sibling files — don't inline prompt strings.

### Plugin / config wiring

`pyproject.toml` exposes these as NAT entry points under `[project.entry-points."nat.plugins"]`:

```
aiq_chat_researcher       → aiq_agent.agents.chat_researcher.register
aiq_shallow_researcher    → aiq_agent.agents.shallow_researcher.register
aiq_deep_researcher       → aiq_agent.agents.deep_researcher.register
aiq_clarifier             → aiq_agent.agents.clarifier.register
aiq_fastapi_extensions    → aiq_agent.fastapi_extensions.register
aiq_data_source_registry  → aiq_agent.common.data_source_registry
```

A YAML in `configs/` references these by their NAT `_type` name (e.g. `_type: chat_deepresearcher_agent`, `_type: deep_research_agent`, `_type: data_source_registry`). Adding a new agent means: add the class, add a `register.py` with `@register_function(config_type=...)`, expose it as an entry point in `pyproject.toml`, then reference it from a config.

### Data source registry

`configs/*.yml` defines a `data_sources` function (`_type: data_source_registry`) listing search/RAG tools. Agents with no explicit `tools:` field auto-inherit every tool from that registry; use `exclude_tools:` to specialize. The registry also drives UI toggles and per-message source filtering. New tools live under `sources/<name>/` as workspace packages (see `sources/tavily_web_search`, `sources/google_scholar_paper_search`, `sources/knowledge_layer`).

### Common utilities

`src/aiq_agent/common/` is the shared layer:
- `LLMProvider` / `LLMRole` — role-based LLM selection (`ORCHESTRATOR`, `RESEARCHER`, `PLANNER`, …) so agents request "the planner LLM" instead of a model name.
- `get_checkpointer(db)` — returns a process-wide cached LangGraph checkpointer. SQLite for `*.db` paths, Postgres for `postgresql://` DSNs; pick via `${AIQ_CHECKPOINT_DB}` in YAML.
- `citation_verification` — session-scoped `SourceRegistry` for catalog/numbering and `verify_citations` / `sanitize_report` for deep-research output. Source parsers are pluggable via `register_source_parser`.
- `data_sources.py`, `data_source_registry.py` — the registry plumbing.
- `tool_validation.py`, `config_validation.py` — startup-time validation that referenced tools/configs exist.

Import these from `aiq_agent.common` (the package `__init__` re-exports everything in `__all__`), not from submodules.

### Frontends

`frontends/` contains separate workspace packages:
- **`cli/`** — `aiq-research` console entry point used by `start_cli.sh`.
- **`aiq_api/`** — FastAPI plugin (`AIQAPIWorker`) that mounts `/v1/jobs/async` (agent-agnostic async jobs, SSE streaming, event replay via `event_store`, reconnect via `websocket_reconnect.py`) and the Knowledge API (`/v1/collections`, `/v1/documents`). This is what `nat serve` exposes when a web config sets `front_end:`.
- **`debug/`** — debug console mounted at `/debug` by the API.
- **`ui/`** — Next.js web UI.
- **`benchmarks/{freshqa,deepresearch_bench,deepsearch_qa,intent_classifier}/`** — each is its own workspace package with its own configs and `nat eval` flow.

Async jobs run via Dask: local cluster auto-created by NAT in dev; for prod set `NAT_DASK_SCHEDULER_ADDRESS` and `NAT_JOB_STORE_DB_URL`. Job store defaults to `.tmp/job_store.db` (SQLite) — Postgres in prod.

### Verbose / tracing

`AIQ_VERBOSE=true` (set by `start_cli.sh --verbose`) attaches `VerboseTraceCallback` to LLM calls. Configs may enable LangSmith or Phoenix tracing under `general.telemetry.tracing`; for Phoenix runs you must `phoenix serve` separately before `nat eval`.

## Conventions worth knowing

- **uv workspace.** `pyproject.toml` declares `[tool.uv.workspace] members = ["sources/*", "frontends/aiq_api", "frontends/cli", "frontends/debug", "frontends/benchmarks/freshqa", "frontends/benchmarks/deepsearch_qa"]`. `uv sync --dev` resolves the whole graph; `uv pip install --no-deps -e ./frontends/<x>` adds a single member without re-resolving (this is what `setup.sh` does).
- **Ruff + yapf, line length 120.** Imports use `force-single-line = true` and `combine-as-imports = true` (isort via ruff). First-party packages: `aiq`, `nat`, `nat_*`, `_utils`.
- **`asyncio_mode = "auto"`, session-scoped event loop.** Don't add `@pytest.mark.asyncio` decorators — they're implicit.
- **Coverage floor is 65%** (CI fails below).
- **Pre-commit `detect-secrets`** runs against `.secrets.baseline`. If you add a new flagged string, update the baseline rather than silencing the hook.
- **DCO required.** Sign commits with `-s` (`Signed-off-by: …`). PRs without sign-off are rejected.
- **Checkpoint and job-store DBs live at repo root** (`checkpoints.db`, `jobs.db`) during local dev — they're gitignored runtime artifacts, not source.

## Configs cheat sheet

| Config | Use when |
|--------|----------|
| `config_cli_default.yml` | CLI (`start_cli.sh`) — web search, no RAG, HITL clarifier + plan approval. |
| `config_web_default_llamaindex.yml` | Web stack default — LlamaIndex knowledge layer + web search. |
| `config_web_frag.yml` | Web + external Foundational RAG server. Helm default. |
| `config_frontier_models.yml` | Frontier orchestrator/planner (needs `OPENAI_API_KEY`), open researcher. |
| `config_openai.yml` / `config_azure_openai.yml` | OpenAI / Azure OpenAI backends. |

The web/server scripts validate that the chosen config has a `front_end:` block — CLI configs intentionally don't and will be rejected by `start_server_in_debug_mode.sh`.
