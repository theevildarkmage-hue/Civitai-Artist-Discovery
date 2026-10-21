// One transport contract for all pages. Callers may pass AbortSignal via options.
const controlsChannel = typeof BroadcastChannel === 'function' ? new BroadcastChannel('discovery-content-controls-v1') : null;
if (controlsChannel) controlsChannel.onmessage = ({data}) => {
  if (data?.phase === 'changing') window.dispatchEvent(new Event('content-controls-changing'));
  if (data?.phase === 'changed') {
    if (Array.isArray(data.settings?.browsingLevels)) window.dispatchEvent(new CustomEvent('shared-settings-changed', {detail:data.settings}));
    window.dispatchEvent(new Event('community-controls-changed'));
  }
};
export async function api(url, options = {}) {
  const controlChange = options.method === 'POST' && ['/api/settings', '/api/oauth/disconnect', '/api/oauth/client', '/api/content-controls/hide-artist', '/api/community/development/member'].includes(url);
  if (controlChange) {
    window.dispatchEvent(new Event('content-controls-changing'));
    controlsChannel?.postMessage({phase:'changing'});
  }
  const headers = new Headers(options.headers);
  if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  let response;
  try { response = await fetch(url, { ...options, headers }); }
  catch (error) {
    if (controlChange) {
      controlsChannel?.postMessage({phase:'changed',settings:null});
      window.dispatchEvent(new Event('community-controls-changed'));
    }
    if (url.startsWith('/api/community/')) throw Object.assign(new Error('The community service is temporarily unavailable. Your personal galleries still work.'), {code:'UNAVAILABLE',status:503});
    throw error;
  }
  const body = await response.json();
  if (controlChange) {
    const settings = url === '/api/settings' && response.ok ? {browsingLevels:body.browsingLevels, contentRating:body.contentRating} : null;
    controlsChannel?.postMessage({phase:'changed',settings});
    window.dispatchEvent(new Event('community-controls-changed'));
  }
  if (!response.ok) throw Object.assign(new Error(body.error || `HTTP ${response.status}`), {code:body.code, status:response.status});
  return body;
}
