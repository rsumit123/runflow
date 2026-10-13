// api.js tells AuthContext, without importing it, that the session has ended.
// Parallel 401s fire the event once; a fresh sign-in re-arms it.
const listeners = new Set();
let fired = false;

export function onSessionEnded(fn) {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}

export function notifySessionEnded() {
  if (fired) return;
  fired = true;
  listeners.forEach((fn) => fn());
}

export function rearmSessionEnded() {
  fired = false;
}
