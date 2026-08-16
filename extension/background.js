/* Profile Stopwatch Gate — background service worker.
 *
 * Responsibilities:
 *  1. Identity gate: read this Chrome profile's email via
 *     chrome.identity.getProfileUserInfo() and only proceed if it matches the
 *     email the backend configures (server/config.json on the backend).
 *  2. Liveness: report to the backend whether this profile has at least one
 *     window open that isn't minimized. "Profile in use" == any non-minimized
 *     window exists, so the stopwatch keeps counting while you work in other
 *     tabs or other apps (split screen, VS Code), and pauses the moment all
 *     windows are minimized or closed (switching profiles via the avatar
 *     closes this profile's window).
 *  3. Token: generate a random API token, store it, register it with the
 *     backend, and send it with every request.
 */
const BASE = "http://127.0.0.1:8765";
const ALARM_PERIOD_MIN = 0.5;

async function getProfile() {
  try {
    return await chrome.identity.getProfileUserInfo();
  } catch {
    return null;
  }
}

async function ensureToken() {
  const { token } = await chrome.storage.local.get("token");
  if (token) return token;
  const t = crypto.randomUUID();
  await chrome.storage.local.set({ token: t });
  return t;
}

async function getExpectedEmail() {
  // Cached so a brief backend hiccup doesn't break the gate.
  const { expectedEmail } = await chrome.storage.local.get("expectedEmail");
  if (expectedEmail) return expectedEmail;
  try {
    const res = await fetch(`${BASE}/api/config`);
    if (res.ok) {
      const cfg = await res.json();
      const email = (cfg.expected_email || "").toLowerCase();
      if (email) await chrome.storage.local.set({ expectedEmail: email });
      return email;
    }
  } catch {
    /* backend offline */
  }
  return expectedEmail || "";
}

async function register() {
  const info = await getProfile();
  const email = (info && info.email) ? info.email : "";
  const expected = await getExpectedEmail();
  // Signed in as a *different* account -> hard block. An empty email means the
  // profile isn't signed in to Chrome at all, so we fall back to the presence
  // gate (the extension is only installed in this profile). If the expected
  // email isn't known yet (backend config unavailable), fail closed.
  if (email && (!expected || email !== expected)) {
    return null;
  }
  const token = await ensureToken();
  try {
    const res = await fetch(`${BASE}/api/extension/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, token }),
    });
    return res.ok ? token : null;
  } catch {
    return token; // backend may be starting up; keep the token for later calls
  }
}

async function report(path, extra = {}) {
  const token = await register();
  if (!token) return;
  try {
    await fetch(`${BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify(extra),
    });
  } catch {
    /* backend offline — retry on next alarm/event */
  }
}

// Focus is the signal: the stopwatch counts while one of this profile's
// windows has focus and pauses the moment it doesn't (you switched to
// another profile's window, another app, or minimized it).
let focusTimer = null;
chrome.windows.onFocusChanged.addListener((winId) => {
  clearTimeout(focusTimer);
  focusTimer = setTimeout(() => {
    const active = winId !== chrome.windows.WINDOW_ID_NONE;
    report(active ? "/api/extension/alive" : "/api/extension/inactive", {});
  }, 300);
});

// Hand the page its identity + token (used by content.js).
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === "gate-info") {
    (async () => {
      const info = await getProfile();
      const token = await ensureToken();
      sendResponse({ email: info ? info.email : "", token: token || "" });
    })();
    return true; // async response
  }
});

chrome.runtime.onInstalled.addListener(() => {
  try {
    chrome.alarms.create("window-watch", { periodInMinutes: ALARM_PERIOD_MIN });
  } catch {
    chrome.alarms.create("window-watch", { periodInMinutes: 1 });
  }
  report("/api/extension/alive");
});

chrome.runtime.onStartup.addListener(() => report("/api/extension/alive"));
chrome.windows.onCreated.addListener(() => report("/api/extension/alive"));
chrome.windows.onRemoved.addListener(() => report("/api/extension/inactive"));

// Backstop: never force-alive. Only re-assert inactivity when the profile has
// no usable windows (all closed or minimized) — focus events drive 'alive',
// so this can't fight the focus logic.
chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name !== "window-watch") return;
  try {
    const wins = await chrome.windows.getAll();
    if (wins.length === 0 || wins.every((w) => w.state === "minimized")) {
      report("/api/extension/inactive");
    }
  } catch {
    /* ignore */
  }
});
