# Repository Guidelines

## Project Structure & Module Organization

This is a Python 3.11 Quart + python-socketio Texas Hold'em arena. The entry point is `run.py`, which creates the ASGI app combining Quart (HTTP) and python-socketio (WebSocket).

- `app/game/`: core poker domain logic, session lifecycle, evaluator, dealer, and pot handling.
- `app/ai/`: LLM-backed player implementations for OpenRouter and Ollama Cloud.
- `app/arena/`: always-on arena manager, viewer tracking, background AI loop, and broadcasts.
- `app/leaderboard/` and `app/profiling/`: persistence, game results, and player profile aggregation.
- `app/templates/` and `app/static/`: vanilla HTML/JS arena, FAQ, and leaderboard UI.
- `tests/unit/`: focused pytest unit tests.
- `tests/property/`: Hypothesis tests and JS `fast-check` card renderer tests.

## Build, Test, and Development Commands

Use Pipenv for Python dependencies:

```bash
pipenv install --dev          # install Python dependencies
pipenv run python run.py      # run local app on port 5000
pipenv run pytest             # run all Python tests
pipenv run pytest tests/unit/ # run unit tests only
pipenv run pytest tests/property/ # run property tests only
npx fast-check --test tests/property/test_card_renderer.js
docker compose pull           # fetch the published GHCR image and PostgreSQL
docker compose up -d          # run with Docker; Compose does not build locally
```

The frontend has no build step.

## Coding Style & Naming Conventions

Follow existing Python style: 4-space indentation, type hints where useful, dataclasses/enums for structured game state, and small helper functions for parsing or state transitions. Keep AI provider code behind `AIPlayer` subclasses and preserve shared prompt/response behavior when adding providers.

Use `asyncio.sleep()` and `asyncio.ensure_future()` for arena background work. All arena loop methods are async coroutines.

## Testing Guidelines

Use pytest for unit coverage and Hypothesis for invariant/property coverage. Name Python tests `test_*.py` and keep module-specific tests near matching behavior, for example `tests/unit/test_game_session.py`.

When changing poker rules, pot distribution, state transitions, or AI parsing, add or update tests. Run at least the targeted tests plus `pipenv run pytest` before committing when feasible.

## Commit & Pull Request Guidelines

Recent commits use short imperative summaries, for example `Add Ollama Cloud arena players`. Keep commits focused on one coherent change.

Pull requests should include a brief description, test commands run, and any relevant UI screenshots for template/static changes. Mention configuration or migration impacts, especially new environment variables or dependency changes.

## Security & Configuration Tips

Keep secrets in `.env`; never commit real API keys. Update `.env.example` with placeholders when adding configuration. Required API keys include `OPENROUTER_API_KEY` for OpenRouter players and `OLLAMA_API_KEY` for Ollama Cloud players.

## Arena Configuration and Model Discovery

### Authoring arena YAML

- The file is **`arenas.yaml`**, not `arena.yaml`. Read the current file and `app/ai/model_config.py` before editing; see README's "Arenas and players" section for deployment and migration details.
- Edit the YAML, not provider classes or hardcoded model lists, to change seats. Do not replace existing arenas or lineups unless requested.
- The only root key is `arenas`, a nonempty list. Each arena requires `id`, `name`, `description`, and `players`; each player requires only `backend` and `model`. Unknown fields are rejected.
- Use unique lowercase, hyphen-separated IDs and preserve existing IDs when renaming or reordering tables: URLs and historical statistics depend on them. The first arena is the default; list order determines display and seat order.
- Configure **2–9 players** per arena. Backends are case-sensitive: `ollama` or `openrouter`. Reject duplicate `(backend, model)` pairs within a table; the same pair may appear in different arenas.
- Quote string values that YAML could interpret as numbers, booleans, nulls, or dates. Keep credentials in `.env`, never YAML. Do not add `ARENA_PLAYERS` or `ARENA_LOBBIES`; those formats were removed.

Example shape, not a permanent model registry; discover current IDs before choosing a lineup:

```yaml
arenas:
  - id: arena
    name: "AI Arena"
    description: "Ollama Cloud matchup"
    players:
      - backend: ollama
        model: "beam"
      - backend: ollama
        model: "kimi-k3"
```

### Discovering provider model IDs

Run these read-only public catalog commands from a shell with `curl` and `jq` (`nix develop` includes both):

```bash
# Ollama hosted catalog, not the local Ollama server
curl --fail --silent --show-error https://ollama.com/api/tags | jq -r '.models[].model'

# OpenRouter catalog; use the returned provider/model ID, not the display name
curl --fail --silent --show-error https://openrouter.ai/api/v1/models | jq -r '.data[].id'
```

- For `backend: ollama`, this application calls **`https://ollama.com`**, not localhost. `ollama list` shows the local server's catalog and is not authoritative for hosted availability.
- Use the exact hosted identifier returned by Ollama; do not automatically append `:cloud` or substitute a CLI alias. OpenRouter IDs retain their provider prefix and any returned suffix.
- Browse [Ollama Cloud models](https://ollama.com/search?c=cloud) or [OpenRouter models](https://openrouter.ai/models) to inspect candidates. Authoritative API guidance: [Ollama Cloud](https://docs.ollama.com/cloud) and [OpenRouter model listing](https://openrouter.ai/docs/api/api-reference/models/list-all-models-and-their-properties).
- Select text/chat-capable models. A catalog entry does not prove account access, remaining quota, or correct poker/JSON responses. If discovery fails, report availability as unverified rather than inventing a model ID.
- OpenRouter reasoning is selected automatically from raw catalog metadata: optional reasoning is disabled and mandatory reasoning uses the lowest supported effort. Do not add model-name exceptions or unsupported YAML fields. Preserve raw catalog discovery: the pinned SDK drops the catalog's newer reasoning metadata. Missing capabilities leave provider defaults in effect, which may cost more.
- Catalog listing does not need an inference request. Do not start paid model turns merely to enumerate models, and never print API keys or full credential-bearing environments.

### Validating and applying changes

Run the real loader without starting game loops:

```bash
pipenv run python - <<'PY'
from app.ai.model_config import load_lobby_configs

for arena in load_lobby_configs():
    print(arena.lobby_id, [(player.backend, player.model) for player in arena.players])
PY
```

- To validate a different file, set `ARENA_CONFIG_FILE` to its nonblank path. Leave it unset for the project's default file; relative overrides resolve from the working directory.
- `pipenv run` loads `.env`. The loader checks model IDs against provider catalogs when the corresponding API keys are available; failed catalog discovery only warns, so distinguish structural validation from verified provider availability.
- Preserve unrelated config, verify requested seat order, and report exactly what was checked. For loader/schema changes, run `pipenv run pytest tests/unit/test_model_config.py` and the full suite; for lineup-only edits, exercise the loader and, when appropriate, startup without triggering paid turns.
- Configuration changes require a restart. With Compose, the host YAML must exist before startup; use `docker compose restart web` after edits, including atomic-save replacements. Use `/arena/<id>` for links to a specific table.
