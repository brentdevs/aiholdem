# AI Hold'em

A real-time AI Texas Hold'em poker arena where configurable AI players powered by different LLM models play continuously while you watch in real time.

## What is this?

AI Hold'em is a spectator poker platform. Configured AI players — each backed by a large language model through Ollama Cloud or OpenRouter — sit at a virtual table and play No-Limit Texas Hold'em. You watch the action unfold live in your browser.

- AI players receive a structured prompt with the full game state and respond with JSON actions plus reasoning
- Showdown pauses for 10 seconds between hands so you can review the results
- Blind levels escalate every 10 hands
- Players are eliminated at 0 chips; the game ends when one player remains
- The arena auto-pauses when no viewers are connected and resets after each game completes
- Leaderboard tracks AI player performance across games
- Player profiling classifies AI playing styles based on aggregated hand statistics

## Tech Stack

- **Backend**: Python 3.11, Quart, python-socketio, uvicorn (ASGI)
- **AI**: Ollama Cloud and OpenRouter providers (supports multiple LLM models simultaneously)
- **Database**: PostgreSQL 16 (leaderboard and profiling)
- **Frontend**: Vanilla HTML/JS with WebSocket for real-time updates
- **Containerization**: Docker + docker-compose

## Quick Start

### With Docker (recommended)

```bash
cp .env.example .env
# Edit .env and add your provider API key(s)
# Edit arenas.yaml to configure tables and their players

docker-compose up --build
```

Open [http://localhost:5000](http://localhost:5000) to watch the arena.

### Without Docker

Prerequisites: Python 3.11, Pipenv, PostgreSQL

```bash
cp .env.example .env
# Edit .env and add your provider API key(s) and DATABASE_URL
# Edit arenas.yaml to configure tables and their players

pipenv install
pipenv run python run.py
```

## Configuration

Arena definitions live in `arenas.yaml`. Secrets and deployment settings use
environment variables; see `.env.example`.

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENROUTER_API_KEY` | For OpenRouter players | Your OpenRouter API key |
| `OLLAMA_API_KEY` | For Ollama players | Your Ollama Cloud API key |
| `SECRET_KEY` | Yes | Application secret key (change in production) |
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `ARENA_PAUSE_ON_EMPTY` | No | Pause arena when no viewers connected (default: `true`) |
| `ARENA_CONFIG_FILE` | No | Alternate arena YAML path; defaults to the project's `arenas.yaml` |
| `PROFILING_ENABLED` | No | Enable AI player profiling (default: `true`) |
| `LOG_LEVEL` | No | `DEBUG` / `INFO` / `WARNING` / `ERROR` (default: `INFO`) |

### Arenas and players

Edit `arenas.yaml` to maintain tables and seat lineups:

```yaml
arenas:
  - id: arena
    name: AI Arena
    description: Mixed-provider matchup
    players:
      - backend: ollama
        model: deepseek-v4-pro
      - backend: openrouter
        model: google/gemini-2.5-flash
```

Add another entry under `arenas` for each table. List order determines arena
display order and player seat order. Each arena needs a unique lowercase,
hyphen-separated `id`, a name, a description, and **2 to 9 players**.
Backend names are case-sensitive: use `ollama` or `openrouter`. Model names are provider IDs.
The same model can play in multiple arenas, but duplicate backend/model pairs
within one arena are rejected. Keep API keys in `.env`, never in this file.

When a provider catalog is available, unknown or retired model IDs fail startup.
If the catalog cannot be fetched (for example, without API credentials), model
names are accepted with a warning; successful startup does not prove API availability.

Quote string values that YAML might interpret as numbers, booleans, nulls, or
dates. For example, use `id: "2024"`, `id: "on"`, or `model: "yes"` rather than
their unquoted forms. This applies to names and descriptions too.
YAML anchors and `<<` merges can reuse player mappings; explicit keys override
inherited keys. Duplicate explicit keys are rejected, including inside merged
mappings. Extra root-level sections are not allowed.

The first arena is the default table served at `/arena`. Arena cards use
`/arena/<id>` so IDs such as `arena`, `faq`, `leaderboard`, `models`, or `api`
still open the correct table, regardless of list order or fixed route names.
Use these canonical URLs when linking to a specific arena; shortlinks such as
`/faq` may instead open an existing fixed page.

Keep arena IDs stable when changing display names: URLs and persisted statistics
are scoped by ID. For migration:

- If you used `ARENA_LOBBIES`, set **every** ID to its previous name-derived slug,
  even if there was only one lobby. Lowercase the old name, replace runs outside
  `a-z` and `0-9` with `-`, then trim leading/trailing `-`. For example,
  `Main Arena` becomes `main-arena`, not `arena`.
- If you used only `ARENA_PLAYERS` or the built-in default, use `id: arena`.

This replaces `ARENA_LOBBIES` and `ARENA_PLAYERS`; remove those variables from
existing deployment environments. There is no environment-string fallback.
Missing files, malformed YAML, duplicate keys, unknown fields, and invalid
definitions fail startup rather than silently selecting another lineup.
Configuration errors identify the source file and, where applicable, the invalid field.

The file is loaded at startup; restart the application after edits. With Docker
Compose, restart the **web container** using `docker compose restart web`, not
just the application process inside it. This also refreshes a single-file bind
mount after an editor replaces the host file through an atomic save.

Compose mounts `./arenas.yaml` read-only at `/app/arenas.yaml`; the source file
must exist on the Docker host before starting the service. A missing source
fails the mount instead of creating a directory. YAML edits do not require an
image rebuild. To use another file with Compose, change the bind-mount source;
if setting `ARENA_CONFIG_FILE`, it must name a path available inside the container.

Without Docker, relative override paths resolve from the working directory;
the default file is resolved relative to the project, independent of working directory.
Leave `ARENA_CONFIG_FILE` unset to use the default; empty or whitespace-only
values are configuration errors, not a request to use the default.

### Model catalog API

`GET /models` returns:

- `models`: the deduplicated provider catalog plus players from all configured arenas.
- `arena_players`: the **first/default arena's** roster, not an all-arena aggregate.
- `lobbies`: every arena and its own ordered roster in `lobbies[].players`.

Use `lobbies[].players` for multi-arena clients. All arena rosters use the
startup configuration and change only after an application restart.

## Running Tests

```bash
# All Python tests
pipenv run pytest

# Unit tests only
pipenv run pytest tests/unit/

# Property-based tests only
pipenv run pytest tests/property/

# JS property tests (card renderer)
npx fast-check --test tests/property/test_card_renderer.js
```

## Project Structure

```
├── run.py                  # Entry point
├── arenas.yaml             # Arena definitions and ordered player lineups
├── app/
│   ├── __init__.py         # Quart app factory, SocketIO init
│   ├── routes.py           # HTTP routes: arena, leaderboard, FAQ
│   ├── events.py           # SocketIO event handlers: arena join/disconnect
│   ├── game/               # Core poker engine
│   │   ├── models.py       # Domain models: Card, Hand, Action, Phase, Pot
│   │   ├── players.py      # Player → AIPlayer hierarchy
│   │   ├── game_session.py # Hand lifecycle, betting, phase advancement
│   │   ├── dealer.py       # Deck shuffle, card dealing
│   │   ├── evaluator.py    # Hand evaluation and player ranking
│   │   └── pot_manager.py  # Bet tracking and side pot calculation
│   ├── ai/
│   │   ├── model_config.py       # Model registry and validated YAML arena loading
│   │   ├── ollama_player.py      # Ollama Cloud-backed AI player decisions
│   │   └── openrouter_player.py  # OpenRouter-backed AI player decisions
│   ├── arena/
│   │   └── arena_manager.py      # AI game loop, viewer tracking, broadcast
│   ├── leaderboard/        # Win/loss tracking across games
│   ├── profiling/          # AI player style classification
│   ├── static/arena.js     # Client-side arena JS
│   └── templates/          # HTML templates
├── tests/
│   ├── unit/               # Pytest unit tests
│   └── property/           # Hypothesis property-based tests
├── Dockerfile
├── docker-compose.yml
├── Pipfile / Pipfile.lock
└── .env.example
```

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
