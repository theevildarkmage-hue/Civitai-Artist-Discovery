// One transport contract for all pages. Callers may pass AbortSignal via options.
const controlsChannel = typeof BroadcastChannel === 'function' ? new BroadcastChannel('discovery-content-controls-v1') : null;
if (controlsChannel) controlsChannel.onmessage = ({data}) => {
  if (data?.phase === 'changing') window.dispatchEvent(new Event('content-controls-changing'));
  if (data?.phase === 'changed') {
    if (Array.isArray(data.settings?.browsingLevels)) window.dispatchEvent(new CustomEvent('shared-settings-changed', {detail:data.settings}));
    window.dispatchEvent(new Event('community-controls-changed'));
    if (data.artistHidden) window.dispatchEvent(new Event('shared-artist-hidden'));
  }
};
export function finishControlChange(settings=null, artistHidden=false) {
  controlsChannel?.postMessage({phase:'changed',settings,artistHidden});
  window.dispatchEvent(new Event('community-controls-changed'));
  if(artistHidden)window.dispatchEvent(new Event('shared-artist-hidden'));
}
export async function api(url, options = {}) {
  const controlChange = options.method === 'POST' && ['/api/settings', '/api/oauth/disconnect', '/api/oauth/client', '/api/content-controls/hide-artist', '/api/community/development/member'].includes(url);
  if (controlChange) {
    window.dispatchEvent(new Event('content-controls-changing'));
    controlsChannel?.postMessage({phase:'changing'});
  }
  const { controlBatch=false, signal, timeoutMs = String(url).startsWith('/api/history/image?') ? 90000 : 60000, ...request } = options;
  const controller = new AbortController();
  let timedOut = false, settings = null, succeeded = false;
  const abort = () => controller.abort(signal?.reason);
  if (signal?.aborted) abort();
  else signal?.addEventListener('abort', abort, {once: true});
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
  const headers = new Headers(request.headers);
  if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  try {
    // Retries belong to bounded server reads; never replay a browser mutation.
    const response = await fetch(url, { ...request, headers, signal: controller.signal });
    const body = await response.json().catch(() => null);
    if (!response.ok) throw Object.assign(new Error(body?.error || `The app returned HTTP ${response.status}. Try again shortly.`), {code:body?.code,status:response.status});
    if (body === null) throw new Error('The app returned an unreadable response. Please try again.');
    succeeded = true;
    if (controlChange && url === '/api/settings') settings = {browsingLevels:body.browsingLevels,contentRating:body.contentRating,applyTagFilters:body.applyTagFilters,applyAccountFilters:body.applyAccountFilters,applyImageFilters:body.applyImageFilters};
    return body;
  } catch (error) {
    if (timedOut) throw Object.assign(new Error('The request timed out. Your saved gallery is safe; please try again.'), {code:'UNAVAILABLE',status:503});
    if (url.startsWith('/api/community/') && error.name !== 'AbortError' && !error.status) throw Object.assign(new Error('The community service is temporarily unavailable. Your personal galleries still work.'), {code:'UNAVAILABLE',status:503});
    throw error;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', abort);
    if (controlChange && !controlBatch) {
      const artistHidden = succeeded && url === '/api/content-controls/hide-artist';
      finishControlChange(settings,artistHidden);
    }
  }
}
