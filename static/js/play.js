/* Table client. Renders server state and sends the player's chosen action, nothing else.
   All rules, dealing and scoring live on the server. */
"use strict";
(function () {
  const $ = (id) => document.getElementById(id);
  const DENOMS = [1000, 500, 100, 25, 5, 1];
  const AUTO_NEXT_SECONDS = 5;

  let ws = null;
  let S = null;            // latest state
  let P = null;            // previous state
  let pending = false;
  let retry = 0;
  let closedForGood = false;
  let countdown = null;
  let bubbleTimers = {};

  // ------------------------------------------------------------ socket
  function connect() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${proto}//${location.host}/ws`);
    ws.onopen = () => { retry = 0; };
    ws.onmessage = (ev) => {
      let msg; try { msg = JSON.parse(ev.data); } catch (e) { return; }
      if (msg.type === "state") { pending = false; P = S; S = msg.state; render(); }
      else if (msg.type === "error") {
        // Restore the controls from the last state, then ask for a fresh one in case we fell behind.
        pending = false; toast(msg.error);
        if (S) renderControls();
        setTimeout(() => { if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: "sync" })); }, 1200);
      }
      else if (msg.type === "no_match") { closedForGood = true; showNoMatch(); }
      else if (msg.type === "expired") { closedForGood = true; showExpired(msg.reason); }
    };
    ws.onclose = () => {
      if (closedForGood) return;
      status("Reconnecting…");
      setTimeout(connect, Math.min(8000, 500 * 2 ** retry++));
    };
  }

  function send(obj) {
    if (!ws || ws.readyState !== 1 || pending) return false;
    pending = true;
    ws.send(JSON.stringify(Object.assign({ seq: S ? S.seq : 0 }, obj)));
    return true;
  }

  // ------------------------------------------------------------ chips
  function chipStacks(amount, maxStacks) {
    const box = el("div", "chips");
    let left = amount;
    const stacks = [];
    for (const d of DENOMS) {
      const n = Math.floor(left / d);
      if (n > 0) { stacks.push([d, n]); left -= n * d; }
      if (stacks.length >= (maxStacks || 3)) break;
    }
    for (const [d, n] of stacks) {
      const st = el("div", "stack");
      const shown = Math.min(n, 8);
      for (let i = 0; i < shown; i++) {
        const c = el("div", "chip v" + d);
        c.style.bottom = (i * 3) + "px";
        st.append(c);
      }
      st.style.height = (22 + shown * 3) + "px";
      box.append(st);
    }
    return box;
  }

  // ------------------------------------------------------------ rendering
  function render() {
    const s = S, p = P;
    const newHand = !p || p.hand_no !== s.hand_no || p.match_id !== s.match_id;

    $("hud-hand").textContent = `${s.hand_no}/${s.hands_total}`;
    const net = $("hud-net");
    net.textContent = fmtSigned(s.match.net_bb, 1) + " bb";
    net.className = s.match.net_bb > 0 ? "pos" : s.match.net_bb < 0 ? "neg" : "";
    $("you-name").textContent = s.username;
    $("you-stack").textContent = fmt(s.you.stack);
    $("bot-stack").textContent = fmt(s.bot.stack);

    $("dealer-btn").className = "dealer-btn " + (s.button === "you" ? "at-you" : "at-bot");
    $("seat-you").classList.toggle("to-act", s.to_act === "you");
    $("seat-bot").classList.toggle("to-act", s.to_act === "bot");
    $("bot-thinking").classList.toggle("hidden", !s.bot_thinking);

    renderHole(newHand);
    renderBoard(newHand);
    renderBets(newHand);
    renderPot(newHand);
    renderBubbles(newHand);
    renderControls();
    renderBanner(newHand);
    renderHistory();
  }

  function renderHole(newHand) {
    const s = S, p = P;
    const you = $("you-cards"), bot = $("bot-cards");
    if (newHand) {
      you.textContent = ""; bot.textContent = "";
      // Deal alternately, starting with the player left of the button.
      const order = s.button === "you" ? ["bot", "you", "bot", "you"] : ["you", "bot", "you", "bot"];
      let yi = 0;
      order.forEach((who, i) => {
        const c = who === "you" ? cardEl(s.you.cards[yi++], "deal-you") : cardEl(null, "deal-bot");
        c.style.animationDelay = (i * 0.12) + "s";
        (who === "you" ? you : bot).append(c);
      });
    }
    const revealNow = s.bot.cards && !(p && !newHand && p.bot.cards);
    if (revealNow) {
      bot.textContent = "";
      s.bot.cards.forEach((c, i) => { const e = cardEl(c, "flip"); e.style.animationDelay = (i * .08) + "s"; bot.append(e); });
    }
    if (!s.bot.cards && s.bot.folded) bot.querySelectorAll(".card").forEach(c => c.classList.add("dim"));
    you.querySelectorAll(".card").forEach(c => c.classList.toggle("dim", s.you.folded));
    if (s.hand_over && s.result && s.result.showdown) {
      const youWon = s.result.net > 0, botWon = s.result.net < 0;
      you.querySelectorAll(".card").forEach(c => c.classList.toggle("win", youWon));
      bot.querySelectorAll(".card").forEach(c => c.classList.toggle("win", botWon));
    }
  }

  function renderBoard(newHand) {
    const s = S, p = P;
    const board = $("board");
    const before = newHand || !p ? 0 : p.board.length;
    if (newHand || board.children.length !== 5) {
      board.textContent = "";
      for (let i = 0; i < 5; i++) board.append(el("div", "slot"));
    }
    let k = 0;
    for (let i = before; i < s.board.length; i++) {
      const c = cardEl(s.board[i], "deal-board");
      // When several cards arrive at once (flop, or an all-in run-out) stagger them.
      c.style.animationDelay = (k++ * (s.board.length - before > 3 ? 0.45 : 0.14)) + "s";
      board.replaceChild(c, board.children[i]);
    }
  }

  function renderBets(newHand) {
    const s = S, p = P;
    for (const who of ["you", "bot"]) {
      const box = $("bet-" + who);
      const amt = s[who].bet;
      const prevAmt = !newHand && p ? p[who].bet : 0;
      if (amt === prevAmt && !newHand && box.dataset.amt === String(amt)) continue;
      box.textContent = "";
      box.dataset.amt = String(amt);
      if (amt > 0) {
        const chips = chipStacks(amt, 2);
        if (amt > prevAmt) chips.classList.add("fly-in-" + who);
        box.append(chips, el("span", "bet-amt", fmt(amt)));
      }
    }
  }

  function renderPot(newHand) {
    const s = S, p = P;
    const chips = $("pot-chips");
    $("pot-amt").textContent = fmt(s.hand_over ? s.pot : s.pot_middle + s.you.bet + s.bot.bet);
    const mid = s.hand_over ? s.pot : s.pot_middle;
    const prevMid = !newHand && p ? (p.hand_over ? p.pot : p.pot_middle) : -1;
    if (mid === prevMid && !(s.hand_over && !(p && p.hand_over))) return;
    chips.textContent = "";
    const stack = chipStacks(mid, 3);
    chips.append(stack);
    if (!newHand && p && s.street !== p.street && !s.hand_over) stack.classList.add("sweep-you");
    if (s.hand_over && !(p && p.hand_over && !newHand)) {
      const r = s.result;
      const cls = r.net > 0 ? "pot-to-you" : r.net < 0 ? "pot-to-bot" : "pot-split";
      setTimeout(() => stack.classList.add(cls), r.showdown ? 900 : 350);
    }
  }

  function describe(a, betBefore) {
    const who = a.who === "you" ? "You" : "Bot";
    switch (a.action) {
      case "small_blind": return `${who} post small blind ${fmt(a.amount)}`.replace("Bot post", "Bot posts");
      case "big_blind": return `${who} post big blind ${fmt(a.amount)}`.replace("Bot post", "Bot posts");
      case "fold": return `${who} fold${a.who === "bot" ? "s" : ""}`;
      case "check": return `${who} check${a.who === "bot" ? "s" : ""}`;
      case "call": return `${who} call${a.who === "bot" ? "s" : ""} ${fmt(a.amount)}`;
      case "raise": return betBefore
        ? `${who} raise${a.who === "bot" ? "s" : ""} to ${fmt(a.amount)}`
        : `${who} bet${a.who === "bot" ? "s" : ""} ${fmt(a.amount)}`;
    }
    return "";
  }

  // Walk the action list, tagging each raise as a bet (first wager of a street) or raise.
  function describeAll(actions) {
    let street = null, wagered = false;
    return actions.map(a => {
      if (a.street !== street) { street = a.street; wagered = street === "preflop"; }
      const text = describe(a, wagered);
      if (a.action === "raise") wagered = true;
      return { a, text };
    });
  }

  function bubbleText(a, wageredBefore) {
    switch (a.action) {
      case "fold": return "Fold";
      case "check": return "Check";
      case "call": return "Call " + fmt(a.amount);
      case "raise": return (wageredBefore ? "Raise to " : "Bet ") + fmt(a.amount);
    }
    return null;
  }

  function renderBubbles(newHand) {
    const s = S, p = P;
    const start = newHand || !p ? 0 : p.actions.length;
    const all = describeAll(s.actions);
    for (let i = start; i < s.actions.length; i++) {
      const a = s.actions[i];
      if (a.action === "small_blind" || a.action === "big_blind") continue;
      const prevWager = all.slice(0, i).some(x => x.a.street === a.street && (x.a.action === "raise" || x.a.action === "big_blind"));
      showBubble(a.who, bubbleText(a, prevWager), a.action);
    }
  }

  function showBubble(who, text, action) {
    const b = $(who + "-bubble");
    b.textContent = text;
    b.className = "bubble" + (action === "fold" ? " b-fold" : action === "raise" ? " b-raise" : "");
    clearTimeout(bubbleTimers[who]);
    bubbleTimers[who] = setTimeout(() => b.classList.add("hidden"), 1800);
  }

  // ------------------------------------------------------------ controls
  function status(text) { $("status-line").textContent = text; }

  function renderControls() {
    const s = S;
    const actions = $("actions");
    if (s.status !== "playing") { actions.classList.add("hidden"); status(""); return; }
    if (!s.your_turn) {
      actions.classList.add("hidden");
      status(s.hand_over ? "" : s.bot_thinking ? "The bot is thinking…" : "Waiting…");
      return;
    }
    status(s.to_call > 0 ? `${fmt(s.to_call)} to call` : "Your move");
    actions.classList.remove("hidden");
    const L = s.legal;
    $("btn-fold").classList.toggle("hidden", !L.fold);
    const call = $("btn-call");
    if (L.check) call.textContent = "Check";
    else call.textContent = (L.call >= s.you.stack ? "Call all-in " : "Call ") + fmt(L.call);
    const r = L.raise;
    $("sizing").classList.toggle("hidden", !r);
    $("btn-raise").classList.toggle("hidden", !r);
    $("btn-raise").dataset.kind = r ? r.kind : "";
    if (r) {
      const sl = $("raise-slider"), inp = $("raise-input");
      sl.min = r.min; sl.max = r.max; sl.step = 1;
      inp.min = r.min; inp.max = r.max;
      setRaise(r.min);
      const only = r.min === r.max;
      document.querySelectorAll(".quick .btn").forEach(b => b.disabled = only && b.dataset.size !== "allin");
      sl.disabled = only;
    }
    document.querySelector(".main-actions").style.gridTemplateColumns =
      [L.fold, true, !!r].filter(Boolean).map((_, i, arr) => (i === arr.length - 1 && r ? "1.25fr" : "1fr")).join(" ");
  }

  function setRaise(v) {
    const r = S.legal.raise;
    v = Math.max(r.min, Math.min(r.max, Math.round(v)));
    $("raise-slider").value = v;
    $("raise-input").value = v;
    const label = v >= r.max ? "All-in " : r.kind === "bet" ? "Bet " : "Raise to ";
    $("btn-raise").textContent = label + fmt(v);
  }

  function sizeFor(kind) {
    const s = S, r = s.legal.raise;
    const facing = s.you.bet + s.to_call;              // the bet we would match
    const potAfterCall = s.pot + s.to_call;
    switch (kind) {
      case "min": return r.min;
      case "half": return facing + 0.5 * potAfterCall;
      case "pot": return facing + potAfterCall;
      case "allin": return r.max;
    }
    return r.min;
  }

  document.querySelectorAll(".quick .btn").forEach(b => b.addEventListener("click", () => setRaise(sizeFor(b.dataset.size))));
  $("raise-slider").addEventListener("input", (e) => setRaise(+e.target.value));
  $("raise-input").addEventListener("change", (e) => setRaise(+e.target.value || 0));
  $("btn-fold").addEventListener("click", () => act("fold"));
  $("btn-call").addEventListener("click", () => act(S.legal.check ? "check" : "call"));
  $("btn-raise").addEventListener("click", () => act("raise", +$("raise-input").value));

  function act(action, amount) {
    if (!S || !S.your_turn) return;
    const msg = { type: "action", action };
    if (action === "raise") msg.amount = amount;
    if (send(msg)) $("actions").classList.add("hidden");
  }

  document.addEventListener("keydown", (e) => {
    if (e.target.tagName === "INPUT" && e.key !== "Enter") return;
    if (!S) return;
    if (S.hand_over && (e.key === "Enter" || e.key === " ") && !$("banner").classList.contains("hidden")) {
      const b = $("banner-actions").querySelector("button"); if (b) { e.preventDefault(); b.click(); } return;
    }
    if (!S.your_turn) return;
    const k = e.key.toLowerCase();
    if (k === "f" && S.legal.fold) act("fold");
    else if (k === "c" || k === "k") act(S.legal.check ? "check" : "call");
    else if (k === "r" && S.legal.raise) act("raise", +$("raise-input").value);
    else if (k === "enter" && e.target.id === "raise-input" && S.legal.raise) { setRaise(+e.target.value); act("raise", +$("raise-input").value); }
  });

  // ------------------------------------------------------------ banner
  function clearCountdown() { if (countdown) { clearInterval(countdown); countdown = null; } }

  function renderBanner(newHand) {
    const s = S, p = P;
    const banner = $("banner");
    if (!s.hand_over) { clearCountdown(); banner.classList.add("hidden"); return; }
    if (p && p.hand_over && !newHand && p.seq !== s.seq && !banner.classList.contains("hidden")) return;
    if (p && p.hand_over && !newHand && !banner.classList.contains("hidden")) return;
    const r = s.result;
    const delay = r.showdown ? 900 : 350;
    setTimeout(() => {
      if (S !== s) return;
      const title = $("banner-title"), sub = $("banner-sub"), acts = $("banner-actions");
      acts.textContent = "";
      banner.className = "banner " + (r.net > 0 ? "win" : r.net < 0 ? "lose" : "");
      if (r.net > 0) title.textContent = `You win ${fmt(r.net)}`;
      else if (r.net < 0) title.textContent = `Bot wins ${fmt(-r.net)}`;
      else title.textContent = "Split pot";
      if (r.showdown) sub.textContent = `You: ${r.your_hand} · Bot: ${r.bot_hand}`;
      else sub.textContent = r.folded === "you" ? "You folded" : "The bot folded";
      if (s.match.finished) return showFinal(s, title, sub, acts, banner);
      const next = el("button", "btn btn-primary", "Next hand");
      acts.append(next);
      let left = AUTO_NEXT_SECONDS;
      next.textContent = `Next hand (${left})`;
      clearCountdown();
      countdown = setInterval(() => {
        left -= 1;
        if (left <= 0) { clearCountdown(); next.click(); } else next.textContent = `Next hand (${left})`;
      }, 1000);
      next.addEventListener("click", () => { clearCountdown(); send({ type: "next" }); });
      const stay = el("button", "btn btn-ghost", "Pause");
      stay.addEventListener("click", () => { clearCountdown(); next.textContent = "Next hand"; stay.remove(); });
      acts.append(stay);
    }, delay);
  }

  function showFinal(s, title, sub, acts, banner) {
    clearCountdown();
    banner.classList.add("final");
    const bb = s.match.net_bb, hands = s.match.hands_played;
    const prefix = title.textContent;
    title.textContent = bb > 0 ? "You beat the bot!" : bb < 0 ? "The bot wins the match" : "Dead even";
    sub.textContent = `Last hand: ${prefix}. Match complete, your result is on the leaderboard.`;
    const grid = el("div", "final-stats");
    const cell = (v, l) => { const d = el("div"); d.append(el("b", bb > 0 ? "pos" : bb < 0 ? "neg" : "", v), el("span", null, l)); return d; };
    grid.append(cell(fmtSigned(bb, 1), "net bb"), cell(fmtSigned(bb * 100 / Math.max(1, hands), 1), "bb/100"), cell(String(hands), "hands"));
    sub.after(grid);
    const again = el("button", "btn btn-primary", "Play again");
    again.addEventListener("click", newMatch);
    const board = el("a", "btn btn-ghost", "Leaderboard");
    board.href = "/leaderboard";
    acts.append(again, board);
    closedForGood = true;
  }

  async function newMatch() {
    const m = await api("/api/matches", {});
    if (!m.ok) { if (m.status === 401) location.href = "/"; else toast(m.error); return; }
    location.reload();
  }

  function showNoMatch() {
    status("");
    const banner = $("banner");
    banner.className = "banner";
    $("banner-title").textContent = "No match in progress";
    $("banner-sub").textContent = "Start a new 50-hand match against the bot.";
    const acts = $("banner-actions"); acts.textContent = "";
    const b = el("button", "btn btn-primary", "Start match"); b.addEventListener("click", newMatch);
    acts.append(b);
  }

  function showExpired(reason) {
    $("actions").classList.add("hidden");
    status("");
    const banner = $("banner");
    banner.className = "banner";
    $("banner-title").textContent = "Match ended";
    $("banner-sub").textContent = `This match was closed (${reason}) and does not count on the leaderboard.`;
    const acts = $("banner-actions"); acts.textContent = "";
    const b = el("button", "btn btn-primary", "New match"); b.addEventListener("click", newMatch);
    acts.append(b);
  }

  // ------------------------------------------------------------ history
  function renderHistory() {
    const s = S;
    const body = $("history-body");
    body.textContent = "";
    if (!s.hand_over) body.append(handBlock({
      hand_no: s.hand_no, your_cards: s.you.cards, bot_cards: null, board: s.board,
      actions: s.actions, net: null, button: s.button,
    }, true));
    for (let i = s.history.length - 1; i >= 0; i--) body.append(handBlock(s.history[i], false));
    if (!body.children.length) body.append(el("div", "hh-empty", "No hands yet."));
  }

  function handBlock(h, current) {
    const box = el("div", "hh" + (current ? " current" : ""));
    const top = el("div", "hh-top");
    top.append(el("span", null, `Hand ${h.hand_no}` + (h.button === "you" ? " · you're on the button" : "")));
    if (h.net !== null && h.net !== undefined) {
      top.append(el("span", h.net > 0 ? "pos" : h.net < 0 ? "neg" : "muted", h.net === 0 ? "±0" : fmtSigned(h.net)));
    } else top.append(el("span", "muted", "in play"));
    box.append(top);
    const cards = el("div", "hh-cards");
    const you = el("span", null, "You "); h.your_cards.forEach(c => you.append(miniCard(c))); cards.append(you);
    if (h.bot_cards) { const b = el("span", null, "Bot "); h.bot_cards.forEach(c => b.append(miniCard(c))); cards.append(b); }
    if (h.board.length) { const b = el("span", null, "Board "); h.board.forEach(c => b.append(miniCard(c))); cards.append(b); }
    box.append(cards);
    const acts = el("div", "hh-acts");
    let street = null;
    let line = null;
    for (const { a, text } of describeAll(h.actions)) {
      if (a.street !== street) {
        street = a.street;
        line = el("div"); line.append(el("span", "st", street + ": ")); acts.append(line);
      } else line.append(document.createTextNode(", "));
      line.append(document.createTextNode(text));
    }
    if (h.showdown) { const d = el("div"); d.textContent = `Showdown: you ${h.your_hand}, bot ${h.bot_hand}`; acts.append(d); }
    box.append(acts);
    return box;
  }

  const hist = $("history");
  $("history-toggle").addEventListener("click", () => { hist.classList.toggle("open"); $("history-toggle").setAttribute("aria-expanded", hist.classList.contains("open")); });
  $("history-close").addEventListener("click", () => hist.classList.remove("open"));

  $("quit-btn").addEventListener("click", async () => {
    if (S && S.status === "playing" && !confirm("Quit this match? It will not count on the leaderboard.")) return;
    if (S && S.status === "playing") { closedForGood = true; await api("/api/matches/current/quit", {}); }
    location.href = "/";
  });

  let toastTimer = null;
  function toast(text) {
    const t = $("toast"); t.textContent = text; t.classList.remove("hidden");
    clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.add("hidden"), 3000);
  }

  connect();
})();
