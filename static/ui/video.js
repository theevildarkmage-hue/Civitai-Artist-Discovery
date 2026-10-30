import { reportClientEvent } from './diagnostics.js';

export const VIDEO_POLICY = Object.freeze({
  visibleShare: 0.5, settleMs: 300, indicatorMs: 2000, idleMs: 15000,
  progressLimitMs: 45000, tickMs: 1000, cardReloads: 1, detailReloads: 2,
  sharedReloads: 2, backoffMs: [3000, 10000], recoverySpacingMs: 1000,
});
const sharedBudget = new Map();
let nextRecoveryAt = 0;
const realClock = { now: () => performance.now(), setTimeout: (fn, ms) => setTimeout(fn, ms), clearTimeout: id => clearTimeout(id) };
const reserveRecovery = (at, spacing) => { const slot = Math.max(at, nextRecoveryAt); nextRecoveryAt = slot + spacing; return slot; };
export function resetVideoRecoveryBudget() { sharedBudget.clear(); nextRecoveryAt = 0; }

// One small lifecycle shared by the existing card and native detail adapters. Each
// attempt owns its element/listeners; replacing it cannot deliver old events to a new player.
export function createMediaLifecycle({ createVideo, mount, eligible, render, diagnose = () => {},
  card = false, muted = true, policy = VIDEO_POLICY, clock = realClock, budget = sharedBudget,
  reserve = reserveRecovery, online = () => navigator.onLine !== false }) {
  let item = null, scope = null, generation = 0, revision = 0, wanted = false;
  let state = 'idle', message = '', retryable = false, disposed = false, snapshot = null;
  let restartAtBeginning = false;
  const ownBudget = new Map();
  const timers = new Set();
  const emit = (next, text = '', canRetry = false) => {
    state = next; message = text; retryable = canRetry;
    render({ state, message, retryable, wanted, video: scope?.video || null });
  };
  const valid = s => !disposed && s === scope && s.generation === generation;
  function clearTimers() { for (const id of timers) clock.clearTimeout(id); timers.clear(); }
  function later(fn, ms) {
    const g = generation, r = revision;
    const id = clock.setTimeout(() => {
      timers.delete(id);
      if (disposed || g !== generation || r !== revision || !wanted) return;
      if (!eligible()) { cancel('ineligible'); return; }
      fn();
    }, ms);
    timers.add(id);
  }
  function remember() {
    const v = scope?.video;
    if (!v) return;
    const time = scope.restoring ? snapshot?.time || 0 : Number.isFinite(v.currentTime) ? Math.max(0, v.currentTime) : 0;
    snapshot = { time, rate: v.playbackRate || 1, volume: v.volume, muted: v.muted };
  }
  function retire() {
    generation++; clearTimers();
    const old = scope; scope = null;
    if (!old) return;
    old.abort.abort();
    old.video.pause(); old.video.removeAttribute('src'); old.video.removeAttribute('poster');
    old.video.preload = 'none'; old.video.load(); old.video.remove();
  }
  function cancel(reason = 'canceled') {
    remember(); revision++; retire(); emit(reason === 'user-paused' ? 'user-paused' : 'canceled');
  }
  function fail(text, canRetry = true) {
    remember(); retire();
    emit('failed', text, canRetry);
    diagnose('video_failed', { reason: text, imageId: item?.id });
  }
  function mayReload() {
    return (ownBudget.get(item.id) || 0) < (card ? policy.cardReloads : policy.detailReloads) &&
      (budget.get(item.id) || 0) < policy.sharedReloads;
  }
  function recover(reason) {
    if (!wanted || !eligible()) { cancel('ineligible'); return; }
    if (!mayReload()) { fail('Video could not be loaded. Retry when ready.'); return; }
    remember(); retire();
    if (!online()) { emit('offline', 'Connection interrupted. Retry when ready.', true); return; }
    emit('recovering', 'Retrying video\u2026');
    const used = budget.get(item.id) || 0;
    const delay = policy.backoffMs[Math.min(used, policy.backoffMs.length - 1)];
    const slot = reserve(clock.now() + delay, policy.recoverySpacingMs);
    later(() => {
      if (!online()) { emit('offline', 'Connection interrupted. Retry when ready.', true); return; }
      if (!mayReload()) { fail('Video could not be loaded. Retry when ready.'); return; }
      ownBudget.set(item.id, (ownBudget.get(item.id) || 0) + 1);
      budget.set(item.id, (budget.get(item.id) || 0) + 1);
      diagnose('video_retry', { imageId: item.id, reason });
      begin();
    }, Math.max(0, slot - clock.now()));
  }
  function rangesEnd(v) {
    let end = 0;
    for (let i = 0; i < v.buffered.length; i++) end = Math.max(end, v.buffered.end(i));
    return end;
  }
  function watchdog(s) {
    if (!valid(s)) return;
    const v = s.video, now = clock.now(), time = v.currentTime;
    const end = rangesEnd(v);
    const advanced = !v.seeking && !s.restoring && Math.abs(time - s.lastTime) > 0.05;
    if (advanced) {
      s.lastTime = time; s.hasPlayed = true; s.lastUseful = now; s.episode = now;
      emit('playing');
    }
    if (end > s.bufferEnd + 0.05 || v.readyState > s.readiness) s.lastUseful = now;
    s.bufferEnd = Math.max(s.bufferEnd, end); s.readiness = Math.max(s.readiness, v.readyState);
    if (now - s.lastUseful >= policy.idleMs) {
      if (s.restoring) { restartAtBeginning = true; fail('Could not resume this video. Retry to play from the beginning.'); }
      else recover(s.hasPlayed ? 'playback-stalled' : 'initial-load-timeout');
      return;
    }
    if (now - s.episode >= policy.indicatorMs) {
      const slow = now - s.episode >= policy.progressLimitMs;
      emit(s.hasPlayed ? 'buffering' : 'loading', slow ? 'Still buffering\u2026 Retry if needed.' : s.hasPlayed ? 'Buffering\u2026' : 'Loading video\u2026', slow);
    }
    later(() => watchdog(s), policy.tickMs);
  }
  function requestPlay(s) {
    if (!valid(s) || !wanted || !eligible() || s.playRequested) return;
    const v = s.video;
    if (s.restoring) {
      if (!v.readyState) return;
      let target = snapshot?.time || 0;
      if (Number.isFinite(v.duration)) target = Math.min(target, Math.max(0, v.duration - 0.1));
      let seekable = false;
      for (let i = 0; i < v.seekable.length; i++) if (target >= v.seekable.start(i) && target <= v.seekable.end(i)) seekable = true;
      if (!seekable) return;
      if (!s.seekRequested) { s.seekRequested = true; v.currentTime = target; }
      if (v.seeking || Math.abs(v.currentTime - target) > 0.5) return;
      s.restoring = false; s.lastTime = v.currentTime;
    }
    s.playRequested = true;
    const r = revision;
    Promise.resolve(v.play()).then(() => {
      if (!valid(s) || r !== revision || !wanted) return;
      if (!eligible()) { cancel('ineligible'); return; }
      emit('playing');
    }).catch(error => {
      if (!valid(s) || r !== revision || !wanted || error?.name === 'AbortError') return;
      if (!eligible()) { cancel('ineligible'); return; }
      // A browser may refuse audible playback; fall back to muted instead of stopping.
      if (error?.name === 'NotAllowedError' && !v.muted) { v.muted = true; s.playRequested = false; requestPlay(s); return; }
      if (error?.name === 'NotAllowedError') { retire(); emit('awaiting-gesture', 'Press Play to start the video.'); }
      else if (error?.name === 'NotSupportedError') fail('Video unavailable or unsupported. Try opening it on Civitai.');
      else recover('play-request-failed');
    });
  }
  function begin() {
    if (disposed || !item?.source || !wanted || !eligible()) return;
    retire();
    const v = createVideo(), now = clock.now();
    const s = { generation, video: v, abort: new AbortController(), lastUseful: now,
      episode: now, lastTime: 0, bufferEnd: 0, readiness: 0, hasPlayed: false,
      restoring: !!snapshot?.time, seekRequested: false, playRequested: false };
    scope = s;
    v.loop = true; v.playsInline = true; v.preload = 'auto';
    v.muted = card ? true : (snapshot?.muted ?? muted);
    v.volume = snapshot?.volume ?? 1; v.playbackRate = snapshot?.rate ?? 1;
    if (item.poster) v.poster = item.poster;
    const listen = (name, fn) => v.addEventListener(name, () => {
      if (!valid(s)) return;
      if (!eligible()) { cancel('ineligible'); return; }
      fn();
    }, { signal: s.abort.signal });
    for (const event of ['loadedmetadata', 'loadeddata', 'canplay', 'seeked']) listen(event, () => requestPlay(s));
    listen('progress', () => {
      // Before the first decodable range, MP4 data can arrive without changing
      // buffered/readyState. Native progress while fetching is evidence of a live load.
      if (!s.hasPlayed && v.networkState === 2) s.lastUseful = clock.now();
      requestPlay(s);
    });
    listen('playing', () => { if (wanted) emit('playing'); else v.pause(); });
    listen('play', () => {
      // Native detail controls can resume an intentionally paused element.
      if (!wanted) { wanted = true; revision++; s.playRequested = true; s.lastUseful = clock.now(); s.episode = clock.now(); later(() => watchdog(s), policy.tickMs); }
    });
    listen('pause', () => {
      if (!wanted) return;
      wanted = false; revision++; clearTimers(); remember(); s.playRequested = false;
      emit('user-paused');
    });
    listen('error', () => {
      if (!wanted) return;
      const code = v.error?.code;
      if (code === 1) { wanted = false; cancel('canceled'); }
      else if (code === 3 || code === 4) fail('Video unavailable or unsupported. Try opening it on Civitai.');
      else recover('media-network-error');
    });
    mount(v); v.src = item.source;
    emit('loading');
    requestPlay(s); later(() => watchdog(s), policy.tickMs);
  }
  const api = {
    setItem(next) {
      if (item?.id === next?.id && item?.source === next?.source) { item = next; return; }
      wanted = false; revision++; retire(); item = next; snapshot = null; restartAtBeginning = false; emit('idle');
    },
    start(manual = false) {
      if (disposed || !eligible() || !item?.source) return;
      if (!manual && ['failed', 'awaiting-gesture', 'offline', 'user-paused'].includes(state)) return;
      if (scope && wanted) return;
      wanted = true; revision++;
      if (scope) { scope.playRequested = false; scope.lastUseful = clock.now(); scope.episode = clock.now(); requestPlay(scope); later(() => watchdog(scope), policy.tickMs); }
      else begin();
    },
    pause() { wanted = false; cancel('user-paused'); },
    cancel,
    retry() {
      if (!eligible() || !item) return;
      ownBudget.delete(item.id); budget.delete(item.id);
      if (restartAtBeginning) snapshot = null;
      restartAtBeginning = false; wanted = true; revision++; begin();
    },
    online() { if (state === 'offline' && wanted && eligible()) recover('connection-restored'); },
    release() { wanted = false; cancel('removed'); disposed = true; },
    get state() { return state; }, get wanted() { return wanted; }, get video() { return scope?.video; },
  };
  return api;
}

const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
const cards = new Set();
const artworkOwners = new WeakMap();
let visibility = null, viewportTop = 0;
let suspended = false, detail = null;
function visibleElement(el) {
  if (!el?.isConnected || el.closest('.hidden') || document.hidden) return false;
  const box = el.getBoundingClientRect();
  return box.height > 0 && box.bottom > 0 && box.top < innerHeight;
}
function artworkViewportTop() {
  return Math.max(0, document.querySelector?.('.view-tabs')?.getBoundingClientRect().bottom || 0);
}
function artworkVisibleShare(stage) {
  if (!stage.isConnected || stage.closest('.hidden, [hidden]') || document.hidden) return 0;
  const box = stage.getBoundingClientRect();
  if (box.width <= 0 || box.height <= 0) return 0;
  const width = Math.max(0, Math.min(innerWidth, box.right) - Math.max(0, box.left));
  const height = Math.max(0, Math.min(innerHeight, box.bottom) - Math.max(artworkViewportTop(), box.top));
  return width * height / (box.width * box.height);
}
function observeArtwork() {
  visibility?.disconnect();
  viewportTop = artworkViewportTop();
  const observer = new IntersectionObserver(entries => {
    if (observer !== visibility) return;
    for (const entry of entries) artworkOwners.get(entry.target)?.setVideoVisible?.(entry.intersectionRatio >= VIDEO_POLICY.visibleShare);
  }, { threshold: [0, VIDEO_POLICY.visibleShare], rootMargin: `-${viewportTop}px 0px 0px 0px` });
  visibility = observer;
  for (const el of cards) {
    const stage = el.querySelector('.image-stage');
    observer.observe(stage);
    el.setVideoVisible(artworkVisibleShare(stage) >= VIDEO_POLICY.visibleShare);
  }
}
let artworkObservationFrame = 0;
function scheduleArtworkObservation() {
  if (artworkObservationFrame) return;
  // The shell measures the masthead in its own resize callback. Read the final
  // sticky offset next frame, after every observer has updated layout variables.
  artworkObservationFrame = requestAnimationFrame(() => {
    artworkObservationFrame = 0;
    observeArtwork();
  });
}
window.addEventListener('resize', scheduleArtworkObservation);
if (typeof ResizeObserver !== 'undefined') {
  const chromeResize = new ResizeObserver(scheduleArtworkObservation);
  for (const el of document.querySelectorAll('.view-tabs, body > header')) chromeResize.observe(el);
}
export function refreshVideoEligibility() {
  for (const el of [...cards]) el.resumeVideo?.();
  detail?.refresh();
}
document.addEventListener('visibilitychange', refreshVideoEligibility);
window.addEventListener('online', () => { for (const el of cards) el.videoOnline?.(); detail?.controller.online(); });
reducedMotion.addEventListener?.('change', refreshVideoEligibility);
export function suspendCardVideos(value) { suspended = !!value; refreshVideoEligibility(); }

function feedback(host, kind) {
  const box = document.createElement('div'); box.className = `${kind} video-feedback`; box.hidden = true;
  box.setAttribute('role', 'status'); box.setAttribute('aria-live', 'polite');
  const text = document.createElement('span'), retry = document.createElement('button');
  retry.type = 'button'; retry.className = 'video-retry'; retry.textContent = 'Retry video'; retry.hidden = true;
  box.append(text, retry); host.append(box);
  return { box, text, retry, update(info) {
    if (text.textContent !== info.message) text.textContent = info.message;
    const awaitingGesture = info.state === 'awaiting-gesture';
    retry.textContent = awaitingGesture ? 'Play video' : 'Retry video';
    retry.hidden = !info.retryable && !awaitingGesture; box.hidden = !info.message;
  } };
}
function diagnostic(surface, item) {
  return (event, details) => reportClientEvent(event, { ...details, surface, imageId: item()?.id });
}
export function attachCardVideo(el, main) {
  let item = null, visible = false, manual = false, pausedVisit = false, settleTimer = 0, released = false;
  const toggle = document.createElement('button'); toggle.type = 'button'; toggle.className = 'video-toggle'; toggle.hidden = true;
  const stage = el.querySelector('.image-stage'); stage.append(toggle);
  const artworkEligible = () => artworkVisibleShare(stage) >= (manual ? Number.EPSILON : VIDEO_POLICY.visibleShare);
  const status = feedback(stage, 'card-video-status');
  const controller = createMediaLifecycle({ card: true,
    createVideo: () => { const v = document.createElement('video'); v.className = 'card-video'; v.hidden = true; v.setAttribute('aria-hidden', 'true'); return v; },
    mount: v => main.after(v),
    eligible: () => !released && !!item?.videoUrl && !suspended && artworkEligible() &&
      !!el.dataset.imagesActive && el.getAttribute('aria-busy') !== 'true' &&
      (manual || (visible && !pausedVisit && !reducedMotion.matches)),
    diagnose: diagnostic('gallery', () => item),
    render(info) {
      el.dataset.videoState = info.state;
      el.classList.toggle('video-playing', info.state === 'playing' || info.state === 'buffering');
      el.classList.toggle('video-loading', ['loading', 'recovering'].includes(info.state));
      el.classList.toggle('video-has-feedback', !!info.message);
      if (info.video) info.video.hidden = !['playing', 'buffering', 'user-paused'].includes(info.state);
      const active = info.wanted && ['loading', 'playing', 'buffering', 'recovering'].includes(info.state);
      toggle.textContent = active ? '\u275a\u275a' : '\u25b6';
      toggle.setAttribute('aria-label', active ? 'Pause video' : 'Play video'); toggle.title = toggle.getAttribute('aria-label');
      status.update(info);
    },
  });
  status.retry.onclick = event => { event.stopPropagation(); manual = true; pausedVisit = false; controller.retry(); };
  toggle.onclick = event => {
    event.stopPropagation();
    if (controller.wanted && ['loading', 'playing', 'buffering', 'recovering'].includes(controller.state)) { pausedVisit = true; manual = false; controller.pause(); }
    else { manual = true; pausedVisit = false; controller.start(true); }
  };
  const update = () => {
    if (released) return;
    if (!el.isConnected) { if (el.dataset.videoAttached) el.releaseVideo(); return; }
    el.dataset.videoAttached = '1';
    if (!artworkEligible() || suspended || el.getAttribute('aria-busy') === 'true' || !el.dataset.imagesActive ||
        (!manual && (!visible || pausedVisit || reducedMotion.matches))) { if (controller.video || controller.state === 'recovering') controller.cancel('ineligible'); return; }
    controller.start();
  };
  el.setVideoVisible = value => {
    if (value && (visible || settleTimer)) return;
    clearTimeout(settleTimer);
    settleTimer = 0;
    if (value) { settleTimer = setTimeout(() => { settleTimer = 0; visible = true; update(); }, VIDEO_POLICY.settleMs); return; }
    visible = false; manual = false; pausedVisit = false; controller.cancel('offscreen');
  };
  el.resumeVideo = update;
  el.stopVideo = () => controller.cancel('navigation');
  el.videoOnline = () => controller.online();
  el.releaseVideo = () => { if (released) return; released = true; clearTimeout(settleTimer); controller.release(); visibility.unobserve(stage); artworkOwners.delete(stage); cards.delete(el); status.box.remove(); };
  cards.add(el); artworkOwners.set(stage, el);
  if (!visibility || viewportTop !== artworkViewportTop()) observeArtwork();
  else visibility.observe(stage);
  controller.setItem(null);
  return next => {
    const changed = item?.id !== next?.id || item?.videoUrl !== next?.videoUrl;
    item = next;
    if (changed) { manual = false; pausedVisit = false; }
    controller.setItem(next?.type === 'video' && next.videoUrl ? { id: String(next.id), source: next.videoUrl,
      poster: main.naturalWidth && !main.classList.contains('image-pending') ? main.currentSrc || main.src : '' } : null);
    toggle.hidden = !(next?.type === 'video' && next.videoUrl);
    el.classList.toggle('is-video', !toggle.hidden); update();
  };
}

export function showDetailVideo(artwork, url, imageId = '', { muted = true } = {}) {
  clearDetailVideo();
  if (!url) return;
  const owner = artwork.closest('dialog');
  const status = feedback(owner, 'detail-video-status');
  const controller = createMediaLifecycle({ muted,
    createVideo: () => { const v = document.createElement('video'); v.id = 'detailVideo'; v.controls = true; return v; },
    mount: v => artwork.after(v),
    eligible: () => !!owner?.open && visibleElement(owner),
    diagnose: diagnostic('details', () => ({ id: imageId })),
    render(info) {
      status.update(info);
      artwork.hidden = !!info.video;
      owner.dataset.videoState = info.state;
      // The poster's "Loading image" overlay waits for an image event that a slow or
      // abandoned poster never sends. Playback, or its own error, supersedes it.
      if (info.state === 'playing' || info.state === 'failed') owner.classList.remove('detail-loading');
    },
  });
  controller.setItem({ id: String(imageId || url), source: url,
    poster: artwork.naturalWidth && !artwork.classList.contains('image-pending') ? artwork.currentSrc || artwork.src : '' });
  status.retry.onclick = () => controller.state === 'awaiting-gesture' ? controller.start(true) : controller.retry();
  detail = { controller, artwork, status, refresh() {
    if (document.hidden || !owner.open) controller.cancel('ineligible');
    else if (controller.wanted && controller.state === 'canceled') controller.start();
  } };
  controller.start();
}
export function clearDetailVideo() {
  if (!detail) return;
  const old = detail; detail = null;
  old.controller.release(); old.status.box.remove(); old.artwork.hidden = false;
}
