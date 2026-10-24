import { requestKey as secureRequestKey } from './request-key.js';
import { api } from './api.js';
import { bindDialog } from './dialog.js';
import { showGallerySkeleton } from './feedback.js';
import { createInfinitePager } from './pagination.js';

const post = (artwork, idempotencyKey) => api('/api/community/recommend', {method:'POST', body:JSON.stringify({artwork, idempotencyKey})});
const disabledNotice = 'Community Beta is invite-only at this time and is currently disabled or under maintenance. Your Daily Gallery still works.';
// Temporarily remove the reporting entry point; backend moderation stays intact.
const reportingUiEnabled = false;

export function mountCommunity() {
  const tab = document.createElement('button');
  tab.id = 'tabCommunity'; tab.textContent = 'Community Gallery'; tab.setAttribute('aria-pressed', 'false');
  tab.hidden = true;tab.classList.add('hidden');
  document.getElementById('tabDiscovery').after(tab);
  const section = document.createElement('section'); section.id = 'community'; section.className = 'discovery hidden';
  section.innerHTML = `<div class="community-heading"><h2>Community Gallery</h2><p id="communityUpdates" class="community-updates" role="status" aria-live="polite" aria-atomic="true"></p><div class="community-tools"><button id="communityRecommend" type="button" class="primary-button" aria-haspopup="dialog">Recommend Art</button><button id="communityRefresh" type="button">Refresh</button><span id="communityQuota" role="status"></span></div></div><p id="communityMessage" class="discovery-message" role="status" aria-live="polite"></p><div id="communityGrid" class="gallery"></div><div id="communitySentinel" class="load-sentinel"></div><details id="communityDiagnostics" class="community-diagnostics hidden"><summary>Development diagnostics</summary><p id="communityMode"></p><div id="communityMemberHost"></div><p id="communityCounts"></p></details>`;
  document.querySelector('main').append(section);
  document.getElementById('discovery').append(document.getElementById('communityDiagnostics'));
  const dialog = document.createElement('dialog'); dialog.className = 'community-dialog';
  dialog.innerHTML = `<h2 id="communityDialogTitle">Recommend Art</h2><form id="communityForm"><p class="community-rules">Recommend someone else's public artwork. Each member can recommend three artworks per week; each artist can be featured three times. Resets Monday at 00:00 UTC.</p><p id="communityFormQuota"></p><label for="communityLink">Civitai artwork URL or image ID</label><div class="community-input"><input id="communityLink" maxlength="512" autocomplete="off" placeholder="https://civitai.red/images/12345" aria-describedby="communityDialogMessage" required autofocus></div><p id="communityDialogMessage" role="status" aria-live="polite"></p><div class="loading-actions"><button id="communitySubmit" type="submit" class="primary-button">Submit Recommendation</button><button id="communityCancel" type="button">Close</button></div></form>`;
  document.body.append(dialog);
  bindDialog(dialog, {labelledBy:'communityDialogTitle', closeButtons:[dialog.querySelector('#communityCancel')]});
  const reportDialog=document.createElement('dialog');reportDialog.className='community-dialog';
  reportDialog.innerHTML='<h2 id="communityReportTitle">Report recommendation</h2><form id="communityReportForm"><label>Reason <select id="communityReportReason"><option value="inappropriate">Inappropriate content</option><option value="abuse">Abuse</option><option value="ownership">Ownership concern</option><option value="unavailable">Artwork unavailable</option></select></label><p id="communityReportMessage" role="status"></p><button type="submit" id="communityReportSubmit">Send report</button><button type="button" id="communityReportClose">Cancel</button></form>';
  document.body.append(reportDialog);
  bindDialog(reportDialog,{labelledBy:'communityReportTitle',closeButtons:[reportDialog.querySelector('#communityReportClose')]});
  const el = id => document.getElementById(id);
  let productionBeta=false, policyControlled=true, submissions=false;
  let reporting=false,reportTarget=null,reportBusy=false;
  el('communityRecommend').disabled=true;
  el('communitySubmit').disabled=true;
  function applyPolicy(status) {
    reporting=reportingUiEnabled && status?.beta?.reporting===true && status?.cache?.state!=='stale';
    section.querySelectorAll('.community-report').forEach(button=>{button.hidden=!reporting;});
    if(!reporting&&reportDialog.open)reportDialog.close();
    if(status?.beta)policyControlled=true;
    updatesAllowed=(!policyControlled || status?.beta?.available===true) && status?.cache?.state!=='stale';
    if(!updatesAllowed)el('communityUpdates').textContent='';
    if(!policyControlled)return;
    submissions=status?.beta?.submissions===true && status?.cache?.state!=='stale';
    el('communityRecommend').disabled=!submissions;
    el('communitySubmit').disabled=busy||!submissions;
    if(!submissions&&dialog.open)dialog.close();
    if(!submissions)message(status?.beta?.available ? 'Community Beta is read-only. Your personal galleries still work.' : disabledNotice);
  }
  async function configureBeta() {
    const beta=await api('/api/community/beta');productionBeta=beta.production===true;
    policyControlled=productionBeta||beta.serverPolicy===true;
    tab.hidden=false;tab.classList.remove('hidden');
    if(!policyControlled){submissions=true;el('communityRecommend').disabled=false;el('communitySubmit').disabled=false;}
    if(productionBeta)tab.textContent='Community Beta';
  }
  configureBeta().catch(()=>{tab.hidden=false;tab.classList.remove('hidden');});
  const message = value => { el('communityMessage').setAttribute('role','status'); el('communityMessage').textContent = value; };
  function accessError(error) {
    el('communityUpdates').textContent='';
    applyPolicy(null);
    const states={
      INVITE_ONLY:"Community Beta is Invite Only\nWe're currently testing Community with a small group of invited Civitai members. Your account isn't on the invitation list yet. You can continue using the Daily Gallery normally.",
      INVITE_REVOKED:'Your Community Beta invitation was revoked. Your Daily Gallery still works.',
      INVITE_EXPIRED:'Your Community Beta invitation expired. Your Daily Gallery still works.',
      SUBMITTER_BLOCKED:'This account is blocked from Community participation. Contact the beta owner for review.',
      BETA_DISABLED:disabledNotice,
      UNAVAILABLE:'Community is temporarily unavailable. Please retry later. Your Daily Gallery still works.',
      AUTH_REQUIRED:'Sign in to Civitai to use Community.',AUTH_EXPIRED:'Please sign in to Civitai again.',
    };
    message(states[error.code]||error.message);
    // Access decisions are not failed pagination. Keep retry UI for transient
    // failures; an explicit refresh can recheck a changed policy or invitation.
    el('communitySentinel').hidden=['INVITE_ONLY','INVITE_REVOKED','INVITE_EXPIRED',
      'SUBMITTER_BLOCKED','BETA_DISABLED','AUTH_REQUIRED','AUTH_EXPIRED','FORBIDDEN'].includes(error.code);
  }
  let reviewPending=false;
  let cursor = null, generation = 0, busy = false, draftVersion = 0, requestKey = null, requestValue = null;
  const quota = value => { if (value) {
    if (value.unlimited === true) {
      el('communityQuota').textContent = 'Unlimited · owner testing';
      el('communityFormQuota').textContent = `${value.used} recommendations this week · No personal limit for owner testing. The three-per-artist weekly cap still applies.`;
      return;
    }
    el('communityQuota').textContent = `${value.remaining} left this week`;
    el('communityFormQuota').textContent = `${value.used} of ${value.limit} recommendations used this week · ${value.remaining} remaining`;
  } };
  let initialized = false, firstPage = true, refreshPage = false, metadataTimer = null, metadataRetries = 0, viewSession = null;
  // Background probes never change the rendered feed or its pagination cursor.
  let newestShown=null, lastUpdateCheck=0, updateRequest=null, updatesAllowed=false;
  function cancelUpdateCheck() { updateRequest?.abort(); updateRequest=null; }
  const requestedCursors = new Set();
  const pages = createInfinitePager({
    container:el('communityGrid'), sentinel:el('communitySentinel'),
    visible:()=>authorized && !section.classList.contains('hidden'),
    hasMore:()=>!initialized || firstPage || !!cursor,
    load:fetchPage,
    exhausted:()=>reviewPending?"Artwork checks are still running. Refresh to retry.":"You're caught up for this view.",
    onError:accessError,
  });
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
      function updateCredit(recommendation) {
        credit.replaceChildren();
        if (!recommendation) return;
        const profile = document.createElement('a');
        profile.textContent = recommendation.recommender;
        profile.href = `https://civitai.red/user/${encodeURIComponent(recommendation.recommender)}`;
        profile.target = '_blank'; profile.rel = 'noopener noreferrer';
        credit.append('Recommended by ', profile, ` \u00b7 ${new Date(recommendation.submittedAt).toLocaleDateString()}`);
      }
      const card = window.DiscoveryGallery.createCard(artist, {
        images:[art], date:'', segment:'all', models:'',
        // Community has no Daily archive date or "seen on this day" persistence.
        seenObserver:{observe:()=>{},unobserve:()=>{}}, loadMore:async()=>{},
        onArtworkChange(image) {
          const recommendation = recommendations.get(String(image.id));
          updateCredit(recommendation);
        },
      });
      card.classList.add('community-card');
      if (reportingUiEnabled) {
        const reportButton=document.createElement('button');reportButton.type='button';
        reportButton.className='community-report';reportButton.textContent='Report recommendation';reportButton.hidden=!reporting;
        reportButton.onclick=()=>{
          if(!reporting||reportBusy)return;
          reportTarget=card.dataset.id;el('communityReportMessage').textContent='Reports are reviewed by a moderator. Reporting does not remove artwork.';
          reportDialog.showModal();
        };
        card.append(reportButton);
      }
      card.append(credit); artists.set(key, card); el('communityGrid').append(card);
      addedArtists.push(artist); addedCards.push(card);
    }
    window.DiscoveryGallery.enrichCards(addedArtists, addedCards);
    window.DiscoveryGallery.hydrateReactionStates(artworks).catch(error => console.warn('Reaction history could not be loaded', error));
  }
  async function load(more = false, refresh = false) {
    if (more) return pages.request(true);
    cancelUpdateCheck();
    el('communitySentinel').hidden=false;
    ++generation; clearTimeout(metadataTimer); pages.reset();
    cursor = null; firstPage = true; refreshPage = refresh; initialized = true;
    requestedCursors.clear(); clearCards();
    return pages.request(true);
  }
  async function fetchPage({signal}) {
    const id = generation, first = firstPage, pageCursor = cursor;
    const clearSkeleton = first ? showGallerySkeleton(el('communityGrid')) : ()=>{};
    if(first)applyPolicy(null);
    if (first) message('Loading community recommendations\u2026');
    try {
      if (first) {
        const status = await api(`/api/community/status${refreshPage ? '?refresh=1' : ''}`, {signal});
        if (id !== generation || signal.aborted) return;
        quota(status.quota);
        applyPolicy(status);
        viewSession = status.viewSession || null;
        if (el('communityMember')) el('communityMember').value = String(status.member.id);
        el('communityMode').textContent = status.development
          ? 'DEVELOPMENT SIMULATOR: Real OAuth session gates access; this Community ledger uses ten simulated members. No public recommendations.'
          : 'Civitai identity verified for each request. Your personal galleries stay on this computer.';
      }
      const result = await api(`/api/community/gallery?refresh=${refreshPage ? '1' : '0'}${pageCursor ? `&cursor=${encodeURIComponent(pageCursor)}` : ''}`, {signal});
      if (id !== generation || signal.aborted) return;
      reviewPending=result.reviewPending===true;
      const next = result.nextCursor;
      if (next && (next === pageCursor || requestedCursors.has(next))) throw new Error('The service repeated a page. Refresh the gallery to try again.');
      if (pageCursor) requestedCursors.add(pageCursor);
      if (result.controls) window.dispatchEvent(new CustomEvent('shared-controls-summary', {detail:result.controls}));
      clearSkeleton(); appendRecommendations(result.items);
      if(first) {
        const dates=result.items.map(row=>Date.parse(row.submittedAt)).filter(Number.isFinite);
        newestShown=dates.length ? Math.max(...dates) : null;
        lastUpdateCheck=0;
        el('communityUpdates').textContent='';
      }
      cursor = next; firstPage = false; refreshPage = false;
      const cached = result.cache;
      if (result.counts) el('communityCounts').textContent = `This page: ${result.counts.acceptedPage} records; ${result.counts.contentControls} filtered; ${result.counts.returned} returned. More pages: ${cursor ? 'yes' : 'no'}.`;
      if (first && cached) el('communityMode').textContent += ` \u00b7 ${cached.state === 'stale' ? 'Cached results - refresh pending' : 'Metadata cached'} \u00b7 ${new Date(cached.verifiedAt * 1000).toLocaleString()}`;
      message(recommendations.size ? '' : cursor ? 'Checking more recommendations against your content controls as you scroll.' : 'No recommendations match your content controls yet. Spotlight an artist to get things started.');
      // Recheck an initially empty cache only a bounded number of times. Never reset
      // an already-rendered feed or its scroll position during background hydration.
      if (!recommendations.size && !cursor && (result.metadataRefreshing || cached?.refreshing) && metadataRetries++ < 2) {
        metadataTimer = setTimeout(()=>{ if (id === generation && !section.classList.contains('hidden')) load(); },2500);
      }
    } finally { clearSkeleton(); }
  }
  async function checkNewRecommendations() {
    if(updateRequest || pages.pending || firstPage || !updatesAllowed || !authorized ||
       document.hidden || section.classList.contains('hidden') || Date.now()-lastUpdateCheck<60000) return;
    const id=generation, controller=new AbortController(); updateRequest=controller;
    lastUpdateCheck=Date.now();
    const found=new Set(), cursors=new Set();
    let next=null, stopped=false;
    try {
      // Bound background work to four normal-size pages. A capped count is shown
      // honestly as "N+" rather than claiming to have scanned the entire feed.
      for(let page=0;page<4;page++) {
        const result=await api(`/api/community/gallery?refresh=1${next ? `&cursor=${encodeURIComponent(next)}` : ''}`,{signal:controller.signal});
        if(id!==generation || controller.signal.aborted || !authorized || !updatesAllowed || document.hidden || section.classList.contains('hidden'))return;
        if(result.cache?.state==='stale')return;
        for(const row of result.items) {
          const key=String(row.artwork.id), date=Date.parse(row.submittedAt);
          if(recommendations.has(key) || (newestShown!==null && date<newestShown)) {stopped=true;break;}
          if(Number.isFinite(date))found.add(key);
        }
        next=result.nextCursor;
        if(stopped || !next)break;
        if(cursors.has(next))throw new Error('Repeated update cursor');
        cursors.add(next);
      }
      const count=found.size, capped=!!next && !stopped;
      el('communityUpdates').textContent=count ? `${count}${capped?'+':''} new recommendation${count===1&&!capped?'':'s'} \u2014 Refresh to Show` : '';
    } catch(error) {
      if(id!==generation || controller.signal.aborted)return;
      // A transient probe failure must not replace cards, scroll or foreground
      // error state. Explicit access denials still close privileged UI promptly.
      if(['INVITE_ONLY','INVITE_REVOKED','INVITE_EXPIRED','SUBMITTER_BLOCKED','BETA_DISABLED','AUTH_REQUIRED','AUTH_EXPIRED','FORBIDDEN'].includes(error.code))accessError(error);
    } finally { if(updateRequest===controller)updateRequest=null; }
  }
  async function refreshRecommendations() {
    metadataRetries=0;
    const id=generation+1;
    await load(false,true);
    if(id===generation && !firstPage && !section.classList.contains('hidden'))window.scrollTo({top:0,behavior:'instant'});
  }
  async function activate() {
    pages.resume();
    if (!initialized) return load();
    const id = generation;
    try {
      const status = await api('/api/community/status');
      if (id !== generation || section.classList.contains('hidden')) return;
      quota(status.quota);
      applyPolicy(status);
      // A restarted server has a new volatile artwork admission list. Refresh the
      // projection then; ordinary tab switches still keep their pages and scroll.
      if (viewSession && status.viewSession && viewSession !== status.viewSession) return load();
      await checkNewRecommendations();
    } catch (error) { if (id === generation) accessError(error); }
  }
  function deactivate() {
    ++generation; clearTimeout(metadataTimer); cancelUpdateCheck(); pages.pause();
  }
  function invalidate() {
    deactivate(); initialized = false; cursor = null; firstPage = true;
    ++draftVersion; clearCards(); dialog.close();reportDialog.close();reporting=false;reportTarget=null;
    newestShown=null;el('communityUpdates').textContent='';
  }
  function recommend(value = '') {
    if (busy || !authorized || !submissions) return;
    ++draftVersion;
    el('communityLink').value = String(value);
    el('communityDialogMessage').textContent = '';
    requestKey = null; requestValue = null;
    if (!dialog.open) dialog.showModal();
    el('communityLink').focus();
  }
  el('communityForm').onsubmit = async event => {
    event.preventDefault();
    if (busy || !authorized || !submissions) return;
    const value = el('communityLink').value.trim(), version = draftVersion;
    try {if(requestValue!==value||!requestKey){requestKey=secureRequestKey();requestValue=value;}}
    catch(error){el('communityDialogMessage').textContent=error.message;return;}
    busy = true;
    el('communitySubmit').disabled = true; el('communityLink').readOnly = true;
    el('communitySubmit').textContent = 'Submitting\u2026';
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
      busy = false; el('communitySubmit').disabled = !submissions; el('communityLink').readOnly = false;
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
    else { invalidate(); section.classList.add('hidden'); }
  });
  el('communityRecommend').onclick = () => recommend();
  el('communityReportForm').onsubmit=async event=>{
    event.preventDefault();if(!reporting||reportBusy||!reportTarget)return;
    reportBusy=true;el('communityReportSubmit').disabled=true;
    try {
      await api('/api/community/report',{method:'POST',body:JSON.stringify({artwork:reportTarget,reason:el('communityReportReason').value})});
      el('communityReportMessage').textContent='Report received. Thank you.';
    } catch(error) {el('communityReportMessage').textContent=error.message;}
    finally {reportBusy=false;el('communityReportSubmit').disabled=false;}
  };
  el('communityRefresh').onclick = refreshRecommendations;
  // A settings/account change invalidates all rendered previews and the pending draft.
  window.addEventListener('community-controls-changed', () => {
    invalidate();
    if (!section.classList.contains('hidden')) activate();
  });
  window.addEventListener('content-controls-changing', invalidate);
  window.CivitaiCommunity = Object.freeze({load, recommend, activate, deactivate});
  setInterval(()=>{if(policyControlled&&authorized&&!document.hidden&&!section.classList.contains('hidden'))activate();},60000);
}
