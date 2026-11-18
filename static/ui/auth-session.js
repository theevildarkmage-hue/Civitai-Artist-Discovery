// Reconcile temporary renewal states without replaying any user action. All status
// reads share one request; invalidation prevents a late reply resurrecting sign-in.
export function createAuthSession({ read, apply, schedule = setTimeout, cancel = clearTimeout,
  delays = [1000, 2000, 4000, 8000, 15000, 30000, 60000] }) {
  let generation = 0, pending = null, timer = null, attempt = 0, current = null;
  function clearTimer() {
    if (timer !== null) cancel(timer);
    timer = null;
  }
  function retry() {
    clearTimer();
    if (attempt >= delays.length) return;
    timer = schedule(() => {
      timer = null;
      request(false).catch(() => {});
    }, delays[attempt++]);
  }
  function request(newCycle = true) {
    if (pending) return pending.promise;
    clearTimer();
    if (newCycle) attempt = 0;
    const entry = { generation, controller: new AbortController() };
    pending = entry;
    entry.promise = (async () => {
      try {
        const auth = await read({ signal: entry.controller.signal });
        if (entry.generation !== generation) throw new DOMException('Account changed', 'AbortError');
        current = auth;
        apply(auth);
        if (!auth.needsReauthentication && ['refreshing', 'offline'].includes(auth.sessionState)) retry();
        else attempt = 0;
        return auth;
      } catch (error) {
        if (entry.generation === generation && error.name !== 'AbortError') {
          // Transport failure cannot establish usable grants. Keep only cached
          // identity for display until a subsequent server snapshot confirms them.
          current = { ...(current || {}), socialWrite: false, userWrite: false,
            collectionsRead: false, collectionsWrite: false, sessionState: 'offline' };
          apply(current);
          if (!current.needsReauthentication) retry();
        }
        throw error;
      } finally {
        if (pending === entry) pending = null;
      }
    })();
    return entry.promise;
  }
  function invalidate() {
    generation++;
    clearTimer();
    pending?.controller.abort();
    pending = null;
    attempt = 0;
  }
  return {
    refresh: () => request(),
    invalidate,
    reset(auth) { invalidate(); current = auth; apply(auth); },
  };
}

export function socialWriteReason(auth) {
  if (auth.needsReauthentication) return 'Session expired. Sign in to resume follows and reactions.';
  if (auth.sessionState === 'offline') return 'Session temporarily unavailable. Retrying automatically; your saved gallery is safe.';
  if (auth.socialWrite && auth.connected) return '';
  if (auth.sessionState === 'refreshing') return 'Refreshing your session. Follows and reactions will return automatically.';
  if (!auth.connected) return 'Sign in to follow and react.';
  return 'Civitai did not grant follow and reaction access. Sign out and back in to allow it.';
}
