"use strict";
(async function () {
  const [lb, me] = await Promise.all([api("/api/leaderboard"), api("/api/me")]);
  const body = document.getElementById("lb-body");
  body.textContent = "";
  if (!lb.ok) { body.append(row(["", lb.error, "", "", "", ""])); return; }
  const d = lb.data;
  const myName = me.ok && me.data.username ? me.data.username.toLowerCase() : null;
  document.getElementById("lb-hands").textContent = d.hands_per_match;

  const b = d.bot;
  document.getElementById("rec-matches").textContent = fmt(b.matches);
  document.getElementById("rec-wl").textContent = `${b.wins} · ${b.losses} · ${b.ties}`;
  setSigned("rec-bb", b.net_bb, 1);
  setSigned("rec-bb100", b.bb_per_100, 1);
  document.getElementById("rec-foot").textContent = b.matches
    ? `${fmt(b.hands)} hands against ${b.players} player${b.players === 1 ? "" : "s"}. Only completed matches count.`
    : "No completed matches yet. Be the first.";

  if (!d.entries.length) {
    const tr = el("tr"); const td = el("td", "muted", "No completed matches yet."); td.colSpan = 6; tr.append(td); body.append(tr);
    return;
  }
  d.entries.forEach((e, i) => {
    const tr = row([
      String(i + 1), e.username,
      fmtSigned(e.net_bb, 1), fmtSigned(e.bb_per_100, 1), String(e.played),
      new Date(e.finished_at * 1000).toLocaleDateString(),
    ]);
    tr.children[2].classList.add("num", e.net_bb >= 0 ? "pos" : "neg");
    tr.children[3].classList.add("num");
    tr.children[4].classList.add("num");
    tr.children[5].classList.add("hide-sm");
    if (i === 0) tr.classList.add("rank-1");
    if (myName && e.username.toLowerCase() === myName) tr.classList.add("me");
    body.append(tr);
  });

  function row(cells) { const tr = el("tr"); cells.forEach(c => tr.append(el("td", null, c))); return tr; }
  function setSigned(id, v, digits) {
    const n = document.getElementById(id);
    n.textContent = fmtSigned(v, digits);
    n.classList.add(v > 0 ? "pos" : v < 0 ? "neg" : "muted");
  }
})();
