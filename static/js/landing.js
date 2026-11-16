"use strict";
(async function () {
  const form = document.getElementById("name-form");
  const input = document.getElementById("username");
  const err = document.getElementById("form-error");
  const btn = document.getElementById("play-btn");
  const RE = /^[A-Za-z0-9_]{3,16}$/;

  const me = await api("/api/me");
  if (me.ok && me.data.username) {
    input.value = me.data.username;
    if (me.data.active_match) document.getElementById("resume").classList.remove("hidden");
  }

  input.addEventListener("input", () => { err.textContent = ""; });

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const name = input.value.trim();
    if (!RE.test(name)) { err.textContent = "Use 3 to 16 letters, numbers or underscores."; return; }
    btn.disabled = true;
    btn.textContent = "Shuffling…";
    const s = await api("/api/session", { username: name });
    if (!s.ok) { err.textContent = s.error; reset(); return; }
    const m = await api("/api/matches", {});
    if (!m.ok) { err.textContent = m.error; reset(); return; }
    location.href = "/play";
  });

  function reset() { btn.disabled = false; btn.textContent = "Take a seat"; }
})();
