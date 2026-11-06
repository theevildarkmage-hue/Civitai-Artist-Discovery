// Gallery, Time Machine, and details share preview fallback and loading feedback.
import { artworkContext, reportClientEvent } from './diagnostics.js';
// Visibility checks belong to the caller: never assign artwork before they succeed.
export function cardPreviewUrl(source, width, density = 1) {
  if (!source || !Number.isFinite(width) || width <= 0) return source;
  let url;
  try { url = new URL(source); } catch (_) { return source; }
  // Only change the CDN transformation format already produced by the archive.
  // Originals, local fixtures, data URLs, and other transformation options stay intact.
  if (url.protocol !== 'https:' || url.hostname !== 'image.civitai.com' ||
      !/\/width=\d+\/[^/]+$/.test(url.pathname)) return source;
  const required = width * Math.min(2, Math.max(1, density || 1));
  const target = [384, 512, 768, 1024, 1280].find(value => value >= required) || 1280;
  url.pathname = url.pathname.replace(/\/width=\d+\//, `/width=${target}/`);
  return url.href;
}

// Runtime identity includes the original source: a changed URL gets a fresh chance.
const unavailable = new Set(), checks = new Map();
let checksStarted = 0, checking = 0;
const waiting = [];
export const artworkKey = item => JSON.stringify([String(item.id || ''), item.url || '']);
export const isArtworkUnavailable = item => unavailable.has(artworkKey(item));
async function classify(key, sources, active) {
  sources = [...new Set(sources.filter(Boolean))];
  const cached = checks.get(key);
  if (cached && Date.now() - cached.time < 60000) return cached.promise;
  if (checksStarted >= 64) return false;
  checksStarted++;
  const promise = (async () => {
    if (checking >= 2) await new Promise(resolve => waiting.push(resolve));
    checking++;
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 8000);
    try {
      if (!active()) return false;
      const response = await fetch('/api/artwork/availability', { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, signal: controller.signal,
        body: JSON.stringify({ sources }) });
      const result = response.ok ? await response.json() : null;
      return result?.unavailable === true && sources.length > 0 && result.statuses?.length === sources.length &&
        result.statuses.every(status => status === 404 || status === 410);
    } catch (_) { return false; }
    finally { clearTimeout(timer); checking--; waiting.shift()?.(); }
  })();
  checks.set(key, {time: Date.now(), promise});
  if (checks.size > 512) checks.delete(checks.keys().next().value);
  return promise;
}

export function showCardArtwork(image, previewUrl, originalUrl, item = {}) {
  const bounds = image.getBoundingClientRect();
  const visible = bounds.bottom > 0 && bounds.top < innerHeight;
  image.loading = visible ? 'eager' : 'lazy';
  image.fetchPriority = visible ? 'high' : 'low';
  showArtwork(image, cardPreviewUrl(previewUrl, bounds.width, devicePixelRatio), originalUrl, item);
}

export function showArtwork(image, previewUrl, originalUrl, item = {}) {
  const preview = previewUrl || originalUrl || '';
  const key = artworkKey({id: item.id || image.closest('.creator-card')?.dataset.id, url: originalUrl || preview});
  if (image.artworkScope?.key === key && image.dataset.previewUrl === preview) return;
  image.cancelArtwork?.();
  const scope = {key};
  image.artworkScope = scope;
  const diagnostic = () => ({...artworkContext(image), ...(item.id ? {imageId: item.id} : {})});
  let cleanup = () => {}, timer, retryButton;
  const valid = () => image.artworkScope === scope && image.isConnected;
  image.cancelArtwork = () => {
    cleanup(); clearTimeout(timer); retryButton?.remove();
    if (image.artworkScope === scope) delete image.artworkScope;
  };
  image.dataset.previewUrl = preview;
  image.dataset.fallbackUrl = originalUrl || '';
  image.decoding = 'async';
  function missing() {
    if (!valid()) return;
    cleanup(); clearTimeout(timer); retryButton?.remove();
    unavailable.add(key);
    if (unavailable.size > 2048) unavailable.delete(unavailable.values().next().value);
    reportClientEvent('image_load_failed', {...diagnostic(), reason: 'confirmed-unavailable', url: originalUrl});
    image.dispatchEvent(new CustomEvent('artwork-unavailable', {bubbles: true,
      detail: {key, id: item.id || image.closest('.creator-card')?.dataset.id, url: originalUrl}}));
  }
  function attempt(retried = false) {
    if (!valid()) return;
    retryButton?.remove();
    const plan = [...new Set([preview, originalUrl].filter(Boolean))];
    let generation = 0;
    function next() {
      cleanup();
      if (!valid()) return;
      const source = plan.shift();
      if (!source) { terminal(); return; }
      const version = ++generation;
      const matches = () => valid() && version === generation &&
        image.src === new URL(source, location.href).href && (!image.currentSrc || image.currentSrc === image.src);
      image.classList.remove('image-error'); image.classList.add('image-pending');
      const loaded = () => {
        if (!matches() || !image.naturalWidth) return;
        cleanup(); image.classList.remove('image-error', 'image-pending');
        if (source !== preview || retried) reportClientEvent('image_load_recovered', {
          ...diagnostic(), reason: source !== preview ? 'original-loaded-after-preview-failed' : 'retry-loaded', url: source});
      };
      const failed = () => {
        if (!matches() || !image.complete || image.naturalWidth) return;
        reportClientEvent('image_load_failed', {...diagnostic(),
          reason: plan.length ? 'preview-failed-trying-original' : source !== preview ? 'original-failed' : 'image-failed', url: source});
        generation++; next();
      };
      cleanup = () => { image.removeEventListener('load', loaded); image.removeEventListener('error', failed); };
      image.addEventListener('load', loaded); image.addEventListener('error', failed);
      image.src = source;
    }
    async function terminal() {
      image.classList.remove('image-pending'); image.classList.add('image-error');
      const sources = Array.isArray(item.availabilitySources) ? item.availabilitySources : [preview, originalUrl];
      if (await classify(key, sources, valid)) { if (valid()) missing(); return; }
      if (!valid()) return;
      if (!retried) { timer = setTimeout(() => attempt(true), 1500); return; }
      // Ambiguous failures keep the artist; only an explicit click starts another budget.
      retryButton = document.createElement('button'); retryButton.className = 'artwork-retry';
      retryButton.textContent = 'Could not load image. Retry';
      retryButton.onclick = event => { event.stopPropagation(); attempt(false); };
      (image.closest('.image-stage') || image.parentElement).append(retryButton);
    }
    next();
  }
  if (unavailable.has(key)) queueMicrotask(missing);
  else attempt();
}

const posterFailures = new Map(), workingPosters = new Map();
const POSTER_TIMEOUT_MS = 15000;
function boundedSet(map, key, value) {
  map.delete(key); map.set(key, value);
  if (map.size > 256) map.delete(map.keys().next().value);
}

// A poster error never starts a media request. Content checks remain the caller's job.
export function showVideoPoster(image, item) {
  const candidates = [...new Set(item.thumbnailCandidates || [item.detailImageUrl || item.thumbnailUrl])].filter(Boolean);
  const key = JSON.stringify([item.id, item.url, candidates]);
  if (image.videoPosterScope?.key === key) return;
  const old = image.videoPosterScope;
  const reusable = old?.id === item.id && old.original === item.url && image.naturalWidth > 0 &&
    (candidates.includes(old.url) || old.url === old.candidates.at(-1)) ? old.url : workingPosters.get(key);
  image.cancelArtwork?.();
  const scope = { key, id: item.id, original: item.url, candidates, url: '', generation: 0, failed: false };
  image.videoPosterScope = scope;
  const stage = image.closest('.image-stage');
  let cleanup = () => {};
  image.cancelArtwork = () => {
    scope.generation++; cleanup();
    if (image.videoPosterScope === scope) { delete image.videoPosterScope; image.removeAttribute('src'); }
    stage?.classList.remove('video-preview-unavailable');
  };
  image.decoding = 'async';
  image.loading = 'eager';
  image.classList.remove('image-error');
  image.classList.add('image-pending');
  stage?.classList.remove('video-preview-unavailable');
  const plan = [...new Set([reusable, ...candidates])].filter(Boolean);
  function next() {
    cleanup();
    if (image.videoPosterScope !== scope) return;
    const url = plan.shift();
    if (!url) {
      image.classList.remove('image-pending'); image.classList.add('image-error');
      image.removeAttribute('src'); stage?.classList.add('video-preview-unavailable');
      return;
    }
    if (Date.now() - (posterFailures.get(url) || 0) < 300000) { next(); return; }
    scope.url = url;
    const generation = ++scope.generation;
    const valid = () => image.videoPosterScope === scope && generation === scope.generation;
    const matchingSource = () => !image.currentSrc || image.currentSrc === image.src;
    const loaded = () => {
      if (!valid() || !matchingSource() || !image.naturalWidth) return;
      cleanup(); image.classList.remove('image-pending', 'image-error');
      boundedSet(workingPosters, key, url);
      if (scope.failed) reportClientEvent('image_load_recovered', { ...artworkContext(image), reason: 'video-poster-loaded', url });
    };
    const failed = (deadline = false) => {
      if (!valid() || (deadline !== true && !matchingSource())) return;
      scope.failed = true;
      boundedSet(posterFailures, url, Date.now());
      reportClientEvent('image_load_failed', { ...artworkContext(image), reason: 'video-poster-failed', url });
      scope.generation++; image.removeAttribute('src'); next();
    };
    const timer = setTimeout(() => failed(true), POSTER_TIMEOUT_MS);
    cleanup = () => { clearTimeout(timer); image.removeEventListener('load', loaded); image.removeEventListener('error', failed); };
    image.addEventListener('load', loaded); image.addEventListener('error', failed);
    image.src = url;
  }
  next();
}
