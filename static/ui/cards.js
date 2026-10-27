import { showCardArtwork, showVideoPoster, isArtworkUnavailable } from './artwork.js';
import { toggleCreatorFollow } from './creator-actions.js';
import { attachCardVideo } from './video.js';
import { reportClientEvent } from './diagnostics.js';

let activeActionMenu = null;
let hoveredCard = null;
const carouselNavigation = new WeakMap();
const nativeArrowControl = 'input,textarea,select,video,[contenteditable]:not([contenteditable="false"]),[role="slider"],[role="spinbutton"],[role="combobox"],[role="listbox"],[role="menu"],[role="tablist"],[role="tree"],[role="grid"]';
function availableCard(el) {
  return el?.isConnected && !el.closest('.hidden,[hidden],[inert]') &&
    el.getClientRects().length > 0 && getComputedStyle(el).visibility === 'visible';
}
document.addEventListener('keydown', event => {
  if (!['ArrowLeft', 'ArrowRight'].includes(event.key) || event.defaultPrevented ||
      event.ctrlKey || event.altKey || event.metaKey || event.shiftKey || document.hidden) return;
  if (document.querySelector('dialog[open],.filter-panel:not(.hidden),.calendar-panel:not(.hidden),#preferencesMenu:not(.hidden),.card-action-menu:not(.hidden)')) return;
  const focus = document.activeElement;
  if (focus?.isContentEditable || focus?.closest(nativeArrowControl)) return;
  const focusedCard = focus?.closest('.creator-card');
  const cardFocus = focusedCard && (focus === focusedCard || focus.closest('.image-button,.carousel-arrow,.image-progress'));
  if (!availableCard(hoveredCard)) hoveredCard = null;
  const target = cardFocus && availableCard(focusedCard) ? focusedCard : hoveredCard;
  if (target && carouselNavigation.get(target)?.(event.key === 'ArrowLeft' ? -1 : 1)) event.preventDefault();
});
window.addEventListener('blur', () => { hoveredCard = null; });
document.addEventListener('visibilitychange', () => { if (document.hidden) hoveredCard = null; });
document.addEventListener('click', event => {
  if (activeActionMenu && !activeActionMenu.parentElement?.contains(event.target)) {
    activeActionMenu.classList.add('hidden');
    activeActionMenu = null;
  }
});

// Shared card behavior. Page adapters supply services and a snapshot of the archive
// being browsed; delayed carousel requests never read another page's selected date.
export function createCreatorCard(a, context) {
  const { api, escapeHtml, avatar, wireAvatarFallback, applyCreatorFollowers, checkImageTags, tagsHideImage, hydrateReactionStates, reactionBar, showDetails, toast, ago, imageTagState, imageReactionState, cardImageObserver, seenObserver, pendingSeen, loadMore } = context;
  const selectedDate = context.date, selectedSegment = context.segment;
  let images = context.images ? [...context.images] : [a.representative], index = 0, current = images[0], imagesLoaded = !!context.images || a.imageCount <= 1;
  // The listing can include a cached decision. Unknown artwork still passes through
  // checkImageTags before any preview URL is attached, including in Time Machine.
  if (current.tagState?.known) imageTagState.set(String(current.id), current.tagState);
  let navigating = false;
  const el = document.createElement("article"); el.className = a.seen ? "creator-card is-seen" : "creator-card"; el.dataset.id = current.id;
  el.addEventListener('pointerenter', () => { hoveredCard = el; });
  el.addEventListener('pointerleave', () => { if (hoveredCard === el) hoveredCard = null; });
  carouselNavigation.set(el, delta => {
    if (navigating || a.imageCount < 2) return false;
    move(delta); return true;
  });
  el.tabIndex = 0;
  el.setAttribute('role', 'group');
  el.setAttribute('aria-label', `Artwork by ${a.username}`);
  el.addEventListener('keydown', event => {
    if (event.defaultPrevented || event.ctrlKey || event.altKey || event.metaKey || event.shiftKey ||
        event.target.isContentEditable || event.target.closest('input,textarea,select,video')) return;
    if (event.code === 'Space' &&
               (event.target === el || event.target.closest('.image-button'))) {
      const toggle = el.querySelector('.video-toggle');
      if (toggle && !toggle.hidden && !navigating) { event.preventDefault(); toggle.click(); }
    }
  });
  el.innerHTML = `<header class="creator-strip"><a class="creator-identity" href="${escapeHtml(a.profileUrl)}" target="_blank" rel="noopener">${avatar(a)}<span><span class="creator-name-line"><strong>${escapeHtml(a.username)}</strong>${a.matchedTags?.length ? `<span class="match-badge" title="Ranked here because you often react to: ${escapeHtml(a.matchedTags.join(", "))}" aria-label="Matches your taste: ${escapeHtml(a.matchedTags.join(", "))}">&#10038;</span>` : ""}${a.reactedOften ? `<span class="worth-badge" title="You have reacted to ${a.reactedCount} of this artist's images but do not follow them" aria-label="You often react to this artist but do not follow them">&#9829;</span>` : ""}<span class="creator-badge"></span></span><small><span class="image-age"></span><span class="creator-followers"></span></small></span></a><div class="creator-controls"><button class="follow-button ${a.following ? "is-following" : ""}" ${context.canWrite() ? "" : "disabled"} title="${context.canWrite() ? "" : "Civitai did not grant follow and reaction access."}">${a.following ? "✓ Following" : "+ Follow"}</button><button class="more-menu">⋮</button></div></header><div class="image-stage"><button class="image-button"><img loading="lazy" alt="Artwork by ${escapeHtml(a.username)}"></button><button class="carousel-arrow previous">‹</button><button class="carousel-arrow next">›</button><div class="card-nav-status" role="status" aria-live="polite"><span class="card-nav-spinner" aria-hidden="true"></span><span>Loading image…</span></div><div class="image-overlay"><div class="reaction-slot"></div><button class="info-button">ⓘ</button></div><div class="image-progress"></div></div><footer class="creator-footer"><span class="image-position"></span><a class="open-image" target="_blank" rel="noopener">Open on Civitai ↗</a></footer>`;
  [[".previous", "Previous image", "15 5 8 12 15 19"], [".next", "Next image", "9 5 16 12 9 19"]].forEach(([selector, label, points]) => {
    const button = el.querySelector(selector);
    button.setAttribute("aria-label", label);
    button.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true"><polyline points="${points}"></polyline></svg>`;
  });
  const stage = el.querySelector('.image-stage');
  const badgeRail = document.createElement('div');
  badgeRail.className = 'card-badge-rail';
  stage.append(badgeRail);
  if (a.recommendationLabel) {
    const reason = document.createElement("span");
    reason.className = "recommendation-badge";
    reason.textContent = a.recommendationLabel;
    reason.title = (a.recommendationReasons || []).join(" · ");
    badgeRail.appendChild(reason);
  }
  for (const badge of el.querySelectorAll('.match-badge, .worth-badge, .creator-badge')) badgeRail.appendChild(badge);
  stage.append(el.querySelector('.creator-strip'));
  stage.append(el.querySelector('.more-menu'));
  stage.after(el.querySelector('.image-overlay'));
  el.querySelector('.image-button').setAttribute('aria-label', `View artwork by ${a.username}`);
  el.querySelector('.info-button').setAttribute('aria-label', 'Image details');
  el.querySelector('.more-menu').setAttribute('aria-label', `More actions for ${a.username}`);
  el.querySelector('.more-menu').setAttribute('aria-expanded', 'false');
  el.querySelector('.more-menu').innerHTML = '<svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true" fill="currentColor"><circle cx="12" cy="5" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="12" cy="19" r="1.5"/></svg>';
  const actionMenu = document.createElement('div');
  actionMenu.className = 'card-action-menu hidden';
  actionMenu.setAttribute('role', 'menu');
  actionMenu.innerHTML = `<button type="button" data-action="collections" role="menuitem">♡ Add image to collection</button><button type="button" data-action="hide" class="danger-item" role="menuitem">⊘ Hide this artist</button>`;
  el.append(actionMenu);
  const main = el.querySelector(".image-button img"), age = el.querySelector(".image-age"), reaction = el.querySelector(".reaction-slot"), position = el.querySelector(".image-position"), progress = el.querySelector(".image-progress"), open = el.querySelector(".open-image"), navStatus = el.querySelector(".card-nav-status"), navMessage = navStatus.lastElementChild, showVideo = attachCardVideo(el, main); wireAvatarFallback(el.querySelector("img.creator-avatar"), a.username);
  function renderReactions() {
    context.onArtworkChange?.(current);
    reaction.innerHTML = reactionBar(current);
    wireReactions();
    if (navigating) reaction.querySelectorAll("[data-reaction]").forEach(button => { button.disabled = true; });
  }
  function wireReactions() { reaction.querySelectorAll("[data-reaction]").forEach(button => button.onclick = async event => { event.stopPropagation(); if (!context.canWrite()) return toast("Civitai did not grant reaction access."); button.disabled = true; const targetImage = current, imageId = targetImage.id, reactionName = button.dataset.reaction, active = !button.classList.contains("selected"); try { const result = await api("/api/reaction", { method: "POST", body: JSON.stringify({ imageId, reaction: reactionName, active }) }); const stats = { ...(targetImage.stats || {}), ...(result.stats || {}) }; targetImage.stats = stats; imageReactionState.set(String(imageId), { reactions: [...(result.reactions || [])], stats }); if (context.notifyReactionState) context.notifyReactionState(imageId); else if (String(current.id) === String(imageId)) renderReactions(); toast(active ? `${reactionName} reaction added` : `${reactionName} reaction removed`); } catch (error) { toast(error.message); if (String(current.id) === String(imageId)) button.disabled = false; } }); }
  function cardDate() {
    const value = selectedDate || String(current.createdAt || '').slice(0, 10);
    const date = new Date(`${value}T12:00:00`);
    return Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
  }
  function paint() { current = images[index]; const activePosition = imagesLoaded ? index : Math.max(0, Number(a.representativeIndex) || 0); el.dataset.id = current.id; if (el.dataset.imagesActive) { if (current.type === 'video') showVideoPoster(main, current); else showCardArtwork(main, current.thumbnailUrl, current.url, current); } age.textContent = cardDate(); renderReactions(); showVideo(current); position.textContent = `${activePosition + 1} of ${a.imageCount} images${current.type === 'video' ? ' · video' : ''}`; open.href = current.civitaiUrl; const shown = imagesLoaded ? images : Array.from({ length: Math.min(a.imageCount, 40) }); const activeMarker = imagesLoaded || a.imageCount <= shown.length ? activePosition : Math.round(activePosition * (shown.length - 1) / (a.imageCount - 1)); progress.innerHTML = shown.map((_, i) => `<button class="${i === activeMarker ? "active" : ""}" data-index="${i}"></button>`).join(""); el.querySelector(".previous").hidden = a.imageCount < 2; el.querySelector(".next").hidden = a.imageCount < 2; if (imagesLoaded) progress.querySelectorAll("[data-index]").forEach(button => button.onclick = () => navigateTo(Number(button.dataset.index), 1)); }
  let alternativesLoaded = false, unavailablePending = false;
  async function ensureImages(forFailure = false) {
    if (imagesLoaded && !(forFailure && el.loadArtworkAlternatives && !alternativesLoaded)) return;
    const data = el.loadArtworkAlternatives
      ? await el.loadArtworkAlternatives()
      : await api(`/api/history/artist?date=${selectedDate}&segment=${selectedSegment}&username=${encodeURIComponent(a.username)}${context.models}`);
    if (!el.isConnected) return;
    const activeId = current.id;
    images = forFailure ? data.images : data.images.filter(image => !isArtworkUnavailable(image));
    index = Math.max(0, images.findIndex(image => image.id === activeId));
    imagesLoaded = true; alternativesLoaded = true; a.imageCount = images.length;
    hydrateReactionStates(images).catch(error => console.warn('Reaction history could not be loaded', error));
  }
  async function skipUnavailable() {
    unavailablePending = true;
    if (navigating || !el.isConnected) return;
    unavailablePending = false;
    setCardNavigationBusy(true, 'Skipping unavailable artwork...');
    try {
      await ensureImages(true);
      if (!el.isConnected) return;
      const next = images.slice(index).find(image => !isArtworkUnavailable(image));
      images = images.filter(image => !isArtworkUnavailable(image));
      a.imageCount = images.length;
      if (!images.length) { removeCard('all-artwork-confirmed-unavailable'); return; }
      index = next ? images.indexOf(next) : 0;
      el.dataset.imagesActive = '1';
      await selectAllowed(index, 1);
    } catch (error) { toast(error.message); }
    finally { if (el.isConnected) setCardNavigationBusy(false); }
  }
  main.addEventListener('artwork-unavailable', () => { skipUnavailable(); });
  el.skipUnavailableArtwork = skipUnavailable;
  function removeCard(reason) {
    const detail = { reason, username: a.username, imageId: current.id,
      date: selectedDate, segment: selectedSegment,
      surface: el.classList.contains('tm-card') ? 'time-machine' : 'gallery' };
    console.info('Gallery card removed', detail);
    reportClientEvent('card_removed', detail);
    el.releaseVideo(); main.cancelArtwork?.(); cardImageObserver.unobserve(el); seenObserver.unobserve(el); pendingSeen.delete(el);
    el.remove();
    if (el.onUnavailableCardRemoved) el.onUnavailableCardRemoved();
    else if (!el.classList.contains('tm-card') && !reason.includes('unavailable')) loadMore().catch(error => toast(error.message));
    // For media failures the existing sentinel supplies more artists when it enters
    // view. Never start an eager replacement loop for a whole missing page.
  }
  async function selectAllowed(candidate, delta = 1) {
    let skippedUnavailable = false;
    while (images.length) {
      candidate = (candidate + images.length) % images.length;
      if (isArtworkUnavailable(images[candidate])) {
        skippedUnavailable = true;
        images.splice(candidate, 1); a.imageCount = images.length;
        if (delta < 0) candidate--;
        continue;
      }
      const result = await checkImageTags(images[candidate].id);
      if (!el.isConnected) return false;
      if (!tagsHideImage(result)) { index = candidate; paint(); if (navigating) await waitForArtwork(); return true; }
      if (navigating) setCardNavigationBusy(true, 'Skipping a filtered image…');
      images.splice(candidate, 1); a.imageCount = images.length;
      if (delta < 0) candidate--;
    }
    removeCard(skippedUnavailable ? 'no-usable-artwork-after-unavailable-skip' : 'all-images-hidden-by-civitai-tags'); return false;
  }
  async function prepareArtwork() {
    setCardNavigationBusy(true, 'Loading image…');
    try {
      if (isArtworkUnavailable(current)) { unavailablePending = true; return; }
      const result = await checkImageTags(current.id);
      if (tagsHideImage(result)) {
        setCardNavigationBusy(true, 'Skipping a filtered image…');
        await ensureImages();
        images = images.filter(image => String(image.id) !== String(current.id));
        a.imageCount = images.length;
        el.dataset.imagesActive = "1";
        if (!await selectAllowed(0, 1)) return;
      } else {
        el.dataset.imagesActive = "1";
        paint();
        await waitForArtwork();
      }
    } finally {
      if (document.body.contains(el)) setCardNavigationBusy(false);
    }
  }
  function waitForArtwork() {
    if (main.complete && main.naturalWidth > 0 && !main.classList.contains('image-pending')) return Promise.resolve();
    return new Promise(resolve => {
      let timer, watchedSrc = main.currentSrc || main.src;
      const done = () => { clearTimeout(timer); main.removeEventListener('load', done); main.removeEventListener('error', failed); resolve(); };
      const failed = () => queueMicrotask(() => {
        const activeSrc = main.currentSrc || main.src;
        if (activeSrc && activeSrc !== watchedSrc) { watchedSrc = activeSrc; return; }
        done();
      });
      main.addEventListener('load', done, { once: true });
      main.addEventListener('error', failed);
      timer = setTimeout(done, 15000);
    });
  }
  function setCardNavigationBusy(value, message = 'Loading image…') {
    navigating = value;
    if (value) el.stopVideo();
    el.setAttribute("aria-busy", value ? "true" : "false");
    navMessage.textContent = message;
    navStatus.classList.toggle('hidden', !value);
    el.querySelectorAll(".previous, .next, .image-progress button").forEach(button => { button.disabled = value; });
    renderReactions();
    if (!value) { el.resumeVideo(); if (unavailablePending) queueMicrotask(skipUnavailable); }
  }
  async function navigateTo(candidate, delta) {
    if (navigating) return;
    const hadFullList = imagesLoaded;
    setCardNavigationBusy(true, imagesLoaded ? 'Checking next image…' : 'Loading artist images…');
    try {
      await ensureImages();
      // Before the first arrow click, `index` belongs to the one-item placeholder
      // array. ensureImages replaces it with the representative's real position in
      // the artist list, so apply the direction from that position—not the stale
      // placeholder destination calculated by move().
      if (!hadFullList) candidate = index + delta;
      if (images.length) await selectAllowed(candidate, delta);
    } catch (error) { toast(error.message); }
    finally { if (document.body.contains(el)) setCardNavigationBusy(false); }
  }
  async function move(delta) { await navigateTo(index + delta, delta); }
  function closeActionMenu() { actionMenu.classList.add('hidden'); el.querySelector('.more-menu').setAttribute('aria-expanded', 'false'); }
  async function showCollections() {
    if (context.canManageCollections && !context.canManageCollections()) {
      actionMenu.innerHTML = '<p class="card-menu-status">Sign out and back in once to allow collection access.</p>';
      return;
    }
    actionMenu.innerHTML = '<p class="card-menu-status">Loading collections…</p>';
    try {
      const data = await api('/api/collections');
      const collections = data.collections || [];
      if (!collections.length) { actionMenu.innerHTML = '<p class="card-menu-status">No image collections available.</p>'; return; }
      actionMenu.innerHTML = '<strong>Save image to</strong>' + collections.map(collection => `<button type="button" data-collection="${collection.id}" role="menuitem">${escapeHtml(collection.name)}</button>`).join('');
      actionMenu.querySelectorAll('[data-collection]').forEach(button => button.onclick = async event => {
        event.stopPropagation(); button.disabled = true;
        try { const result = await api('/api/collections/add', { method: 'POST', body: JSON.stringify({ imageId: current.id, collectionId: Number(button.dataset.collection) }) }); closeActionMenu(); toast(`Added to ${result.collectionName}`); }
        catch (error) { button.disabled = false; toast(error.message); }
      });
    } catch (error) {
      actionMenu.innerHTML = `<p class="card-menu-status">${escapeHtml(error.message)}</p>`;
    }
  }
  function resetActionMenu() {
    actionMenu.innerHTML = `<button type="button" data-action="collections" role="menuitem">♡ Add image to collection</button><button type="button" data-action="hide" class="danger-item" role="menuitem">⊘ Hide this artist</button>`;
    actionMenu.querySelector('[data-action="collections"]').onclick = event => { event.stopPropagation(); showCollections(); };
    const recommend = document.createElement('button');
    recommend.type = 'button'; recommend.setAttribute('role', 'menuitem');
    recommend.textContent = 'Recommend to Community';
    recommend.onclick = event => { event.stopPropagation(); closeActionMenu(); window.CivitaiCommunity?.recommend(current.id); };
    actionMenu.prepend(recommend);
    actionMenu.querySelector('[data-action="hide"]').onclick = async event => {
      event.stopPropagation();
      if (context.canManageContentControls && !context.canManageContentControls()) {
        toast('Sign out and back in once to allow Civitai Content Control changes.');
        return;
      }
      if (!confirm(`Hide all content from ${a.username} on Civitai? You can undo this in Civitai Content Controls.`)) return;
      try { await api('/api/content-controls/hide-artist', { method: 'POST', body: JSON.stringify({ username: a.username, userId: a.userId }) }); closeActionMenu(); removeCard('artist-hidden-by-user'); toast(`${a.username} added to your Civitai Hidden Users.`); }
      catch (error) { toast(error.message); }
    };
  }
  const more = el.querySelector('.more-menu');
  more.onclick = event => { event.stopPropagation(); const opening = actionMenu.classList.contains('hidden'); if (activeActionMenu && activeActionMenu !== actionMenu) activeActionMenu.classList.add('hidden'); if (opening) resetActionMenu(); actionMenu.classList.toggle('hidden', !opening); activeActionMenu = opening ? actionMenu : null; more.setAttribute('aria-expanded', String(opening)); if (opening) actionMenu.querySelector('button')?.focus(); };
  actionMenu.addEventListener('keydown', event => { if (event.key === 'Escape') { closeActionMenu(); more.focus(); } });
  el.querySelector(".previous").onclick = () => move(-1); el.querySelector(".next").onclick = () => move(1); el.querySelector(".image-button").onclick = () => showDetails(current, a, el); el.querySelector(".info-button").onclick = () => showDetails(current, a, el);
  applyCreatorFollowers(el, a);
  // Setting src while the card is still detached defeats loading="lazy" — the browser
  // fetches immediately — so a whole page of cards requested every preview at once and
  // saturated the connection. Artwork is attached only as a card nears the viewport.
  el.paintImages = paint;
  el.appendImages = additions => {
    const ids = new Set(images.map(image => String(image.id)));
    for (const image of additions) if (!ids.has(String(image.id))) {
      images.push(image); ids.add(String(image.id));
      if (image.tagState?.known) imageTagState.set(String(image.id), image.tagState);
    }
    a.imageCount = images.length; imagesLoaded = true; paint();
    hydrateReactionStates(images).catch(error => console.warn('Reaction history could not be loaded', error));
  };
  el.prepareArtwork = prepareArtwork;
  el.removeHiddenImage = async imageId => {
    imageTagState.delete(String(imageId));
    if (!imagesLoaded) await ensureImages();
    images = images.filter(image => String(image.id) !== String(imageId));
    a.imageCount = images.length;
    if (!images.length) { removeCard('image-hidden-by-civitai-tags'); return; }
    index = Math.min(index, images.length - 1);
    await selectAllowed(index, 1);
  };
  cardImageObserver.observe(el);
  el.dataset.username = a.username.toLowerCase();
  el.dataset.seenDate = selectedDate;
  if (!a.seen) seenObserver.observe(el);
  const follow = el.querySelector(".follow-button");
  el.addEventListener('creatorfollow', ({detail}) => {
    a.following = detail.following; a.userId = detail.userId;
    follow.classList.toggle('is-following', a.following);
    follow.textContent = a.following ? '\u2713 Following' : '+ Follow';
  });
  follow.onclick = () => toggleCreatorFollow(follow, a, { api, canWrite: context.canWrite, toast });
  el.addEventListener("reactionstate", () => { if (document.body.contains(el)) renderReactions(); });
  // Avatar and follower count only; Community reads follow state separately.
  el.applyCreatorProfile = metadata => { a.avatarUrl = metadata.avatarUrl; const oldAvatar = el.querySelector(".creator-avatar"); if (a.avatarUrl && oldAvatar && oldAvatar.getAttribute("src") !== a.avatarUrl) { const image = document.createElement("img"); image.className = "creator-avatar"; image.src = a.avatarUrl; image.alt = ""; wireAvatarFallback(image, a.username); oldAvatar.replaceWith(image); } applyCreatorFollowers(el, metadata); };
  el.applyCreatorMetadata = metadata => { a.following = !!metadata.following; a.userId = metadata.userId; el.applyCreatorProfile(metadata); follow.classList.toggle("is-following", a.following); follow.textContent = a.following ? "✓ Following" : "+ Follow"; };
  el.clearAccountMetadata = () => { a.following = false; follow.classList.remove("is-following"); follow.textContent = "+ Follow"; };
  el.setAttribute('aria-busy', 'true');
  paint(); return el;
}
