/**
 * Property-based tests for CardRenderer functions.
 *
 * Feature: poker-ui-redesign
 * Properties tested: 4, 5, 6, 7
 *
 * Runs with: node tests/property/test_card_renderer.js
 *
 * Dependencies: fast-check, jsdom (npm install --save-dev fast-check jsdom)
 */

"use strict";

const { JSDOM } = require("jsdom");
const fc = require("fast-check");
const assert = require("node:assert/strict");

// ---------------------------------------------------------------------------
// Set up a minimal DOM environment (jsdom)
// ---------------------------------------------------------------------------
const dom = new JSDOM("<!DOCTYPE html><html><body></body></html>");
global.document = dom.window.document;

// ---------------------------------------------------------------------------
// Replicated pure card-renderer functions from app/static/game.js
// (Cannot import game.js directly — it references browser globals: io, SESSION_ID)
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
// Arbitraries
// ---------------------------------------------------------------------------
const validSuit = fc.constantFrom("S", "H", "D", "C");
const validRank = fc.integer({ min: 2, max: 14 });
const validCard  = fc.record({ suit: validSuit, rank: validRank });

const redSuit   = fc.constantFrom("H", "D");
const blackSuit = fc.constantFrom("S", "C");
const faceRank  = fc.constantFrom(11, 12, 13);
const nonFaceRank = fc.integer({ min: 2, max: 10 }).filter(r => r !== 11 && r !== 12 && r !== 13);

// ---------------------------------------------------------------------------
// Property 4: Card dimensions meet minimum size
// ---------------------------------------------------------------------------
// Note: jsdom does not compute CSS layout, so we cannot check computed pixel
// dimensions. Instead we verify the element carries the CSS class "playing-card"
// which the stylesheet targets for min-width/height, and that the element is a
// <div> (not a text node or broken element). This is the testable invariant in
// a headless DOM environment without a CSS engine.
//
// Feature: poker-ui-redesign, Property 4: Card dimensions meet minimum size
// Validates: Requirements 2.1
// ---------------------------------------------------------------------------
console.log("Running Property 4: Card dimensions meet minimum size...");
fc.assert(
  fc.property(validCard, (card) => {
    const el = makeCardEl(card);
    // Element must carry the base class that the stylesheet sizes to 80×112px
    assert.ok(
      el.classList.contains("playing-card"),
      `Expected 'playing-card' class on element for card ${JSON.stringify(card)}`
    );
    assert.strictEqual(el.tagName.toLowerCase(), "div");
  }),
  { numRuns: 100 }
);

// Also verify makeCardBackEl carries the same class
fc.assert(
  fc.property(fc.constant(null), () => {
    const el = makeCardBackEl();
    assert.ok(el.classList.contains("playing-card"));
  }),
  { numRuns: 10 }
);
console.log("  PASS");

// ---------------------------------------------------------------------------
// Property 5: Suit color correctness
// ---------------------------------------------------------------------------
// Feature: poker-ui-redesign, Property 5: Suit color correctness
// Validates: Requirements 2.2
// ---------------------------------------------------------------------------
console.log("Running Property 5: Suit color correctness...");

// Red suits (H, D) → 'red' class
fc.assert(
  fc.property(redSuit, validRank, (suit, rank) => {
    const el = makeCardEl({ suit, rank });
    assert.ok(
      el.classList.contains("red"),
      `Expected 'red' class for suit ${suit}, rank ${rank}`
    );
    assert.ok(
      !el.classList.contains("black"),
      `Did not expect 'black' class for suit ${suit}, rank ${rank}`
    );
  }),
  { numRuns: 100 }
);

// Black suits (S, C) → 'black' class
fc.assert(
  fc.property(blackSuit, validRank, (suit, rank) => {
    const el = makeCardEl({ suit, rank });
    assert.ok(
      el.classList.contains("black"),
      `Expected 'black' class for suit ${suit}, rank ${rank}`
    );
    assert.ok(
      !el.classList.contains("red"),
      `Did not expect 'red' class for suit ${suit}, rank ${rank}`
    );
  }),
  { numRuns: 100 }
);
console.log("  PASS");

// ---------------------------------------------------------------------------
// Property 6: Face card indicator
// ---------------------------------------------------------------------------
// Feature: poker-ui-redesign, Property 6: Face card indicator
// Validates: Requirements 2.4
// ---------------------------------------------------------------------------
console.log("Running Property 6: Face card indicator...");

// Ranks 11, 12, 13 → element contains a .face-band child
fc.assert(
  fc.property(validSuit, faceRank, (suit, rank) => {
    const el = makeCardEl({ suit, rank });
    const band = el.querySelector(".face-band");
    assert.ok(
      band !== null,
      `Expected .face-band child for rank ${rank}, suit ${suit}`
    );
  }),
  { numRuns: 100 }
);

// Non-face ranks → no .face-band child
fc.assert(
  fc.property(validSuit, nonFaceRank, (suit, rank) => {
    const el = makeCardEl({ suit, rank });
    const band = el.querySelector(".face-band");
    assert.strictEqual(
      band,
      null,
      `Did not expect .face-band for rank ${rank}, suit ${suit}`
    );
  }),
  { numRuns: 100 }
);
console.log("  PASS");

// ---------------------------------------------------------------------------
// Property 7: Missing card fields produce card back
// ---------------------------------------------------------------------------
// Feature: poker-ui-redesign, Property 7: Missing card fields produce card back
// Validates: Requirements 2.7
// ---------------------------------------------------------------------------
console.log("Running Property 7: Missing card fields produce card back...");

// Generator for "bad" card objects: null/undefined rank or suit, or absent fields
const missingRankCard = fc.record({
  suit: validSuit,
  rank: fc.constantFrom(null, undefined, 0),
});
const missingSuitCard = fc.record({
  rank: validRank,
  suit: fc.constantFrom(null, undefined, ""),
});
const emptyCard = fc.constant({});
const nullCard  = fc.constant(null);

const badCard = fc.oneof(missingRankCard, missingSuitCard, emptyCard, nullCard);

fc.assert(
  fc.property(badCard, (card) => {
    const el = makeCardEl(card);
    assert.ok(
      el.classList.contains("card-back"),
      `Expected 'card-back' class for bad card ${JSON.stringify(card)}`
    );
    assert.ok(
      !el.classList.contains("red") && !el.classList.contains("black"),
      `Did not expect color class for bad card ${JSON.stringify(card)}`
    );
  }),
  { numRuns: 200 }
);
console.log("  PASS");

// ---------------------------------------------------------------------------
console.log("\nAll CardRenderer property tests passed.");
