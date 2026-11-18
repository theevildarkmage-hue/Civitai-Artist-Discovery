import assert from 'node:assert/strict';
import { createAuthSession, socialWriteReason } from '../static/ui/auth-session.js';
import { toggleCreatorFollow } from '../static/ui/creator-actions.js';

const ready = { connected: true, id: 42, socialWrite: true, sessionState: 'ready' };
const refreshing = { ...ready, socialWrite: false, sessionState: 'refreshing' };
const states = [], timers = new Map();
let sequence = 0, calls = 0, answer = refreshing;
const session = createAuthSession({
  read: async () => { calls++; if (answer instanceof Error) throw answer; return answer; },
  apply: auth => states.push(auth),
  schedule: (callback, delay) => { timers.set(++sequence, { callback, delay }); return sequence; },
  cancel: id => timers.delete(id),
});
async function tick() {
  const [id, timer] = timers.entries().next().value;
  timers.delete(id); timer.callback();
  await new Promise(resolve => setImmediate(resolve));
}
await session.refresh();
assert.equal(states.at(-1).socialWrite, false);
assert.equal(timers.values().next().value.delay, 1000);
answer = ready; await tick();
assert.equal(states.at(-1).socialWrite, true);
assert.equal(timers.size, 0);
answer = new Error('Synthetic offline');
await assert.rejects(session.refresh());
assert.equal(states.at(-1).id, 42);
assert.equal(states.at(-1).socialWrite, false);
assert.equal(states.at(-1).sessionState, 'offline');
answer = ready; await tick();
assert.equal(states.at(-1).socialWrite, true);
for (const auth of [{ ...refreshing, needsReauthentication: true }, { ...ready, socialWrite: false }]) {
  answer = auth; await session.refresh();
  assert.equal(timers.size, 0, 'Revoked/missing scopes cannot trigger renewal loops');
}
answer = refreshing; await session.refresh();
const before = calls, delays = [];
while (timers.size) { delays.push(timers.values().next().value.delay); await tick(); }
assert.equal(calls - before, 7);
assert.deepEqual(delays, [1000, 2000, 4000, 8000, 15000, 30000, 60000]);

// An obsolete request may ignore AbortSignal; its result still cannot be applied.
let resolveOld, reads = 0;
const applied = [], signals = [];
const serial = createAuthSession({
  read: ({ signal }) => { signals.push(signal); reads++; return new Promise(resolve => { resolveOld = resolve; }); },
  apply: auth => applied.push(auth),
});
const old = serial.refresh();
assert.equal(serial.refresh(), old);
assert.equal(reads, 1, 'Overlapping readers share one status request');
serial.reset({ connected: false });
assert.equal(signals[0].aborted, true);
resolveOld(refreshing);
await assert.rejects(old, { name: 'AbortError' });
assert.deepEqual(applied, [{ connected: false }]);
const fresh = serial.refresh();
resolveOld({ ...ready, id: 43 }); await fresh;
assert.equal(applied.at(-1).id, 43);
assert.equal(reads, 2);
assert.match(socialWriteReason(refreshing), /Refreshing/);
assert.match(socialWriteReason({ ...ready, needsReauthentication: true }), /Sign in/);
assert.match(socialWriteReason({ ...ready, socialWrite: false }), /did not grant/);
assert.match(socialWriteReason({ ...ready, sessionState: 'offline' }), /temporarily unavailable/);
assert.equal(socialWriteReason(ready), '');
let account = 42, finishFollow;
const button = { dataset: {}, disabled: false }, creator = { userId: 101, username: 'Synthetic', following: false };
const pendingFollow = toggleCreatorFollow(button, creator, {
  api: () => new Promise(resolve => { finishFollow = resolve; }),
  accountEpoch: () => account, canWrite: () => true,
  toast: () => { throw new Error('Obsolete follow must not display a result'); },
});
account = 43; finishFollow({ following: true, userId: 101 }); await pendingFollow;
assert.equal(creator.following, false);
assert.equal(button.dataset.followPending, undefined);
console.log('PASS: renewal recovery, failed reads fail closed, revoked/missing grants, bounded backoff, coalescing, invalidation, account isolation and reasons; synthetic unit transport');
