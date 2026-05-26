# Repository Guidelines

## Project Structure & Module Organization

This is a Python 3.11 Flask + Flask-SocketIO Texas Hold'em arena. The entry point is `run.py`, which applies eventlet monkey patching before creating the app.

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
docker-compose up --build     # run with Docker
```

The frontend has no build step.

## Coding Style & Naming Conventions

Follow existing Python style: 4-space indentation, type hints where useful, dataclasses/enums for structured game state, and small helper functions for parsing or state transitions. Keep AI provider code behind `AIPlayer` subclasses and preserve shared prompt/response behavior when adding providers.

Use `eventlet.sleep()` and `socketio.start_background_task()` for arena background work. Do not introduce blocking sleeps in the eventlet loop.

## Testing Guidelines

Use pytest for unit coverage and Hypothesis for invariant/property coverage. Name Python tests `test_*.py` and keep module-specific tests near matching behavior, for example `tests/unit/test_game_session.py`.

When changing poker rules, pot distribution, state transitions, or AI parsing, add or update tests. Run at least the targeted tests plus `pipenv run pytest` before committing when feasible.

## Commit & Pull Request Guidelines

Recent commits use short imperative summaries, for example `Add Ollama Cloud arena players`. Keep commits focused on one coherent change.

Pull requests should include a brief description, test commands run, and any relevant UI screenshots for template/static changes. Mention configuration or migration impacts, especially new environment variables or dependency changes.

## Security & Configuration Tips

Keep secrets in `.env`; never commit real API keys. Update `.env.example` with placeholders when adding configuration. Required API keys include `OPENROUTER_API_KEY` for OpenRouter players and `OLLAMA_API_KEY` for Ollama Cloud players.
