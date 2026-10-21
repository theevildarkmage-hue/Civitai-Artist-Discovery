import { api } from './api.js';
import { bindDialog } from './dialog.js';
import { showGallerySkeleton, showPageError } from './feedback.js';

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
  const message = value => { el('communityMessage').setAttribute('role','status'); el('communityMessage').textContent = value; };
  let cursor = null, generation = 0, busy = false, draftVersion = 0, requestKey = null, requestValue = null;
  const quota = value => { if (value) {
    el('communityQuota').textContent = `${value.remaining} left this week`;
    el('communityFormQuota').textContent = `${value.used} of ${value.limit} recommendations used this week · ${value.remaining} remaining`;
  } };
  const artists = new Map(), recommendations = new Map();
  function clearCards() {
    for (const card of el('communityGrid').children) window.DiscoveryGallery.releaseCard(card);
    artists.clear(); recommendations.clear(); el('communityGrid').replaceChildren();
  }
  function appendRecommendations(rows) {
    const addedArtists = [], addedCards = [], artworks = [];
    for (const row of rows) {
      const id = String(row.artwork.id);
      if (recommendations.has(id)) continue;
      recommendations.set(id, row);
      const art = {...row.artwork, community:true, civitaiUrl:row.artwork.imageUrl};
      const key = art.creatorId ? `id:${art.creatorId}` : `name:${art.username.toLowerCase()}`;
      artworks.push(art);
      const existing = artists.get(key);
      if (existing?.isConnected) { existing.appendImages([art]); continue; }
      const artist = {username:art.username, userId:art.creatorId, profileUrl:art.profileUrl,
        representative:art, imageCount:1, following:false};
      const credit = document.createElement('p'); credit.className = 'community-credit';
      const card = window.DiscoveryGallery.createCard(artist, {
        images:[art], date:'', segment:'all', models:'',
        // Community has no Daily archive date or "seen on this day" persistence.
        seenObserver:{observe:()=>{},unobserve:()=>{}}, loadMore:async()=>{},
        onArtworkChange(image) {
          const recommendation = recommendations.get(String(image.id));
          credit.textContent = recommendation ? `Recommended by ${recommendation.recommender} \u00b7 ${new Date(recommendation.submittedAt).toLocaleDateString()}` : '';
        },
      });
      card.classList.add('community-card');
      card.append(credit); artists.set(key, card); el('communityGrid').append(card);
      addedArtists.push(artist); addedCards.push(card);
    }
    window.DiscoveryGallery.enrichCards(addedArtists, addedCards);
    window.DiscoveryGallery.hydrateReactionStates(artworks).catch(error => console.warn('Reaction history could not be loaded', error));
  }
  async function load(more = false, refresh = false) {
    const id = ++generation;
    if (!more) { cursor = null; clearCards(); el('communityMore').classList.add('hidden'); }
    const clearSkeleton = more ? ()=>{} : showGallerySkeleton(el('communityGrid'));
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
      clearSkeleton();
      appendRecommendations(result.items);
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
    } catch (error) { if (id === generation) showPageError(el('communityMessage'), error.message, ()=>load(more,refresh)); }
    finally { clearSkeleton(); if (id === generation) el('communityMore').disabled = false; }
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
