# Profile Stopwatch

A stopwatch that counts up **only while your `sapnilm.working@gmail.com` Chrome
profile is in use**. Start it once; it pauses when you leave the profile
(switch profiles, close Chrome, close the laptop) and continues automatically
when you return. Split-screen friendly, works from other tabs and apps
(VS Code, etc.) as long as your profile's Chrome window is open.

## What you get

| Piece | Purpose |
|---|---|
| `server/` | FastAPI backend + stopwatch web page (localhost only, port 8765) |
| `extension/` | Tiny Chrome extension — the "profile lock" + activity tracker |
| `start.bat` / `start.sh` | One-click launcher (creates env on first run) |

## Setup (one time)

1. **Install the extension in the right profile.**
   - Open Chrome, make sure you're in the profile signed in as
     `sapnilm.working@gmail.com`.
   - Go to `chrome://extensions`, enable **Developer mode** (top right).
   - Click **Load unpacked** and select the `extension` folder of this project.
   - You should see "Profile Stopwatch Gate". Only this profile has it.

2. **Start the server once.**
   - Windows: double-click `start.bat`. A window opens and stays open with the
     server logs — **keep that window open** (closing it stops the server).
   - macOS/Linux: `./start.sh`.
   - Optional, to have it always running: put a shortcut to `start.bat` in
     `shell:startup` (Win+R → `shell:startup`).

3. **Open the stopwatch in the sapnilm profile.**
   - Go to `http://127.0.0.1:8765` in that profile.
   - It should show "Running · profile active" status after you press **Start**.
   - **Pin the tab** so it's always one click away and restores with Chrome.

That's it. Click **Start** once — it keeps counting from then on.

## How it behaves

| Situation | What happens |
|---|---|
| Stopwatch window is the **focused** window (any tab in it) | Keeps counting |
| Switch to another profile's window / another app (VS Code) / minimize | Stops — the stopwatch window lost focus |
| Switch profiles via the avatar (window closes) | Stops |
| Close Chrome entirely | Stops |
| Laptop sleep / lid closed | Stops; on wake the missed time is excluded automatically |
| Return to the profile / reopen the tab | Resumes automatically from where it left off |
| You press **Pause** | Stays paused until you press **Start** again (no auto-resume) |
| **Reset** (click twice to confirm) | Back to 0 and stopped |
| Any other profile or browser | Page shows **Locked** — can't start |

Notes:
- The rule is deliberately simple: **it counts while the stopwatch window has
  focus, and pauses the moment it doesn't** (another profile, another app,
  minimized). It resumes automatically when you focus the window again.
- After a hard Chrome kill or laptop sleep there can be up to ~2 minutes of
  over-count (the safety timeout); profile switches via the avatar stop
  instantly. The time you were genuinely away is never counted.

## How it works

- **Backend is the source of truth** (`server/main.py`). It stores one row in
  SQLite: status (`stopped/running/paused`), accumulated time, and when the
  current run segment started. The live total is always
  `accumulated + (now − started)`, so nothing is lost on restarts.
- **The extension watches focus.** A service worker tells the backend when
  the stopwatch window gains/loses focus (`chrome.windows.onFocusChanged`),
  pauses the moment focus is lost, and resumes when it returns. A ~30s alarm
  only re-asserts inactivity when every window is closed or minimized, so it
  can't override the focus logic. Page heartbeats never resume a paused
  stopwatch — only real focus does. The start.bat window logs every report
  (`[ext] alive` / `[ext] inactive`) — handy for troubleshooting.
- **The page heartbeats every 2s while visible** (fast signal + instant
  auto-resume when you come back) and renders the ticking display locally.
- **Safety timeout.** If no signal arrives for 120s (browser killed, laptop
  asleep, event missed), the backend pauses. When activity returns, any quiet
  gap longer than the timeout is retroactively excluded.
- **Identity lock.** The extension reads the profile email via
  `chrome.identity.getProfileUserInfo()`. If the profile is signed in, only
  `sapnilm.working@gmail.com` is accepted. If the profile isn't signed in to
  Chrome (so Chrome can't report an email), it falls back to a presence gate:
  the lock is "the extension is installed in this profile". Either way the
  page only works where the extension is installed, and every API call needs
  the random token the extension hands to the page. No extension / wrong
  profile → no token → locked.

## Layout

```
server/main.py        FastAPI app, state machine, SQLite, watchdog
server/static/        index.html, app.js, style.css (the stopwatch page)
extension/            manifest.json, background.js, content.js (profile gate)
tests/test_state.py   Unit tests for the state machine (fake clock)
start.bat, start.sh   Launchers
```

## Troubleshooting

- **start.bat says "The server is ALREADY running"** — good, it's up. Just open
  `http://127.0.0.1:8765` in the sapnilm profile.
- **start.bat window shows the server running, but the page won't load** — a
  proxy/VPN in Chrome can block localhost. Try `http://localhost:8765` instead,
  or add `127.0.0.1` to your proxy's bypass list.
- **"Locked" on the page** — you're in a profile without the extension, or the
  profile isn't signed in as sapnilm.working@gmail.com. Re-check step 1.
- **"Backend offline"** — the server isn't running; run `start.bat`.
- **First run asks about Windows Firewall** — allow it; the server only listens
  on 127.0.0.1 (your own machine).
- **Server won't start / port in use** — change `PORT` in `server/main.py`
  (and the matching port in `extension/manifest.json` + `extension/background.js`).
- **Reset after testing** — the page's Reset button, or delete
  `server/stopwatch.db` and restart the server.
