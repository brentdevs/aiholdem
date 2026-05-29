// ---------------------------------------------------------------------------
// arena.js — AI Spectator Arena client
// ---------------------------------------------------------------------------
// Reuses the same rendering logic as game.js but:
//   - Connects with `join_arena` instead of `join_session`
//   - Listens for `arena_state` instead of `game_state`
//   - Has no action buttons or lobby controls
//   - Adds LiveReviewPanel, InterHandCountdown, and winner highlighting

// ---------------------------------------------------------------------------
// Socket setup — Task 8.1
// ---------------------------------------------------------------------------
const socket = io();

socket.on("connect", () => {
  socket.emit("join_arena", {});
});

socket.on("arena_state", state => {
  render(state);
  LiveReviewPanel.update(state.live_move_logs || [], state.hand_number);
  InterHandCountdown.update(state);
});

socket.on("arena_viewer_count", data => {
  const badge = document.getElementById("viewer-count-badge");
  if (badge) badge.textContent = `👁 ${data.count} watching`;
});

// ---------------------------------------------------------------------------
// Card rendering helpers (same as game.js)
// ---------------------------------------------------------------------------
const SUIT_SYMBOLS = { S: "♠", H: "♥", D: "♦", C: "♣" };
const RED_SUITS    = new Set(["H", "D"]);
const FACE_RANKS   = new Set([11, 12, 13]);

function rankLabel(rank) {
  if (rank === 11) return "J";
  if (rank === 12) return "Q";
  if (rank === 13) return "K";
  if (rank === 14) return "A";
  return String(rank);
}

function makeCardBackEl() {
  const el = document.createElement("div");
  el.className = "playing-card card-back";
  const pattern = document.createElement("div");
  pattern.className = "card-back-pattern";
  el.appendChild(pattern);
  return el;
}

function makeCardEl(card) {
  const suit = card && (card.suit || card.Suit);
  const rank = card && (card.rank || card.Rank);

  if (rank == null || suit == null || rank === 0 || suit === "") {
    return makeCardBackEl();
  }

  const label = rankLabel(rank);
  const suitSymbol = SUIT_SYMBOLS[suit] || suit;
  const isFace = FACE_RANKS.has(rank);

  const el = document.createElement("div");
  el.className = "playing-card " +
    (RED_SUITS.has(suit) ? "red" : "black") +
    (isFace ? " face-card" : "");
  el.dataset.rank = label;
  el.dataset.suit = suit;

  const rankTl = document.createElement("span");
  rankTl.className = "card-rank-tl";
  rankTl.textContent = label;

  const suitBr = document.createElement("span");
  suitBr.className = "card-suit-br";
  suitBr.textContent = suitSymbol;

  el.appendChild(rankTl);
  el.appendChild(suitBr);

  if (isFace) {
    const band = document.createElement("div");
    band.className = "face-band";
    band.textContent = label;
    el.appendChild(band);
  }

  return el;
}

// ---------------------------------------------------------------------------
// computeSeatPositions (same as game.js)
// ---------------------------------------------------------------------------
function computeSeatPositions(playerCount, localPlayerIndex) {
  const cx = 50;
  const cy = 50;
  // Push seats further out for larger player counts to avoid overlap
  const rx = playerCount >= 7 ? 52 : 46;
  const ry = playerCount >= 7 ? 46 : 42;

  const positions = [];
  for (let i = 0; i < playerCount; i++) {
    const offset = i - localPlayerIndex;
    const theta = Math.PI / 2 + (2 * Math.PI / playerCount) * offset;
    const x = cx + rx * Math.cos(theta);
    const y = cy + ry * Math.sin(theta);
    positions.push({ x, y });
  }
  return positions;
}

// ---------------------------------------------------------------------------
// renderSeats — Task 8.4: adds .winner class to winning seat during showdown
// ---------------------------------------------------------------------------
function renderSeats(state) {
  const tableEl = document.getElementById("poker-table");
  if (!tableEl) return;

  tableEl.querySelectorAll(".seat").forEach(el => el.remove());

  const players = state.players || [];
  if (!players.length) return;

  // In spectator mode there is no local player — use index 0 as anchor
  const positions = computeSeatPositions(players.length, 0);

  // Determine winner player_id for Task 8.4
  let winnerPlayerId = null;
  if (state.showdown_pending && state.showdown_results && state.showdown_results.length > 0) {
    winnerPlayerId = state.showdown_results[0].player_id;
  }

  players.forEach((p, i) => {
    const pos = positions[i];
    const isDealer = state.dealer_button !== undefined && i === state.dealer_button;
    const isWinner = winnerPlayerId !== null && p.player_id === winnerPlayerId;

    const seat = document.createElement("div");
    seat.className = "seat" +
      (p.is_turn    ? " active-turn" : "") +
      (!p.is_active ? " folded"      : "") +
      (isWinner     ? " winner"      : "");
    seat.style.left = pos.x + "%";
    seat.style.top  = pos.y + "%";
    seat.dataset.playerId = p.player_id;

    const hud = document.createElement("div");
    hud.className = "seat-hud";

    if (p.is_turn) {
      const turnLabel = document.createElement("div");
      turnLabel.className = "seat-turn-label";
      turnLabel.textContent = "🤖 Thinking…";
      hud.appendChild(turnLabel);
    }

    hud.innerHTML += `
      <div class="seat-name"><span class="seat-plabel">P${i + 1}:</span> ${escHtml(p.name)}</div>
      <div class="seat-chips">${p.chips}</div>
      ${p.current_bet ? `<div class="seat-bet">Bet: ${p.current_bet}</div>` : ""}
    `;
    seat.appendChild(hud);

    if (isDealer) {
      const btn = document.createElement("div");
      btn.className = "dealer-btn";
      btn.textContent = "D";
      seat.appendChild(btn);
    }

    // Always show hole cards in the arena (all players are AI — no privacy needed)
    const cards = p.hole_cards || [];
    if (cards.length) {
      const hcSlot = document.createElement("div");
      hcSlot.className = "seat-hole-cards";
      cards.forEach(c => hcSlot.appendChild(makeCardEl(c)));
      seat.appendChild(hcSlot);
    }

    // AI turn timer
    if (p.is_turn) {
      const timer = document.createElement("div");
      timer.className = "ai-timer";
      timer.innerHTML = `
        <div class="ai-timer-bar-track"><div class="ai-timer-bar-fill"></div></div>
        <span class="ai-timer-count">20s</span>
      `;
      seat.appendChild(timer);
    }

    tableEl.appendChild(seat);
  });

  // Start or stop the AI timer
  const activeAISeat = tableEl.querySelector('.seat.active-turn .ai-timer');
  if (activeAISeat) {
    AITimer.start(activeAISeat.closest('.seat'));
  } else {
    AITimer.stop();
  }
}

// ---------------------------------------------------------------------------
// AITimer (same as game.js)
// ---------------------------------------------------------------------------
const AITimer = {
  _intervalId: null,
  _startedAt: null,
  _duration: 20,

  start(seatEl) {
    this.stop();
    this._startedAt = Date.now();

    const fill  = seatEl.querySelector('.ai-timer-bar-fill');
    const count = seatEl.querySelector('.ai-timer-count');
    if (!fill || !count) return;

    const tick = () => {
      const elapsed = (Date.now() - this._startedAt) / 1000;
      const remaining = Math.max(0, this._duration - elapsed);
      const pct = (remaining / this._duration) * 100;

      fill.style.width = pct + '%';
      count.textContent = Math.ceil(remaining) + 's';

      if (remaining <= 5) fill.classList.add('urgent');
      else fill.classList.remove('urgent');
    };

    tick();
    this._intervalId = setInterval(tick, 200);
  },

  stop() {
    if (this._intervalId !== null) {
      clearInterval(this._intervalId);
      this._intervalId = null;
    }
    this._startedAt = null;
  },
};

// ---------------------------------------------------------------------------
// ActionLog (same as game.js, minus chat/action wiring)
// ---------------------------------------------------------------------------
const ActionLog = {
  _lastPot: null,
  _lastPhase: null,
  _lastHistoryLen: 0,
  _lastShowdownResults: null,

  _ACTION_LABELS: {
    F: 'folded', X: 'checked', C: 'called', R: 'raised', A: 'went all-in',
  },

  _timestamp() {
    const now = new Date();
    const hh = String(now.getHours()).padStart(2, "0");
    const mm = String(now.getMinutes()).padStart(2, "0");
    return `${hh}:${mm}`;
  },

  append(text, cssClass) {
    const list = document.getElementById("log-list");
    if (!list) return;

    const atBottom = list.scrollTop + list.clientHeight >= list.scrollHeight - 10;

    const entry = document.createElement("div");
    entry.className = "log-entry" + (cssClass ? " " + cssClass : "");
    entry.textContent = `[${this._timestamp()}] ${text}`;
    list.appendChild(entry);

    if (atBottom) list.scrollTop = list.scrollHeight;
  },

  onGameState(state) {
    const history = state.hand_history || [];
    if (history.length < this._lastHistoryLen) this._lastHistoryLen = 0;
    const newEntries = history.slice(this._lastHistoryLen);
    this._lastHistoryLen = history.length;

    for (const entry of newEntries) {
      const [, name, code, amount] = entry;
      const verb = this._ACTION_LABELS[code] || code;
      const amountStr = amount != null ? ` ${amount}` : '';
      this.append(`${name} ${verb}${amountStr}`);
    }

    if (state.phase !== undefined && state.phase !== this._lastPhase) {
      // Skip the generic "Showdown" phase label — the winner line below is more informative
      if (state.phase !== 'showdown') {
        const label = state.phase
          ? state.phase.charAt(0).toUpperCase() + state.phase.slice(1).replace(/_/g, ' ')
          : null;
        if (label) this.append(`── ${label} ──`, "log-phase");
      }
      this._lastPhase = state.phase;
    }

    if (state.showdown_pending && state.showdown_results && !this._lastShowdownResults) {
      this._lastShowdownResults = state.showdown_results;
      for (const r of state.showdown_results) {
        // hand_rank is null when the hand ended by everyone else folding
        const handStr = r.hand_rank ? ` — ${r.hand_rank}` : ' — everyone else folded';
        this.append(`🏆 ${r.name} wins ${r.chips_won} chips${handStr}`, "log-winner");
      }
    }
    if (!state.showdown_pending) {
      this._lastShowdownResults = null;
    }

    if (state.pot !== undefined && state.pot !== this._lastPot) {
      this.append(`Pot → ${state.pot}`, "log-pot");
      this._lastPot = state.pot;
    }
  },
};

// ---------------------------------------------------------------------------
// Player label legend — maps P1/P2/... to model names for spectators
// ---------------------------------------------------------------------------
let _playerLabelMap = {};  // { "P1": "Deepseek-v4-pro", ... }

function buildPlayerLabelMap(players) {
  _playerLabelMap = {};
  players.forEach((p, i) => {
    _playerLabelMap[`P${i + 1}`] = p.name;
  });
}

// Replace P1/P2/... references in reasoning text with actual names
function expandPlayerLabels(text) {
  return text.replace(/\bP(\d+)\b/g, (match, num) => {
    const name = _playerLabelMap[match];
    return name ? `${match} (${name})` : match;
  });
}
// ---------------------------------------------------------------------------
const LiveReviewPanel = {
  _MAX_REASONING_LEN: 500,
  _currentHand: null,
  _lastCount: 0,

  update(moveLogs, handId) {
    const entriesEl = document.getElementById("live-review-entries");
    if (!entriesEl) return;

    // If hand changed, clear panel and reset counters
    if (handId !== this._currentHand) {
      entriesEl.innerHTML = "";
      this._currentHand = handId;
      this._lastCount = 0;
    }

    // Append only new entries (index >= _lastCount)
    const newEntries = moveLogs.slice(this._lastCount);
    if (!newEntries.length) return;

    newEntries.forEach(log => {
      const entry = document.createElement("div");
      entry.className = "live-review-entry";

      const phase = (log.phase || "").replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
      const actionStr = log.amount != null ? `${log.action} ${log.amount}` : log.action;
      let reasoning = log.reasoning || "";
      if (reasoning.length > this._MAX_REASONING_LEN) {
        reasoning = reasoning.slice(0, this._MAX_REASONING_LEN) + "… (truncated)";
      }
      // Expand P1/P2/... to actual player names for readability
      reasoning = expandPlayerLabels(reasoning);

      entry.innerHTML = `
        <div class="live-review-entry-header">
          <span class="live-review-player">${escHtml(log.player_name)}</span>
          <span class="live-review-phase">${escHtml(phase)}</span>
          <span class="live-review-action">${escHtml(actionStr)}</span>
        </div>
        <div class="live-review-reasoning">${colorSuits(escHtml(reasoning))}</div>
      `;
      entriesEl.appendChild(entry);
    });

    this._lastCount = moveLogs.length;

    // Auto-scroll to latest entry
    const panel = document.getElementById("live-review-panel");
    if (panel) panel.scrollTop = panel.scrollHeight;
  },
};

// ---------------------------------------------------------------------------
// InterHandCountdown — Task 8.3
// ---------------------------------------------------------------------------
const InterHandCountdown = {
  _intervalId: null,

  update(state) {
    const overlay = document.getElementById("inter-hand-countdown");
    if (!overlay) return;

    if (state.inter_hand_ends_at) {
      // Show the overlay
      overlay.classList.add("visible");
      this._startCountdown(state.inter_hand_ends_at);
    } else {
      // Hide the overlay and stop interval
      overlay.classList.remove("visible");
      this._stop();
    }
  },

  _startCountdown(endsAt) {
    this._stop();

    const numberEl = document.getElementById("countdown-number");
    if (!numberEl) return;

    const tick = () => {
      const remaining = Math.max(0, endsAt - Date.now() / 1000);
      numberEl.textContent = Math.ceil(remaining);
      if (remaining <= 0) this._stop();
    };

    tick();
    this._intervalId = setInterval(tick, 250);
  },

  _stop() {
    if (this._intervalId !== null) {
      clearInterval(this._intervalId);
      this._intervalId = null;
    }
  },
};

// ---------------------------------------------------------------------------
// render — same structure as game.js but no action panel or lobby controls
// ---------------------------------------------------------------------------
function render(state) {
  ActionLog.onGameState(state);

  buildPlayerLabelMap(state.players || []);

  document.getElementById("sb-phase").textContent = state.phase || state.status || "—";
  document.getElementById("sb-pot").textContent   = state.pot   ?? 0;
  document.getElementById("sb-bet").textContent   = state.current_bet ?? 0;

  const blinds = state.blinds;
  const sbBlinds = document.getElementById("sb-blinds");
  if (sbBlinds && blinds) {
    sbBlinds.textContent = `${blinds[0]}/${blinds[1]}`;
  }

  // Next blind level info
  const sbNextBlinds = document.getElementById("sb-next-blinds");
  if (sbNextBlinds && state.hand_number != null) {
    const HANDS_PER_LEVEL = 8;
    const BLIND_SCHEDULE = [
      [10, 20], [20, 40], [40, 80], [75, 150],
      [150, 300], [300, 600], [500, 1000],
    ];
    const currentLevel = Math.min(Math.floor((state.hand_number - 1) / HANDS_PER_LEVEL), BLIND_SCHEDULE.length - 1);
    const nextLevel = currentLevel + 1;
    if (nextLevel < BLIND_SCHEDULE.length) {
      const handsUntil = HANDS_PER_LEVEL - ((state.hand_number - 1) % HANDS_PER_LEVEL);
      const next = BLIND_SCHEDULE[nextLevel];
      sbNextBlinds.textContent = `↑ ${next[0]}/${next[1]} in ${handsUntil} hand${handsUntil === 1 ? '' : 's'}`;
    } else {
      sbNextBlinds.textContent = "max blinds";
    }
  }

  const activePlayer = (state.players || []).find(p => p.is_turn);
  const sbTurn = document.getElementById("sb-turn");
  if (sbTurn) {
    sbTurn.textContent = activePlayer ? escHtml(activePlayer.name) : "—";
  }

  const potDisplay = document.querySelector('#community-area .pot-display');
  if (potDisplay) potDisplay.textContent = 'Pot: ' + (state.pot ?? 0);

  const ccEl = document.getElementById("community-cards");
  if (ccEl) {
    ccEl.innerHTML = "";
    (state.community_cards || []).forEach(c => ccEl.appendChild(makeCardEl(c)));
  }

  renderSeats(state);
}

// ---------------------------------------------------------------------------
// Utility
// ---------------------------------------------------------------------------
function escHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// Wrap ♥/♦ in red and ♠/♣ in a light color for readability in reasoning text.
// Input should already be HTML-escaped.
function colorSuits(escapedStr) {
  return escapedStr
    .replace(/([♥♦])/g, '<span style="color:#e05050">$1</span>')
    .replace(/([♠♣])/g, '<span style="color:#b0c8b0">$1</span>');
}
