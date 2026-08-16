/* Injected into the stopwatch page. Bridges the page and the background
 * service worker: on request (and on load), fetch the profile identity +
 * token and hand them to the page via window.postMessage. */
const EXPECTED_EMAIL = "sapnilm.working@gmail.com";

async function deliver() {
  try {
    const info = await chrome.runtime.sendMessage({ type: "gate-info" });
    if (!info || !info.token) return;
    window.postMessage(
      { source: "stopwatch-gate", email: info.email || "", token: info.token },
      window.location.origin
    );
  } catch {
    /* extension reloading; the page will re-request on visibilitychange */
  }
}

window.addEventListener("message", (e) => {
  if (e.source === window && e.data && e.data.source === "stopwatch-gate-request") {
    deliver();
  }
});

deliver();
