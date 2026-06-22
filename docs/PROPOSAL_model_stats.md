# Proposal: Per-Model Historical Stats & Charts

## What you already have

Your codebase is already collecting almost everything you'd want for historical stats — it's just not surfaced anywhere.

**Existing PostgreSQL store** (`DATABASE_URL`, raw `psycopg2`, no ORM):
- `profiling.games` — per-game row with `started_at`/`ended_at` timestamps
- `profiling.game_results` — `finish_position`, `net_profit` per player per game
- `profiling.hands` — per-hand `played_at` timestamp
- `profiling.hand_players` — VPIP/PFR/3bet/cbet/WTSD boolean flags, `net_result`
- `profiling.errors` — `occurred_at` per error (your per-hand error history already exists here)
- `leaderboard` — cumulative rollup (no timestamps)

The arena already writes to all of these on every game (`arena_manager.py:319-398`) and every hand (`game_session.py:596-685`). Nothing is ever pruned.

**What's missing**: no per-player detail route, no historical JSON endpoints, no charts, no per-game latency/failure persistence (only cumulative in `leaderboard`).

## Opinion on InfluxDB

**Don't add it.** Here's why:

1. **You already have the time-series data in Postgres.** `games.started_at`, `hands.played_at`, `errors.occurred_at` are timestamps on every row. A `GROUP BY date_trunc('day', g.started_at)` query gives you daily win rate, placements, error counts — that's exactly the "error rates per day, wins, placements" you asked for, with zero new infrastructure.

2. **A second database for a hobby arena is real overhead** — another container in `docker-compose`, another connection pool, another set of env vars, retention policies, and a second graceful-degradation path to mirror the existing one in `LeaderboardService`/`ProfilingService`. You'd be duplicating the write path in `_reset_after_complete()` (`arena_manager.py:329-382`).

3. **The volume is tiny.** A game is ~30-80 hands, each a handful of rows. You're not writing millions of points/sec — the use case InfluxDB is built for. Postgres with an index on `games.started_at` and `hands.played_at` will be instant at any scale this arena will reach.

4. **Player-style stats (VPIP/PFR/AF/WTSD) are relational, not measurement-series data.** They're computed by `StatAggregator` from joins across `hands`+`hand_players`. Shoving them into InfluxDB would mean either re-querying Postgres anyway, or duplicating the flag computation into the Influx write path. InfluxDB's tag/field model is awkward for this.

InfluxDB would only earn its keep if you wanted sub-second, high-cardinality operational metrics (per-request latency histograms for thousands of models). For "graphs per model per day" it's the wrong tool, and you'd be maintaining two DBs to get one feature.

## Proposed solution (all on existing Postgres)

**1. Add a per-game stats snapshot table** so latency/failure history is recoverable per game (currently only kept cumulatively in `leaderboard`):

```sql
CREATE TABLE game_model_stats (
    game_id      TEXT REFERENCES games(game_id),
    model_id     TEXT,
    api_calls    INTEGER,
    api_failures INTEGER,
    latency_ms   BIGINT,
    finish_pos   INTEGER,
    PRIMARY KEY (game_id, model_id)
);
```

Written alongside the existing leaderboard flush in `arena_manager.py:329-355`. This is the one small schema change — everything else queries tables that already exist.

**2. Add service methods** in `ProfilingService` (it owns the timestamped tables):
- `get_model_daily_stats(model_id, days)` — `date_trunc('day')` over `game_results ⋈ games`, returning daily `{date, games, wins, avg_finish, net_profit, errors}`
- `get_model_error_history(model_id, days)` — daily error counts from `errors ⋈ hands ⋈ game_results`
- `get_model_style_trends(model_id, buckets)` — rolling VPIP/PFR/AF over time buckets
- `get_model_hand_samples(model_id, limit)` — recent hands for a scatter/detail view

**3. Add routes** in `app/routes.py`:
- `GET /model/<model_id>` → renders a new `model_detail.html`
- `GET /api/model/<model_id>/daily` → JSON for the charts
- `GET /api/model/<model_id>/style` → JSON style trends
- Make leaderboard rows clickable (the table is already keyed by `model_id`).

**4. Frontend** — add a client-side chart lib via CDN (the project already loads Socket.IO via CDN, so this matches convention). Two good fits:
- **Chart.js** (lightweight, ~65KB, simple line/bar/doughnut — covers daily win rate, placements, error rate, VPIP/PFR over time)
- **Apache ECharts** (~1MB, richer, only if you want interactive zooming/brushing)

I'd go Chart.js for the arena's aesthetic. No Python chart deps, no pandas, no build step — consistent with the existing "no build step" frontend.

**5. Retention** (optional, separate concern) — add a nightly `DELETE FROM hands WHERE played_at < NOW() - INTERVAL '90 days'` if you ever care about growth. Not needed for the feature, just flagging that unbounded growth is a pre-existing condition, not something this change introduces.

## TL;DR

The data is already in Postgres with timestamps. Add one small snapshot table for per-game latency/failures, ~4 query methods, 2 routes, one new template, and Chart.js from CDN. Skip InfluxDB — it'd double your infra for a dataset Postgres handles trivially, and the relational player-style stats don't fit its model well.

## Key file references

- `app/leaderboard/service.py:36-48` — `leaderboard` schema (cumulative only, no timestamps)
- `app/leaderboard/service.py:56-94` — `record_game_results()` upsert
- `app/profiling/service.py:66-154` — six profiling tables incl. `games`, `hands`, `hand_players`, `game_results`, `errors`
- `app/profiling/stat_aggregator.py:33-50` — rolling-window VPIP/PFR/AF/WTSD query
- `app/profiling/style_classifier.py:27-59` — VPIP/AF grid → style label
- `app/arena/arena_manager.py:319-398` — game-end result writing to both services
- `app/arena/arena_manager.py:411-463` — game-start recording
- `app/game/game_session.py:596-685` — per-hand recording
- `app/game/game_session.py:80` — `profiling_game_id` (join key across tables)
- `app/routes.py:59-64` — only existing JSON endpoint (`/api/leaderboard`)
- `app/__init__.py:48-62` — service wiring
- `Pipfile:9` — `psycopg2-binary` (only DB lib; no pandas/sqlalchemy)
- `app/templates/leaderboard.html:197-208` — current flat leaderboard table