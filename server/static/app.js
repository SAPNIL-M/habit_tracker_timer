/* Profile stopwatch page.
 * - Gate: asks the Chrome extension (via postMessage) for the profile identity
 *   and API token. The authorized email comes from the backend
 *   (server/config.json). Without a matching profile, the page is locked.
 * - While visible, sends a heartbeat every couple of seconds so the backend
 *   knows the profile is in use.
 * - Displays the live total: backend state + local ticking between polls.
 */
const HEARTBEAT_MS = 2000;
const POLL_MS = 1000;
let EXPECTED_EMAIL = "";

const state = {
  token: null,
  gateOk: false,
  locked: false,
  backendOk: false,
  status: "stopped",
  accumulatedMs: 0,
  startedAtMs: null,
  autoResume: false,
  resetArmed: false,
  resetTimer: null,
};

const $ = (id) => document.getElementById(id);
const timeEl = $("time");
const hoursEl = $("hoursHint");
const dotEl = $("statusDot");
const statusEl = $("statusText");
const toggleBtn = $("toggleBtn");
const resetBtn = $("resetBtn");
const gateEl = $("gateMsg");
const noteEl = $("gateNote");

// ---------------------------------------------------------------- gate

function requestGate() {
  window.postMessage({ source: "stopwatch-gate-request" }, window.location.origin);
}

function showGateLocked(message) {
  state.locked = true;
  gateEl.textContent = message;
  gateEl.classList.remove("hidden");
  setButtonsEnabled(false);
  dotEl.className = "dot off";
  statusEl.textContent = "Locked";
}

window.addEventListener("message", (e) => {
  if (e.origin !== window.location.origin) return;
  const d = e.data;
  if (!d || d.source !== "stopwatch-gate" || !d.token) return;
  const email = (d.email || "").toLowerCase();
  if (email === EXPECTED_EMAIL || email === "") {
    // Email verified, OR the profile isn't signed in to Chrome so we fall back
    // to the presence gate (extension installed in this profile).
    state.token = d.token;
    state.gateOk = true;
    gateEl.classList.add("hidden");
    if (email === "") noteEl.classList.remove("hidden");
    setButtonsEnabled(true);
    updateStatus();
    heartbeat();
    poll();
  } else {
    showGateLocked(
      `This Chrome profile is "${d.email}". The stopwatch only works in the profile signed in as ${EXPECTED_EMAIL}.`
    );
  }
});

async function init() {
  // The authorized email lives on the backend (server/config.json), so the
  // page never hardcodes personal info. Fetch it before starting the gate.
  try {
    const cfg = await (await fetch("/api/config")).json();
    EXPECTED_EMAIL = (cfg.expected_email || "").toLowerCase();
  } catch {
    /* backend offline; the status line will say so */
  }
  requestGate();
  setTimeout(() => {
    if (!state.gateOk && !state.locked) {
      showGateLocked(
        "This stopwatch only works in the Chrome profile configured on the backend " +
        "(server/config.json). Open it there and make sure the “Profile Stopwatch Gate” " +
        "extension is installed (chrome://extensions → Developer mode → Load unpacked " +
        "→ the extension folder)."
      );
    }
  }, 4000);
}
init();

// ---------------------------------------------------------------- backend

async function api(path, method = "GET") {
  const res = await fetch(path, {
    method,
    headers: { Authorization: "Bearer " + state.token },
  });
  if (!res.ok) throw new Error(path + " -> " + res.status);
  return res.json();
}

async function poll() {
  if (!state.token) return;
  try {
    const s = await api("/api/state");
    state.status = s.status;
    state.accumulatedMs = s.accumulated_ms;
    state.startedAtMs = s.started_at_ms;
    state.autoResume = s.auto_resume;
    state.backendOk = true;
  } catch {
    state.backendOk = false;
  }
  updateStatus();
}

async function heartbeat() {
  if (!state.token || document.visibilityState !== "visible") return;
  try {
    await api("/api/heartbeat", "POST");
    state.backendOk = true;
  } catch {
    state.backendOk = false;
  }
  updateStatus();
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    requestGate();
    heartbeat();
    poll();
  }
});

setInterval(() => {
  if (document.visibilityState === "visible") poll();
}, POLL_MS);

setInterval(() => {
  if (document.visibilityState === "visible") heartbeat();
}, HEARTBEAT_MS);

// ---------------------------------------------------------------- controls

function setButtonsEnabled(on) {
  toggleBtn.disabled = !on;
  resetBtn.disabled = !on;
}

toggleBtn.addEventListener("click", async () => {
  if (!state.gateOk || !state.backendOk) return;
  try {
    await api("/api/toggle", "POST");
    await poll();
  } catch { /* backend hiccup; poll will retry */ }
});

function disarmReset() {
  state.resetArmed = false;
  resetBtn.textContent = "Reset";
  resetBtn.classList.remove("danger");
  clearTimeout(state.resetTimer);
}

resetBtn.addEventListener("click", async () => {
  if (!state.gateOk || !state.backendOk) return;
  if (!state.resetArmed) {
    state.resetArmed = true;
    resetBtn.textContent = "Confirm reset?";
    resetBtn.classList.add("danger");
    clearTimeout(state.resetTimer);
    state.resetTimer = setTimeout(disarmReset, 3000);
    return;
  }
  disarmReset();
  try {
    await api("/api/reset", "POST");
    await poll();
  } catch { /* backend hiccup */ }
});

// ---------------------------------------------------------------- render

function liveMs() {
  if (state.status === "running" && state.startedAtMs != null) {
    return state.accumulatedMs + (Date.now() - state.startedAtMs);
  }
  return state.accumulatedMs;
}

function fmt(ms) {
  const total = Math.floor(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return (
    String(h).padStart(2, "0") + ":" +
    String(m).padStart(2, "0") + ":" +
    String(s).padStart(2, "0")
  );
}

function updateStatus() {
  toggleBtn.textContent = state.status === "running" ? "Pause" : "Start";

  if (!state.gateOk) {
    return; // lock message already shown / still handshaking
  }
  if (!state.backendOk) {
    dotEl.className = "dot off";
    statusEl.textContent = "Backend offline — run start.bat";
    setButtonsEnabled(false);
    return;
  }
  setButtonsEnabled(true);

  if (state.status === "running") {
    dotEl.className = "dot ok";
    statusEl.textContent = "Running · profile active";
  } else if (state.status === "paused") {
    dotEl.className = "dot pause";
    statusEl.textContent = state.autoResume
      ? "Paused · resumes automatically when you return"
      : "Paused";
  } else {
    dotEl.className = "dot stop";
    statusEl.textContent = "Stopped — press Start";
  }
}

function tick() {
  const ms = liveMs();
  timeEl.textContent = fmt(ms);
  hoursEl.textContent = (ms / 3600000).toFixed(2) + " h";
  requestAnimationFrame(tick);
}

requestAnimationFrame(tick);
