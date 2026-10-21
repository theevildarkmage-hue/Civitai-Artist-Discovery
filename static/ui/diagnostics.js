// Best-effort local logging. A failure to log must never interrupt the gallery.
const pending = [];
const recent = new Map();
let timer = 0;

function flush(keepalive = false) {
  clearTimeout(timer); timer = 0;
  const events = pending.splice(0, 10);
  if (!events.length) return;
  const controller = new AbortController();
  const deadline = setTimeout(() => controller.abort(), 5000);
  fetch('/api/diagnostics/browser', { method: 'POST', keepalive,
    signal: controller.signal, headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ events })
  }).then(response => response.text()).catch(() => {}).finally(() => clearTimeout(deadline));
  if (pending.length) timer = setTimeout(flush, 250);
}

export function reportClientEvent(event, details = {}) {
  const record = { event, ...details };
  if (record.url) {
    try {
      const url = new URL(record.url);
      record.url = url.protocol === 'https:' && url.hostname === 'image.civitai.com'
        ? `${url.origin}${url.pathname.slice(0, 400)}` : '';
    } catch (_) { record.url = ''; }
  }
  const signature = JSON.stringify(record), now = Date.now();
  if (recent.has(signature) && now - recent.get(signature) < 30_000) return;
  recent.set(signature, now);
  if (recent.size > 256) recent.delete(recent.keys().next().value);
  if (pending.length < 100) pending.push(record);
  if (!timer) timer = setTimeout(flush, 250);
}

export function artworkContext(image) {
  const card = image.closest('.creator-card');
  const detailId = image.closest('dialog')?.querySelector('#fullLink')?.getAttribute('href')?.match(/\/images\/(\d+)/)?.[1];
  return { imageId: card?.dataset.id || detailId || '', username: card?.dataset.username || '',
    date: card?.dataset.seenDate || '',
    surface: image.closest('dialog') ? 'details' : card?.classList.contains('tm-card') ? 'time-machine' : 'gallery' };
}

window.addEventListener('pagehide', () => flush(true));
