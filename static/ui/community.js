import { api } from './api.js';
import { createCreatorCard } from './cards.js';
import { bindDialog } from './dialog.js';

const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const post = (artwork, idempotencyKey) => api('/api/community/recommend', {method:'POST', body:JSON.stringify({artwork, idempotencyKey})});

export function mountCommunity() {
  const tab = document.createElement('button');
  tab.id = 'tabCommunity'; tab.textContent = 'Community Gallery'; tab.setAttribute('aria-pressed', 'false');
  document.getElementById('tabDiscovery').after(tab);
  const section = document.createElement('section'); section.id = 'community'; section.className = 'discovery hidden';
  section.innerHTML = `<div class="community-heading"><h2>Community Gallery</h2><div class="community-tools"><button id="communityRecommend" type="button" class="primary-button" aria-haspopup="dialog">Recommend Art</button><button id="communityRefresh" type="button">Refresh</button><span id="communityQuota" role="status"></span></div></div><p id="communityMessage" class="discovery-message" role="status" aria-live="polite"></p><div id="communityGrid" class="gallery"></div><button id="communityMore" type="button" class="hidden">Load more</button><details id="communityDiagnostics" class="community-diagnostics hidden"><summary>Development diagnostics</summary><p id="communityMode"></p><div id="communityMemberHost"></div><p id="communityCounts"></p></details>`;
  document.querySelector('main').append(section);
  document.getElementById('discovery').append(document.getElementById('communityDiagnostics'));
  const dialog = document.createElement('dialog'); dialog.className = 'community-dialog';
  dialog.innerHTML = `<h2 id="communityDialogTitle">Recommend Art</h2><form id="communityForm"><p class="community-rules">Recommend someone else's public artwork. Each member can recommend three artworks per week; each artist can be featured three times. Resets Monday at 00:00 UTC.</p><p id="communityFormQuota"></p><label for="communityLink">Civitai artwork URL or image ID</label><div class="community-input"><input id="communityLink" maxlength="512" autocomplete="off" placeholder="https://civitai.red/images/12345" aria-describedby="communityDialogMessage" required autofocus></div><p id="communityDialogMessage" role="status" aria-live="polite"></p><div class="loading-actions"><button id="communitySubmit" type="submit" class="primary-button">Submit Recommendation</button><button id="communityCancel" type="button">Close</button></div></form>`;
  document.body.append(dialog);
  bindDialog(dialog, {labelledBy:'communityDialogTitle', closeButtons:[dialog.querySelector('#communityCancel')]});
  const el = id => document.getElementById(id);
  const message = value => { el('communityMessage').textContent = value; };
  let cursor = null, generation = 0, busy = false, draftVersion = 0, requestKey = null, requestValue = null;
  const quota = value => { if (value) {
    el('communityQuota').textContent = `${value.remaining} left this week`;
    el('communityFormQuota').textContent = `${value.used} of ${value.limit} recommendations used this week · ${value.remaining} remaining`;
  } };
  const observer = new IntersectionObserver(entries => {
    for (const entry of entries) if (entry.isIntersecting) {
      observer.unobserve(entry.target);
      entry.target.prepareArtwork().catch(() => entry.target.remove());
    }
  }, {rootMargin:'300px'});
  function clearCards() {
    for (const card of el('communityGrid').children) { observer.unobserve(card); card.releaseVideo?.(); }
    el('communityGrid').replaceChildren();
  }
  function showDetail(art, artist) {
    window.showDetails({...art,community:true},artist);
  }
  function makeCard(row) {
    const art = {...row.artwork, civitaiUrl:row.artwork.imageUrl, createdAt:row.submittedAt};
    const artist = {username:art.username, userId:art.creatorId, profileUrl:art.profileUrl,
      representative:art, imageCount:1, following:false};
    const card = createCreatorCard(artist, {
      api, escapeHtml, avatar:a => `<span class="avatar-fallback">${escapeHtml(a.username.slice(0,2))}</span>`,
      wireAvatarFallback:()=>{}, applyCreatorFollowers:()=>{}, checkImageTags:async()=>({known:true,hidden:false}),
      tagsHideImage:()=>false, hydrateReactionStates:async()=>{}, reactionBar:()=>'', showDetails:showDetail,
      toast:message, ago:value=>value, imageTagState:new Map(), imageReactionState:new Map(),
      cardImageObserver:observer, seenObserver:{observe:()=>{},unobserve:()=>{}}, pendingSeen:new Set(),
      loadMore:()=>{}, canWrite:()=>false, canManageCollections:()=>false, canManageContentControls:()=>false,
      date:new Date(row.submittedAt).toLocaleDateString('en-CA'), segment:'all', models:''
    });
    card.querySelector('.follow-button').hidden = true;
    const unavailable = document.createElement('span'); unavailable.className = 'community-thumbnail-note';
    unavailable.textContent = art.thumbnailUrl ? 'Thumbnail unavailable. Artwork metadata is still available.' : 'No thumbnail URL in the local archive';
    unavailable.hidden = !!art.thumbnailUrl;
    card.querySelector('.image-stage').append(unavailable);
    card.querySelector('.image-button img').addEventListener('error', () => { unavailable.hidden = false; });
    const credit = document.createElement('p'); credit.className = 'community-credit';
    credit.textContent = `Recommended by ${row.recommender} · ${new Date(row.submittedAt).toLocaleDateString()}`;
    card.append(credit); return card;
  }
  async function load(more = false, refresh = false) {
    const id = ++generation;
    if (!more) { cursor = null; clearCards(); el('communityMore').classList.add('hidden'); }
    message('Loading community recommendations…'); el('communityMore').disabled = true;
    try {
      const status = await api(`/api/community/status${refresh ? '?refresh=1' : ''}`);
      if (id !== generation) return;
      quota(status.quota);
      if (el('communityMember')) el('communityMember').value = String(status.member.id);
      el('communityMode').textContent = status.development
        ? 'DEVELOPMENT SIMULATOR ? Real OAuth session gates access; this Community ledger uses ten simulated members. Artwork submission checks use read-only Civitai calls. No public recommendations.'
        : 'Civitai identity verified for each request. Your personal galleries stay on this computer.';
      const result = await api(`/api/community/gallery?refresh=${refresh ? '1' : '0'}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`);
      if (id !== generation) return;
      if (result.controls) window.dispatchEvent(new CustomEvent('shared-controls-summary', {detail:result.controls}));
      for (const row of result.items) el('communityGrid').append(makeCard(row));
      cursor = result.nextCursor;
      el('communityMore').classList.toggle('hidden', !cursor);
      const cached = result.cache;
      if (result.counts) el('communityCounts').textContent = `This page: ${result.counts.acceptedPage} records; ${result.counts.contentControls} filtered; ${result.counts.unavailableMetadata} unavailable; ${result.counts.returned} returned. More pages: ${result.nextCursor ? 'yes' : 'no'}.`;
      el('communityMode').textContent += cached ? ` · ${cached.state === 'stale' ? 'Cached results · refresh pending' : 'Metadata cached'} · ${new Date(cached.verifiedAt * 1000).toLocaleString()}` : '';
      message(el('communityGrid').children.length ? '' : 'No recommendations match your content controls yet. Spotlight an artist to get things started.');
      if (result.metadataRefreshing || cached?.refreshing) {
        if (!el('communityGrid').children.length) message('Checking artwork metadata against your content controls…');
        setTimeout(() => { if (id === generation && !section.classList.contains('hidden')) load(); }, 2500);
      }
    } catch (error) { if (id === generation) message(error.message); }
    finally { if (id === generation) el('communityMore').disabled = false; }
  }
  function recommend(value = '') {
    if (busy || !authorized) return;
    ++draftVersion;
    el('communityLink').value = String(value);
    el('communityDialogMessage').textContent = '';
    requestKey = null; requestValue = null;
    if (!dialog.open) dialog.showModal();
    el('communityLink').focus();
  }
  el('communityForm').onsubmit = async event => {
    event.preventDefault();
    if (busy) return;
    const value = el('communityLink').value.trim(), version = draftVersion;
    if (requestValue !== value || !requestKey) { requestKey = crypto.randomUUID(); requestValue = value; }
    busy = true;
    el('communitySubmit').disabled = true; el('communityLink').readOnly = true;
    el('communitySubmit').textContent = 'Submitting?';
    el('communityForm').setAttribute('aria-busy', 'true');
    el('communityDialogMessage').textContent = 'Verifying artwork and submitting recommendation?';
    try {
      const result = await post(value, requestKey); quota(result.quota);
      if (version !== draftVersion) return;
      await load();
      if (version !== draftVersion) return;
      dialog.close(); el('communityLink').value = '';
      message('Recommendation accepted. Thanks for spotlighting another artist!');
    } catch (error) {
      if (version !== draftVersion) return;
      el('communityDialogMessage').textContent = error.message;
      api('/api/community/status').then(result => quota(result.quota)).catch(() => {});
    } finally {
      busy = false; el('communitySubmit').disabled = false; el('communityLink').readOnly = false;
      el('communitySubmit').textContent = 'Submit Recommendation'; el('communityForm').removeAttribute('aria-busy');
    }
  };
  dialog.addEventListener('close', () => { ++draftVersion; });
  let authorized = false, configured = false;
  const enter = () => { if (authorized) window.showView('community'); };
  tab.onclick = enter;
  async function configure() {
    if (configured) return;
    configured = true;
    let config;
    try { config = await api('/api/community/config'); }
    catch (error) { configured = false; throw error; }
    if (!authorized) { configured = false; return; }
    if (!config.development) return;
    el('communityDiagnostics').classList.remove('hidden');
    const label = document.createElement('label'); label.className = 'community-member';
    label.textContent = 'Simulated member ';
    const select = document.createElement('select'); select.id = 'communityMember';
    for (const member of config.members) {
      const option = document.createElement('option'); option.value = String(member.id); option.textContent = member.username; select.append(option);
    }
    label.append(select); el('communityMemberHost').append(label);
    select.onchange = async () => {
      select.disabled = true;
      try { await api('/api/community/development/member', {method:'POST', body:JSON.stringify({member:Number(select.value)})}); }
      catch (error) { message(error.message); }
      finally { select.disabled = false; }
    };
  }
  window.addEventListener('discovery-auth-changed', ({detail}) => {
    authorized=!!detail.connected;
    tab.disabled=!authorized;
    if (authorized) configure().catch(() => {});
    else { ++generation; ++draftVersion; clearCards(); dialog.close(); section.classList.add('hidden'); }
  });
  el('communityRecommend').onclick = () => recommend();
  el('communityRefresh').onclick = () => load(false, true); el('communityMore').onclick = () => load(true);
  // A settings/account change invalidates all rendered previews and the pending draft.
  window.addEventListener('community-controls-changed', () => {
    ++generation; ++draftVersion; clearCards(); dialog.close();
    if (!section.classList.contains('hidden')) load();
  });
  window.addEventListener('content-controls-changing', () => { ++generation; ++draftVersion; clearCards(); dialog.close(); });
  window.CivitaiCommunity = Object.freeze({load, recommend});
}
