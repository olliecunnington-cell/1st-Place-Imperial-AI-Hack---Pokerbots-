/* Shared helpers. Everything here only renders data the server already chose to send. */
"use strict";

const SUIT_CHAR = { s: "♠", h: "♥", d: "♦", c: "♣" };
const RANK_TXT = { T: "10" };

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
}

function fmt(n) { return Math.round(n).toLocaleString("en-US"); }
function fmtSigned(n, digits) {
  const v = digits ? n.toFixed(digits) : fmt(n);
  return (n > 0 ? "+" : "") + v;
}

function cardEl(code, extra) {
  if (!code) return el("div", "card back" + (extra ? " " + extra : ""));
  const r = code[0], s = code[1];
  const red = s === "h" || s === "d";
  const c = el("div", "card" + (red ? " c-red" : "") + (extra ? " " + extra : ""));
  c.setAttribute("role", "img");
  c.setAttribute("aria-label", (RANK_TXT[r] || r) + " of " + { s: "spades", h: "hearts", d: "diamonds", c: "clubs" }[s]);
  c.append(el("span", "rank", RANK_TXT[r] || r), el("span", "suit", SUIT_CHAR[s]), el("span", "pip", SUIT_CHAR[s]));
  return c;
}

function miniCard(code) {
  const red = code[1] === "h" || code[1] === "d";
  return el("span", "mini" + (red ? " r" : ""), (RANK_TXT[code[0]] || code[0]) + SUIT_CHAR[code[1]]);
}

async function api(path, body) {
  const opts = body === undefined ? { credentials: "same-origin" } : {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  };
  let res;
  try { res = await fetch(path, opts); } catch (e) { return { ok: false, error: "Network error. Check your connection." }; }
  let data = {};
  try { data = await res.json(); } catch (e) { /* non-JSON */ }
  if (!res.ok) return { ok: false, status: res.status, error: data.error || "Something went wrong." };
  return { ok: true, data };
}
