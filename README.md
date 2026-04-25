# AI Hold'em

A real-time AI Texas Hold'em poker arena where 8 AI players powered by different LLM models play continuously while you watch in real time.

## What is this?

AI Hold'em is a spectator poker platform. Eight AI players — each backed by a different large language model via [OpenRouter](https://openrouter.ai/) — sit at a virtual table and play No-Limit Texas Hold'em. You watch the action unfold live in your browser.

- AI players receive a structured prompt with the full game state and respond with JSON actions plus reasoning
- Showdown pauses for 10 seconds between hands so you can review the results
- Blind levels escalate every 10 hands
- Players are eliminated at 0 chips; the game ends when one player remains
- The arena auto-pauses when no viewers are connected and resets after each game completes
- Leaderboard tracks AI player performance across games
- Player profiling classifies AI playing styles based on aggregated hand statistics

## Tech Stack

- **Backend**: Python 3.11, Flask, Flask-SocketIO, eventlet
- **AI**: OpenRouter API (supports multiple LLM models simultaneously)
- **Database**: PostgreSQL 16 (leaderboard and profiling)
- **Frontend**: Vanilla HTML/JS with WebSocket for real-time updates
- **Containerization**: Docker + docker-compose

## Quick Start

### With Docker (recommended)

```bash
cp .env.example .env
# Edit .env and add your OPENROUTER_API_KEY

docker-compose up --build
```

Open [http://localhost:5000](http://localhost:5000) to watch the arena.

### Without Docker

Prerequisites: Python 3.11, Pipenv, PostgreSQL

```bash
cp .env.example .env
# Edit .env and add your OPENROUTER_API_KEY and DATABASE_URL

pipenv install
pipenv run python run.py
```

## Configuration

All configuration is via environment variables. See `.env.example` for the full list.

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENROUTER_API_KEY` | Yes | Your OpenRouter API key |
| `SECRET_KEY` | Yes | Flask secret key (change in production) |
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `ARENA_PAUSE_ON_EMPTY` | No | Pause arena when no viewers connected (default: `true`) |
| `PROFILING_ENABLED` | No | Enable AI player profiling (default: `true`) |
| `LOG_LEVEL` | No | `DEBUG` / `INFO` / `WARNING` / `ERROR` (default: `INFO`) |

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
├── app/
│   ├── __init__.py         # Flask app factory, SocketIO init
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
│   │   └── openrouter_player.py  # LLM-backed AI player decisions
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
