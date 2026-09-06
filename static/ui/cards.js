import { showCardArtwork, wireArtworkFallback } from './artwork.js';
import { toggleCreatorFollow } from './creator-actions.js';

// Shared card behavior. Page adapters supply services and a snapshot of the archive
// being browsed; delayed carousel requests never read another page's selected date.
export function createCreatorCard(a, context) {
  const { api, escapeHtml, avatar, wireAvatarFallback, applyCreatorFollowers, checkImageTags, tagsHideImage, hydrateReactionStates, reactionBar, showDetails, toast, ago, imageTagState, imageReactionState, cardImageObserver, seenObserver, pendingSeen, loadMore } = context;
  const selectedDate = context.date, selectedSegment = context.segment;
  let images = [a.representative], index = 0, current = images[0], imagesLoaded = a.imageCount <= 1;
  // The listing can include a cached decision. Unknown artwork still passes through
  // checkImageTags before any preview URL is attached, including in Time Machine.
  if (current.tagState?.known) imageTagState.set(String(current.id), current.tagState);
  let navigating = false;
  const el = document.createElement("article"); el.className = a.seen ? "creator-card is-seen" : "creator-card"; el.dataset.id = current.id;
  el.innerHTML = `<header class="creator-strip"><a class="creator-identity" href="${escapeHtml(a.profileUrl)}" target="_blank" rel="noopener">${avatar(a)}<span><span class="creator-name-line"><strong>${escapeHtml(a.username)}</strong>${a.matchedTags?.length ? `<span class="match-badge" title="Ranked here because you often react to: ${escapeHtml(a.matchedTags.join(", "))}" aria-label="Matches your taste: ${escapeHtml(a.matchedTags.join(", "))}">&#10038;</span>` : ""}${a.reactedOften ? `<span class="worth-badge" title="You have reacted to ${a.reactedCount} of this artist's images but do not follow them" aria-label="You often react to this artist but do not follow them">&#9829;</span>` : ""}<span class="creator-badge"></span></span><small><span class="image-age"></span><span class="creator-followers"></span></small></span></a><div class="creator-controls"><button class="follow-button ${a.following ? "is-following" : ""}" ${context.canWrite() ? "" : "disabled"} title="${context.canWrite() ? "" : "Civitai did not grant follow and reaction access."}">${a.following ? "✓ Following" : "+ Follow"}</button><button class="more-menu">⋮</button></div></header><div class="image-stage"><button class="image-button"><img loading="lazy" alt="Artwork by ${escapeHtml(a.username)}"></button><button class="carousel-arrow previous">‹</button><button class="carousel-arrow next">›</button><div class="image-overlay"><div class="reaction-slot"></div><button class="info-button">ⓘ</button></div><div class="image-progress"></div></div><footer class="creator-footer"><span class="image-position"></span><a class="open-image" target="_blank" rel="noopener">Open on Civitai ↗</a></footer>`;
  [[".previous", "Previous image", "15 5 8 12 15 19"], [".next", "Next image", "9 5 16 12 9 19"]].forEach(([selector, label, points]) => {
    const button = el.querySelector(selector);
    button.setAttribute("aria-label", label);
    button.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true"><polyline points="${points}"></polyline></svg>`;
  });
  if (a.recommendationLabel) {
    const reason = document.createElement("span");
    reason.className = "recommendation-badge";
    reason.textContent = a.recommendationLabel;
    reason.title = (a.recommendationReasons || []).join(" · ");
    el.querySelector(".image-stage").appendChild(reason);
  }
  const stage = el.querySelector('.image-stage');
  stage.append(el.querySelector('.creator-strip'));
  stage.append(el.querySelector('.more-menu'));
  stage.after(el.querySelector('.image-overlay'));
  el.querySelector('.image-button').setAttribute('aria-label', `View artwork by ${a.username}`);
  el.querySelector('.info-button').setAttribute('aria-label', 'Image details');
  el.querySelector('.more-menu').setAttribute('aria-label', 'More image details');
  const main = el.querySelector(".image-button img"), age = el.querySelector(".image-age"), reaction = el.querySelector(".reaction-slot"), position = el.querySelector(".image-position"), progress = el.querySelector(".image-progress"), open = el.querySelector(".open-image"); wireAvatarFallback(el.querySelector("img.creator-avatar"), a.username);
  function renderReactions() {
    reaction.innerHTML = reactionBar(current);
    wireReactions();
    if (navigating) reaction.querySelectorAll("[data-reaction]").forEach(button => { button.disabled = true; });
  }
  function wireReactions() { reaction.querySelectorAll("[data-reaction]").forEach(button => button.onclick = async event => { event.stopPropagation(); if (!context.canWrite()) return toast("Civitai did not grant reaction access."); button.disabled = true; const targetImage = current, imageId = targetImage.id, reactionName = button.dataset.reaction, active = !button.classList.contains("selected"); try { const result = await api("/api/reaction", { method: "POST", body: JSON.stringify({ imageId, reaction: reactionName, active }) }); const stats = { ...(targetImage.stats || {}), ...(result.stats || {}) }; targetImage.stats = stats; imageReactionState.set(String(imageId), { reactions: [...(result.reactions || [])], stats }); if (String(current.id) === String(imageId)) renderReactions(); toast(active ? `${reactionName} reaction added` : `${reactionName} reaction removed`); } catch (error) { toast(error.message); if (String(current.id) === String(imageId)) button.disabled = false; } }); }
  function paint() { current = images[index]; const activePosition = imagesLoaded ? index : Math.max(0, Number(a.representativeIndex) || 0); el.dataset.id = current.id; if (el.dataset.imagesActive) showCardArtwork(main, current.thumbnailUrl, current.url); age.textContent = ago(current.createdAt); renderReactions(); position.textContent = `${activePosition + 1} of ${a.imageCount} images`; open.href = current.civitaiUrl; const shown = imagesLoaded ? images : Array.from({ length: Math.min(a.imageCount, 40) }); const activeMarker = imagesLoaded || a.imageCount <= shown.length ? activePosition : Math.round(activePosition * (shown.length - 1) / (a.imageCount - 1)); progress.innerHTML = shown.map((_, i) => `<button class="${i === activeMarker ? "active" : ""}" data-index="${i}"></button>`).join(""); el.querySelector(".previous").hidden = a.imageCount < 2; el.querySelector(".next").hidden = a.imageCount < 2; if (imagesLoaded) progress.querySelectorAll("[data-index]").forEach(button => button.onclick = () => navigateTo(Number(button.dataset.index), 1)); }
  async function ensureImages() { if (imagesLoaded) return; const data = await api(`/api/history/artist?date=${selectedDate}&segment=${selectedSegment}&username=${encodeURIComponent(a.username)}${context.models}`); const activeId = current.id; images = data.images; index = Math.max(0, images.findIndex(image => image.id === activeId)); imagesLoaded = true; a.imageCount = images.length; hydrateReactionStates(images).catch(error => console.warn("Reaction history could not be loaded", error)); }
  function removeCard() {
    cardImageObserver.unobserve(el); seenObserver.unobserve(el); pendingSeen.delete(el);
    el.remove();
    loadMore().catch(error => toast(error.message));
  }
  async function selectAllowed(candidate, delta = 1) {
    while (images.length) {
      candidate = (candidate + images.length) % images.length;
      const result = await checkImageTags(images[candidate].id);
      if (!tagsHideImage(result)) { index = candidate; paint(); return true; }
      images.splice(candidate, 1); a.imageCount = images.length;
      if (delta < 0) candidate--;
    }
    removeCard(); return false;
  }
  async function prepareArtwork() {
    const result = await checkImageTags(current.id);
    if (tagsHideImage(result)) {
      await ensureImages();
      images = images.filter(image => String(image.id) !== String(current.id));
      a.imageCount = images.length;
      if (!await selectAllowed(0, 1)) return;
    }
    el.dataset.imagesActive = "1";
    paint();
  }
  function setCardNavigationBusy(value) {
    navigating = value;
    el.setAttribute("aria-busy", value ? "true" : "false");
    el.querySelectorAll(".previous, .next, .image-progress button").forEach(button => { button.disabled = value; });
    renderReactions();
  }
  async function navigateTo(candidate, delta) {
    if (navigating) return;
    setCardNavigationBusy(true);
    try {
      await ensureImages();
      if (images.length) await selectAllowed(candidate, delta);
    } catch (error) { toast(error.message); }
    finally { if (document.body.contains(el)) setCardNavigationBusy(false); }
  }
  async function move(delta) { await navigateTo(index + delta, delta); }
  wireArtworkFallback(main); el.querySelector(".previous").onclick = () => move(-1); el.querySelector(".next").onclick = () => move(1); el.querySelector(".image-button").onclick = () => showDetails(current, a, el); el.querySelector(".info-button").onclick = () => showDetails(current, a, el); el.querySelector(".more-menu").onclick = () => showDetails(current, a, el);
  applyCreatorFollowers(el, a);
  // Setting src while the card is still detached defeats loading="lazy" — the browser
  // fetches immediately — so a whole page of cards requested every preview at once and
  // saturated the connection. Artwork is attached only as a card nears the viewport.
  el.paintImages = paint;
  el.prepareArtwork = prepareArtwork;
  el.removeHiddenImage = async imageId => {
    imageTagState.delete(String(imageId));
    if (!imagesLoaded) await ensureImages();
    images = images.filter(image => String(image.id) !== String(imageId));
    a.imageCount = images.length;
    if (!images.length) { removeCard(); return; }
    index = Math.min(index, images.length - 1);
    await selectAllowed(index, 1);
  };
  cardImageObserver.observe(el);
  el.dataset.username = a.username.toLowerCase();
  el.dataset.seenDate = selectedDate;
  if (!a.seen) seenObserver.observe(el);
  const follow = el.querySelector(".follow-button");
  follow.onclick = () => toggleCreatorFollow(follow, a, { api, canWrite: context.canWrite, toast });
  el.addEventListener("reactionstate", () => { if (document.body.contains(el)) renderReactions(); });
  el.applyCreatorMetadata = metadata => { a.avatarUrl = metadata.avatarUrl; a.following = !!metadata.following; a.userId = metadata.userId; const oldAvatar = el.querySelector(".creator-avatar"); if (a.avatarUrl && oldAvatar && oldAvatar.getAttribute("src") !== a.avatarUrl) { const image = document.createElement("img"); image.className = "creator-avatar"; image.src = a.avatarUrl; image.alt = ""; wireAvatarFallback(image, a.username); oldAvatar.replaceWith(image); } follow.classList.toggle("is-following", a.following); follow.textContent = a.following ? "✓ Following" : "+ Follow"; applyCreatorFollowers(el, metadata); };
  el.clearAccountMetadata = () => { a.following = false; follow.classList.remove("is-following"); follow.textContent = "+ Follow"; };
  paint(); return el;
}
