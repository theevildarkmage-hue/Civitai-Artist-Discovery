const $ = id => document.getElementById(id);
let selectedModels = new Set();
let contentRating = "Soft";
let visibleBrowsingLevels = new Set([1, 2]);
let buildSegment = "all", buildCoverageRating = "Soft", currentBlocks = null, estimateToken = 0;
// How many creators this day's Civitai content controls removed, so the count on screen
// can explain itself rather than looking like missing data.
let hiddenCreators = 0;
let selectedDate = null, selectedSegment = "evening", activeBuildSegment = null, selectedView = "foryou", newestDate = null, socialWrite = false, oauthConnected = false, artistTotal = 0, imageTotal = 0, loadedArtists = 0, loadingMore = false, loadCancelled = false, loadingPhaseIndex = -1, activeLoadToken = 0, dayBuilt = false, activeRebuild = false;
const imageReactionState = new Map();
const segmentToolbar = document.createElement("nav"); segmentToolbar.className = "segment-toolbar"; segmentToolbar.innerHTML = '<label for="daySegment">Gallery window</label><select id="daySegment"><option value="evening">Evening · 12 PM–12 AM</option><option value="morning">Morning · 12 AM–12 PM</option><option value="all">All day · 12 AM–12 AM</option></select><label for="dayView">View</label><select id="dayView"><option value="foryou">For you</option><option value="discovery">Popular</option><option value="followed">Followed first</option><option value="new">New to you</option><option value="emerging">Emerging first</option></select><label for="cardSize">Card size</label><select id="cardSize"><option value="1">Large</option><option value="0.8">Medium</option><option value="0.6">Small</option></select><button id="contentFilter" class="filter-button" aria-expanded="false" title="Choose the content levels to display">Content: PG + PG-13</button><button id="modelFilter" class="filter-button" aria-expanded="false">Model: all</button><span id="followerSweep" class="sweep-note hidden"></span><div id="contentMenu" class="filter-menu content-menu hidden" aria-label="Browsing level"><div class="content-menu-title">Browsing Level</div><p>Select any combination of content you want to see</p><div class="rating-pills"><button data-level="1">PG</button><button data-level="2">PG-13</button><button data-level="4">R</button><button data-level="8">X</button><button data-level="16">XXX</button></div><div class="content-warning">⚠ Mature content is off until you explicitly enable it.</div><small>Collection uses Civitai Red’s grouped feeds, but saved images are filtered by their individual level.</small></div><div id="modelMenu" class="filter-menu hidden" role="group" aria-label="Filter by generation model"></div>'; document.body.insertBefore(segmentToolbar, document.querySelector("main"));
segmentToolbar.insertAdjacentHTML("afterbegin", '<span id="completeDayPrompt" class="complete-day-prompt hidden"><span id="completeDayText"></span><button id="completeDay">Complete this day</button></span>');
// Card size is a plain display preference, so it is saved locally rather than round-
// tripping to the server, and applied immediately — before anything else on the page
// runs — so there is no flash of the default size while a saved smaller one loads in.
const CARD_SCALE_KEY = "civitai-card-scale";
(() => {
  const select = document.getElementById("cardSize");
  let saved = null;
  try { saved = localStorage.getItem(CARD_SCALE_KEY); } catch (_) {}
  if (saved && [...select.options].some(option => option.value === saved)) {
    document.documentElement.style.setProperty("--card-scale", saved);
    select.value = saved;
  }
  select.onchange = () => {
    document.documentElement.style.setProperty("--card-scale", select.value);
    try { localStorage.setItem(CARD_SCALE_KEY, select.value); } catch (_) {}
  };
})();
async function api(url, options = {}) { const response = await fetch(url, { headers: { "Content-Type": "application/json" }, ...options }); const body = await response.json(); if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`); return body; }
const contentLabels = { Soft: "PG + PG-13", Mature: "PG through R", X: "All ratings" };
const browsingLevelLabels = new Map([[1, "PG"], [2, "PG-13"], [4, "R"], [8, "X"], [16, "XXX"]]);
function contentButtonLabel() {
  const selected = [...browsingLevelLabels].filter(([level]) => visibleBrowsingLevels.has(level)).map(([, label]) => label);
  return selected.length === browsingLevelLabels.size ? "All ratings" : selected.join(" + ");
}
function showContentRating() {
  $("contentFilter").textContent = `Content: ${contentButtonLabel()}`;
  $("contentMenu").querySelectorAll("[data-level]").forEach(button => {
    const selected = visibleBrowsingLevels.has(Number(button.dataset.level));
    button.classList.toggle("selected", selected);
    button.textContent = `${selected ? "✓ " : ""}${browsingLevelLabels.get(Number(button.dataset.level))}`;
  });
  const mature = [...visibleBrowsingLevels].some(level => level >= 4);
  $("contentMenu").querySelector(".content-warning").textContent = !mature
    ? "⚠ Mature content is off until you explicitly enable it."
    : "⚠ Mature content is enabled. Some images may be explicit.";
}
async function chooseContentRating(nextLevels) {
  const previous = contentRating, buttons = [...$("contentMenu").querySelectorAll("[data-level]")];
  $("contentFilter").disabled = true; buttons.forEach(button => button.disabled = true);
  $("contentMenu").classList.add("hidden"); $("contentFilter").setAttribute("aria-expanded", "false");
  try {
    const result = await api("/api/settings", { method: "POST", body: JSON.stringify({ browsingLevels: nextLevels }) });
    contentRating = result.contentRating; visibleBrowsingLevels = new Set(result.browsingLevels); showContentRating(); selectedModels.clear();
    const lowering = ({ Soft: 0, Mature: 1, X: 2 }[contentRating] || 0) < ({ Soft: 0, Mature: 1, X: 2 }[previous] || 0);
    toast(lowering ? `Showing ${contentButtonLabel()} from saved galleries. No download needed.`
      : `Showing ${contentButtonLabel()}. Incomplete coverage will be marked for upgrade.`);
    if (selectedDate) await loadDay(selectedDate, false, true);
  } catch (error) { toast(error.message); }
  finally { $("contentFilter").disabled = false; buttons.forEach(button => button.disabled = false); }
}
function toast(text) { $("toast").textContent = text; $("toast").classList.remove("hidden"); setTimeout(() => $("toast").classList.add("hidden"), 3000); }
function escapeHtml(value) { return String(value ?? "").replace(/[&<>"']/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]); }
function safeCount(value) { const number = Number(value); return Number.isFinite(number) ? Math.max(0, Math.round(number)) : 0; }
function displayCount(value) { return safeCount(value).toLocaleString(); }
function initials(name) { return (name || "?").split(/[_\s-]+/).slice(0, 2).map(x => x[0] || "").join("").toUpperCase(); }
function ago(value) { if (!value) return "Recently"; const seconds = Math.max(0, (Date.now() - new Date(value).getTime()) / 1000); if (seconds < 3600) return `${Math.max(1, Math.floor(seconds / 60))} minutes ago`; if (seconds < 86400) return `${Math.floor(seconds / 3600)} hours ago`; return new Date(value).toLocaleDateString(); }
const reactionInfo = { Like: ["👍", "likeCount"], Heart: ["❤️", "heartCount"], Laugh: ["😂", "laughCount"], Cry: ["😢", "cryCount"] };
async function hydrateReactionStates(images) { if (!oauthConnected) return; const unknown = [...new Map((images || []).filter(image => image?.id && !imageReactionState.has(String(image.id))).map(image => [String(image.id), image])).values()]; for (let offset = 0; offset < unknown.length; offset += 100) { const batch = unknown.slice(offset, offset + 100), query = new URLSearchParams(); batch.forEach(image => query.append("imageId", image.id)); const data = await api(`/api/reaction-status?${query}`); Object.entries(data.images || {}).forEach(([id, state]) => { const image = batch.find(item => String(item.id) === id); imageReactionState.set(id, { reactions: [...(state.reactions || [])], stats: image?.stats || {} }); document.querySelectorAll(`.creator-card[data-id="${CSS.escape(id)}"]`).forEach(card => card.dispatchEvent(new Event("reactionstate"))); }); } }
function reactionBar(image) { const state = imageReactionState.get(String(image.id)), s = state?.stats || image.stats || {}, selected = new Set(state?.reactions || []); return `<div class="reaction-bar">${Object.entries(reactionInfo).map(([name, [icon, key]]) => `<button class="${selected.has(name) ? "selected" : ""}" data-reaction="${name}" ${socialWrite ? "" : "disabled"} title="${socialWrite ? "" : "Civitai did not grant follow and reaction access."}">${icon} <b>${safeCount(s[key])}</b></button>`).join("")}<span class="total-reactions" title="Total reactions">Total ${safeCount(s.reactionCount)}</span></div>`; }
// Follower count is public information like the avatar, so it survives a disconnect.
// An unreadable count shows nothing rather than claiming the creator has none.
function applyCreatorFollowers(el, metadata) {
  const count = metadata.followers, line = el.querySelector(".creator-followers"), badge = el.querySelector(".creator-badge");
  if (!line || !badge) return;
  line.textContent = count === null || count === undefined ? "" : ` · ${displayCount(count)} followers`;
  badge.className = metadata.emerging ? "creator-badge pill emerging" : "creator-badge";
  badge.textContent = metadata.emerging ? "EMERGING" : "";
  badge.title = metadata.emerging ? "Fewer than 1,000 followers" : "";
}
// Keep the sticky window/view bar pinned exactly below the masthead. The masthead grows
// and shrinks with its content and the viewport, so the offset is measured rather than
// assumed — a fixed value let a taller masthead cover the bar while scrolling.
function trackHeaderHeight() {
  const header = document.querySelector("body>header");
  if (!header) return;
  const apply = () => document.documentElement.style.setProperty(
    "--header-h", `${Math.ceil(header.getBoundingClientRect().height)}px`);
  apply();
  if (window.ResizeObserver) new ResizeObserver(apply).observe(header);
  window.addEventListener("resize", apply);
}
trackHeaderHeight();
// Where you were. A session that lapses, or any reload, used to drop you back at the top
// of the first page; the day, window, view, how far you had paged and the scroll offset
// are all remembered so you come back to the same place.
const FEED_STATE = "civitai-feed-state";
let saveTimer = 0;
function saveFeedState() {
  if (!dayBuilt || !selectedDate) return;
  try {
    sessionStorage.setItem(FEED_STATE, JSON.stringify({
      date: selectedDate, segment: selectedSegment, view: selectedView,
      models: [...selectedModels], loaded: loadedArtists, scrollY: Math.round(window.scrollY),
    }));
  } catch (_) {}
}
function scheduleFeedSave() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveFeedState, 250);
}
function readFeedState() {
  try { return JSON.parse(sessionStorage.getItem(FEED_STATE) || "null"); } catch (_) { return null; }
}
window.addEventListener("scroll", scheduleFeedSave, { passive: true });
window.addEventListener("pagehide", saveFeedState);
// Poll often enough that the authorization is renewed long before it expires.
setInterval(() => { refreshAuth().catch(() => {}); }, 5 * 60 * 1000);
// Back to top. Days run to thousands of cards, so scrolling back by hand is a real cost.
(() => {
  const button = $("scrollTop");
  const update = () => button.classList.toggle("is-visible", window.scrollY > 900);
  window.addEventListener("scroll", update, { passive: true });
  update();
  button.onclick = () => {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    window.scrollTo({ top: 0, behavior: reduced ? "auto" : "smooth" });
  };
})();
const cardImageObserver = new IntersectionObserver(entries => {
  entries.forEach(entry => {
    if (!entry.isIntersecting) return;
    cardImageObserver.unobserve(entry.target);
    entry.target.dataset.imagesActive = "1";
    entry.target.paintImages?.();
  });
}, { rootMargin: "700px" });
// Cards the user has scrolled past dim in place immediately, and the fact of having
// seen them is saved so a day revisited later opens with those already out of the way.
// Unlike cardImageObserver above (which fires 700px early, on purpose, to have artwork
// ready before it is needed) this uses a near-zero margin and a short dwell: a card has
// to actually reach the screen and sit there a moment, so a fast fling-scroll past ten
// cards does not mark all ten as "seen" when nothing was really looked at.
const SEEN_DWELL_MS = 600;
// Keyed by the day each card actually belongs to, not the currently-selected one: a
// card's dwell timer can still be pending when the user switches days, and reading
// selectedDate only at flush time would attribute it to whatever day they had moved on
// to by then rather than the one they actually saw it on.
const pendingSeen = new Map();
let seenFlushTimer = 0;
function flushSeen() {
  const groups = [...pendingSeen.entries()]; pendingSeen.clear();
  groups.forEach(([date, usernames]) => {
    if (!date || !usernames.size) return;
    api("/api/history/seen", { method: "POST", body: JSON.stringify({ date, usernames: [...usernames] }) })
      .catch(error => console.warn("Seen state could not be saved", error));
  });
}
function scheduleSeenFlush() { clearTimeout(seenFlushTimer); seenFlushTimer = setTimeout(flushSeen, 3000); }
// A regular fetch can be aborted mid-flight once the page starts unloading, silently
// dropping whatever was still batched. sendBeacon exists for exactly this moment: the
// browser still attempts delivery even though the page is on its way out.
function flushSeenOnUnload() {
  const groups = [...pendingSeen.entries()]; pendingSeen.clear();
  groups.forEach(([date, usernames]) => {
    if (!date || !usernames.size) return;
    const blob = new Blob([JSON.stringify({ date, usernames: [...usernames] })], { type: "application/json" });
    navigator.sendBeacon("/api/history/seen", blob);
  });
}
window.addEventListener("pagehide", flushSeenOnUnload);
// Marks a card "seen" when the user scrolls it away, not just for having sat on screen:
// cards already in view on a fresh page load must not dim themselves before anyone has
// scrolled past them, so this fires on exit (dwell measured beforehand) rather than on
// entry — otherwise every reload would immediately dim whatever loaded above the fold.
const seenObserver = new IntersectionObserver(entries => {
  entries.forEach(entry => {
    const el = entry.target;
    if (entry.isIntersecting) { el.seenEnteredAt = Date.now(); return; }
    const enteredAt = el.seenEnteredAt;
    el.seenEnteredAt = null;
    // A view reload, tab switch, or day change can detach/hide a card and produces the
    // same non-intersecting notification as scrolling. It is not evidence the person saw
    // the card. Likewise, leaving through the bottom means the reader scrolled back up;
    // only a connected card that travelled completely above the viewport was passed.
    const exitedAbove = entry.rootBounds
      ? entry.boundingClientRect.bottom <= entry.rootBounds.top
      : entry.boundingClientRect.bottom <= 0;
    if (!el.isConnected || !exitedAbove || !enteredAt || Date.now() - enteredAt < SEEN_DWELL_MS) return;
    seenObserver.unobserve(el);
    el.classList.add("is-seen");
    const date = el.dataset.seenDate;
    if (!pendingSeen.has(date)) pendingSeen.set(date, new Set());
    pendingSeen.get(date).add(el.dataset.username);
    scheduleSeenFlush();
  });
}, { rootMargin: "0px" });
function pauseSeenTracking() {
  document.querySelectorAll(".creator-card").forEach(el => {
    seenObserver.unobserve(el);
    el.seenEnteredAt = null;
  });
}
function resumeSeenTracking() {
  document.querySelectorAll(".creator-card:not(.is-seen)").forEach(el => seenObserver.observe(el));
}
function clearGallery() {
  document.querySelectorAll(".creator-card").forEach(el => {
    seenObserver.unobserve(el);
    cardImageObserver.unobserve(el);
    el.seenEnteredAt = null;
  });
  $("gallery").replaceChildren();
}
function avatar(a) { return a.avatarUrl ? `<img class="creator-avatar" src="${escapeHtml(a.avatarUrl)}" alt="">` : `<span class="creator-avatar fallback">${escapeHtml(initials(a.username))}</span>`; }
function wireAvatarFallback(image, username) { if (!image) return; image.addEventListener("error", () => { const fallback = document.createElement("span"); fallback.className = "creator-avatar fallback"; fallback.textContent = initials(username); image.replaceWith(fallback); }, { once: true }); }
function card(a) {
  let images = [a.representative], index = 0, current = images[0], imagesLoaded = a.imageCount <= 1;
  const el = document.createElement("article"); el.className = a.seen ? "creator-card is-seen" : "creator-card"; el.dataset.id = current.id;
  el.innerHTML = `<header class="creator-strip"><a class="creator-identity" href="${escapeHtml(a.profileUrl)}" target="_blank" rel="noopener">${avatar(a)}<span><span class="creator-name-line"><strong>${escapeHtml(a.username)}</strong>${a.matchedTags?.length ? `<span class="match-badge" title="Ranked here because you often react to: ${escapeHtml(a.matchedTags.join(", "))}" aria-label="Matches your taste: ${escapeHtml(a.matchedTags.join(", "))}">&#10038;</span>` : ""}${a.worthFollowing ? `<span class="worth-badge" title="You have reacted to ${a.reactedCount} of their images but do not follow them" aria-label="You react to this creator often">&#9829;</span>` : ""}<span class="creator-badge"></span></span><small><span class="image-age"></span><span class="creator-followers"></span></small></span></a><div class="creator-controls"><button class="follow-button ${a.following ? "is-following" : ""}" ${socialWrite ? "" : "disabled"} title="${socialWrite ? "" : "Civitai did not grant follow and reaction access."}">${a.following ? "✓ Following" : "+ Follow"}</button><button class="more-menu">⋮</button></div></header><div class="image-stage"><button class="image-button"><img loading="lazy" alt="Artwork by ${escapeHtml(a.username)}"></button><button class="carousel-arrow previous">‹</button><button class="carousel-arrow next">›</button><div class="image-overlay"><div class="reaction-slot"></div><button class="info-button">ⓘ</button></div><div class="image-progress"></div></div><footer class="creator-footer"><span class="image-position"></span><a class="open-image" target="_blank" rel="noopener">Open on Civitai ↗</a></footer>`;
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
  const main = el.querySelector(".image-button img"), age = el.querySelector(".image-age"), reaction = el.querySelector(".reaction-slot"), position = el.querySelector(".image-position"), progress = el.querySelector(".image-progress"), open = el.querySelector(".open-image"); wireAvatarFallback(el.querySelector("img.creator-avatar"), a.username);
  function renderReactions() { reaction.innerHTML = reactionBar(current); wireReactions(); }
  function wireReactions() { reaction.querySelectorAll("[data-reaction]").forEach(button => button.onclick = async event => { event.stopPropagation(); if (!socialWrite) return toast("Civitai did not grant reaction access."); button.disabled = true; const targetImage = current, imageId = targetImage.id, reactionName = button.dataset.reaction, active = !button.classList.contains("selected"); try { const result = await api("/api/reaction", { method: "POST", body: JSON.stringify({ imageId, reaction: reactionName, active }) }); const stats = { ...(targetImage.stats || {}), ...(result.stats || {}) }; targetImage.stats = stats; imageReactionState.set(String(imageId), { reactions: [...(result.reactions || [])], stats }); if (String(current.id) === String(imageId)) renderReactions(); toast(active ? `${reactionName} reaction added` : `${reactionName} reaction removed`); } catch (error) { toast(error.message); if (String(current.id) === String(imageId)) button.disabled = false; } }); }
  function paint() { current = images[index]; const activePosition = imagesLoaded ? index : Math.max(0, Number(a.representativeIndex) || 0); el.dataset.id = current.id; if (el.dataset.imagesActive) main.src = current.thumbnailUrl; age.textContent = ago(current.createdAt); renderReactions(); position.textContent = `${activePosition + 1} of ${a.imageCount} images`; open.href = current.civitaiUrl; const shown = imagesLoaded ? images : Array.from({ length: Math.min(a.imageCount, 40) }); const activeMarker = imagesLoaded || a.imageCount <= shown.length ? activePosition : Math.round(activePosition * (shown.length - 1) / (a.imageCount - 1)); progress.innerHTML = shown.map((_, i) => `<button class="${i === activeMarker ? "active" : ""}" data-index="${i}"></button>`).join(""); el.querySelector(".previous").hidden = a.imageCount < 2; el.querySelector(".next").hidden = a.imageCount < 2; if (imagesLoaded) progress.querySelectorAll("[data-index]").forEach(button => button.onclick = () => { index = Number(button.dataset.index); paint(); }); }
  async function ensureImages() { if (imagesLoaded) return; const data = await api(`/api/history/artist?date=${selectedDate}&segment=${selectedSegment}&username=${encodeURIComponent(a.username)}${modelQuery()}`); const activeId = current.id; images = data.images; index = Math.max(0, images.findIndex(image => image.id === activeId)); imagesLoaded = true; a.imageCount = images.length; hydrateReactionStates(images).catch(error => console.warn("Reaction history could not be loaded", error)); }
  async function move(delta) { try { await ensureImages(); index = (index + delta + images.length) % images.length; paint(); } catch (error) { toast(error.message); } }
  main.addEventListener("error", () => { main.classList.add("image-error"); }); el.querySelector(".previous").onclick = () => move(-1); el.querySelector(".next").onclick = () => move(1); el.querySelector(".image-button").onclick = () => showDetails(current, a); el.querySelector(".info-button").onclick = () => showDetails(current, a); el.querySelector(".more-menu").onclick = () => showDetails(current, a);
  applyCreatorFollowers(el, a);
  // Setting src while the card is still detached defeats loading="lazy" — the browser
  // fetches immediately — so a whole page of cards requested every preview at once and
  // saturated the connection. Artwork is attached only as a card nears the viewport.
  el.paintImages = paint;
  cardImageObserver.observe(el);
  el.dataset.username = a.username.toLowerCase();
  el.dataset.seenDate = selectedDate;
  if (!a.seen) seenObserver.observe(el);
  const follow = el.querySelector(".follow-button"); follow.onclick = async () => { if (!socialWrite) return toast("Civitai did not grant follow access."); follow.disabled = true; try { const result = await api("/api/follow", { method: "POST", body: JSON.stringify({ userId: a.userId, username: a.username, following: !a.following }) }); a.following = result.following; a.userId = result.userId; follow.classList.toggle("is-following", a.following); follow.textContent = a.following ? "✓ Following" : "+ Follow"; toast(a.following ? `Now following @${a.username}` : `Unfollowed @${a.username}`); } catch (error) { toast(error.message); } finally { follow.disabled = false; } };
  el.addEventListener("reactionstate", () => { if (document.body.contains(el)) renderReactions(); });
  el.applyCreatorMetadata = metadata => { a.avatarUrl = metadata.avatarUrl; a.following = !!metadata.following; a.userId = metadata.userId; const oldAvatar = el.querySelector(".creator-avatar"); if (a.avatarUrl && oldAvatar && oldAvatar.getAttribute("src") !== a.avatarUrl) { const image = document.createElement("img"); image.className = "creator-avatar"; image.src = a.avatarUrl; image.alt = ""; wireAvatarFallback(image, a.username); oldAvatar.replaceWith(image); } follow.classList.toggle("is-following", a.following); follow.textContent = a.following ? "✓ Following" : "+ Follow"; applyCreatorFollowers(el, metadata); };
  el.clearAccountMetadata = () => { a.following = false; follow.classList.remove("is-following"); follow.textContent = "+ Follow"; };
  paint(); return el;
}
function displayDate(value) { return new Date(`${value}T12:00:00`).toLocaleDateString(undefined, { weekday: "long", year: "numeric", month: "long", day: "numeric" }); }
function shiftDate(value, delta) { const date = new Date(`${value}T12:00:00`); date.setDate(date.getDate() + delta); return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`; }
function localDateString(date) { return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`; }
function dayRequest(value, segment = selectedSegment) { const start = new Date(`${value}T00:00:00`); if (segment === "evening") start.setHours(12); const end = new Date(start); segment === "all" ? end.setDate(end.getDate() + 1) : end.setHours(end.getHours() + 12); return { date: value, segment, startUtc: start.toISOString(), endUtc: end.toISOString(), timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "Local" }; }
function friendlyDuration(seconds) { const value = Math.max(0, Math.round(Number(seconds) || 0)); if (value < 60) return "less than a minute"; const minutes = Math.round(value / 60); if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"}`; const hours = Math.floor(minutes / 60), remainder = minutes % 60; return `${hours} hour${hours === 1 ? "" : "s"}${remainder ? ` ${remainder} min` : ""}`; }
function etaPoint(seconds) { const value = Math.max(0, Math.round(Number(seconds) || 0)); if (value < 120) return `${Math.max(15, Math.round(value / 15) * 15)} seconds`; const minutes = Math.max(2, Math.round(value / 60)); if (minutes < 60) return `${minutes} minutes`; return friendlyDuration(value); }
function etaRange(low, high) { if (!Number.isFinite(Number(high))) return null; return `About ${etaPoint(high)} remaining`; }
function resetLoadingPhases() { loadingPhaseIndex = -1; ["Finding", "Collecting", "Organizing"].forEach(name => $(`phase${name}`).classList.remove("active", "complete")); }
function setLoadingPhase(phase) { const order = ["finding", "collecting", "organizing"], requested = phase === "complete" ? order.length : Math.max(0, order.indexOf(phase)); loadingPhaseIndex = Math.max(loadingPhaseIndex, requested); order.forEach((name, index) => { const item = $(`phase${name[0].toUpperCase()}${name.slice(1)}`); item.classList.toggle("complete", loadingPhaseIndex >= order.length || index < loadingPhaseIndex); item.classList.toggle("active", loadingPhaseIndex < order.length && index === loadingPhaseIndex); }); }
// A retry is not a failure. Stay calm for the first few attempts, keep the real
// counters on screen, and only escalate the wording once Civitai has actually
// stopped answering, so a hiccup never reads as a crash.
const PATIENT_RETRIES = 3;
function retryMessage(status) {
  const attempt = Number(status.retryAttempt) || 0;
  if (status.delayReason === "rate_limited") return "Civitai asked us to slow down. Your progress is saved while we wait.";
  if (attempt < PATIENT_RETRIES) return "Civitai is busy. Waiting a moment — everything collected so far is saved.";
  return "Civitai is not responding. Still retrying — everything collected so far is saved.";
}
function retryNote(status) {
  const seconds = Number(status.retryInSeconds), attempt = Number(status.retryAttempt) || 0,
    attempts = Number(status.retryAttempts) || 0;
  const when = Number.isFinite(seconds) && seconds > 0 ? `Retrying in ${seconds}s` : "Retrying now";
  return attempt >= PATIENT_RETRIES && attempts ? `${when} · attempt ${attempt} of ${attempts}` : when;
}
function updateProgress(status) {
  const phase = status.phase === "locating" ? "finding" : status.phase === "organizing" ? "organizing" : status.phase === "complete" ? "complete" : "collecting";
  const progress = Math.max(0, Math.min(100, Number(status.progress) || 0)), found = safeCount(status.itemCount),
    creators = safeCount(status.creatorCount), checked = safeCount(status.listingsChecked),
    delay = status.delayReason, estimate = etaRange(status.etaLowSeconds, status.etaHighSeconds);
  setLoadingPhase(phase);
  $("elapsedText").textContent = `Elapsed: ${friendlyDuration(status.elapsedSeconds)}`;
  // The bar keeps animating through a retry: work is paused, not abandoned.
  $("progressBar").classList.toggle("indeterminate", phase === "finding");
  $("progressBar").classList.toggle("waiting", Boolean(delay));
  if (phase === "finding") {
    $("progressBar").style.width = "28%";
    const reached = status.searchReachedAt ? new Date(status.searchReachedAt).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : null;
    $("loadingMessage").textContent = delay ? retryMessage(status) : "Searching backward through newer artwork to reach this day.";
    const found_so_far = `${displayCount(checked)} artwork listings checked${reached ? ` · Reached ${reached}` : ""}`;
    $("progressText").textContent = delay ? `${found_so_far} · ${retryNote(status)}` : found_so_far;
    return;
  }
  if (phase === "organizing") {
    $("progressBar").classList.remove("indeterminate", "waiting");
    $("progressBar").style.width = "97%";
    $("loadingMessage").textContent = `Organizing ${displayCount(found)} images into ${displayCount(creators)} creator galleries.`;
    $("progressText").textContent = "Almost ready";
    return;
  }
  $("progressBar").classList.remove("indeterminate");
  $("progressBar").style.width = `${25 + progress * .7}%`;
  $("loadingMessage").textContent = delay ? retryMessage(status) : "Collecting this day’s artwork listings.";
  $("progressText").textContent = `${displayCount(found)} image${found === 1 ? "" : "s"} found from ${displayCount(creators)} creator${creators === 1 ? "" : "s"} · ${delay ? retryNote(status) : estimate || "Measuring collection speed…"}`;
}
async function enrichCards(artists, cards) { if (!oauthConnected || !artists.length) return; const query = new URLSearchParams(); artists.forEach(artist => query.append("username", artist.username)); try { const data = await api(`/api/creator-metadata?${query}`); artists.forEach((artist, index) => { const metadata = data.creators?.[artist.username.toLowerCase()]; if (metadata) cards[index].applyCreatorMetadata(metadata); }); } catch (error) { console.warn("Creator details could not be loaded", error); } }
async function refreshVisibleCreatorMetadata() { if (!oauthConnected) return; const cards = [...document.querySelectorAll(".creator-card")]; for (let offset = 0; offset < cards.length; offset += 50) { const batch = cards.slice(offset, offset + 50), query = new URLSearchParams(); batch.forEach(element => query.append("username", element.querySelector(".creator-identity strong").textContent)); const data = await api(`/api/creator-metadata?${query}`); batch.forEach(element => { const metadata = data.creators?.[element.dataset.username]; if (metadata) element.applyCreatorMetadata(metadata); }); } }
const renderProgress = updateProgress;
updateProgress = status => { const requested = status.phase === "locating" ? 0 : status.phase === "organizing" ? 2 : status.phase === "complete" ? 3 : 1; if (requested < loadingPhaseIndex) return; renderProgress(status); };
// Changing the view or the day resets paging. A request already in flight would come back
// with an offset from the previous ordering and append a page a second time, so every load
// carries the token it started under and discards its result if that token has moved on.
let galleryToken = 0;
// Identifies one continuous scroll session to the server, so a personalised order can
// stay frozen while paging through it (a background sweep is still adding tag/follower
// data underneath, and re-deriving the order fresh on every page let a creator's rank
// shift between two fetches, serving whoever sat at the page boundary twice) while a
// genuinely new day/view load still gets whatever the sweep has added by then.
// galleryToken alone is not enough: it restarts from 0 on every reload, so two different
// page loads could send the same value and collide on the server's cache. This adds the
// per-page-load part that makes the combination unique across reloads, not just within one.
const PAGE_SESSION = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
async function loadMore() { if (loadingMore || loadedArtists >= artistTotal) return; loadingMore = true; const token = galleryToken; try { const data = await api(`/api/history/artists?date=${selectedDate}&segment=${selectedSegment}&offset=${loadedArtists}&limit=50&view=${selectedView}&session=${PAGE_SESSION}-${galleryToken}${modelQuery()}`); if (token !== galleryToken) return; if (Number.isFinite(data.total)) artistTotal = data.total; const fragment = document.createDocumentFragment(), cards = []; data.artists.forEach(artist => { const element = card(artist); cards.push(element); fragment.appendChild(element); }); $("gallery").insertBefore(fragment, $("loadSentinel")); loadedArtists += data.artists.length; $("summary").textContent = `${displayCount(artistTotal)} artists${selectedView === "new" ? " new to you" : ""} · ${displayCount(imageTotal)} images · showing ${loadedArtists}${hiddenCreators ? ` · ${displayCount(hiddenCreators)} hidden by your Civitai settings` : ""}`;
  $("summary").title = hiddenCreators ? "Creators you hide on Civitai, or who have blocked you, are left out of this gallery." : ""; enrichCards(data.artists, cards); hydrateReactionStates(data.artists.map(artist => artist.representative)).catch(error => console.warn("Reaction history could not be loaded", error)); } finally { loadingMore = false; } }
function applyAuth(auth) { oauthConnected = !!auth.connected; socialWrite = !!auth.socialWrite; const waiting = auth.oauthJob?.state === "loading";
  // Follows and reactions are granted at sign-in, so the normal signed-in state needs no
  // qualifier. The exception is worth naming: Civitai can complete a sign-in while
  // withholding write access, and silently dead buttons would look like a broken app.
  $("accountStatus").textContent = !oauthConnected ? "Not signed in"
    : `Signed in as @${auth.username || "Civitai"}${socialWrite ? "" : " · follows & reactions not granted"}`;
  $("accountStatus").title = !oauthConnected ? "Signing in is encrypted and saved for future launches."
    : socialWrite ? "Follows and reactions are sent only when you click them."
    : "Civitai did not grant follow and reaction access. Sign out and back in to allow it.";
  $("connect").classList.toggle("hidden", oauthConnected); $("connect").disabled = waiting;
  $("connect").textContent = waiting ? "Waiting for Civitai…" : "Sign in";
  $("disconnect").classList.toggle("hidden", !oauthConnected);
  document.querySelectorAll("[data-reaction],.follow-button").forEach(button => {
    button.disabled = !socialWrite;
    button.title = socialWrite ? "" : "Civitai did not grant follow and reaction access.";
  }); }
async function refreshAuth() { const auth = await api("/api/auth-status"); applyAuth(auth); return auth; }
// A half-built block is otherwise invisible: you land on whichever block is finished and
// nothing says another one is part-way through. The window control states it instead.
const segmentLabels = { morning: "Morning · 12 AM–12 PM", evening: "Evening · 12 PM–12 AM",
  all: "All day" };
async function refreshBlockLabels(value) {
  try {
    const data = await api(`/api/history/blocks?date=${value}`);
    if (value !== selectedDate) return;
    currentBlocks = data.blocks || null;
    Object.entries(segmentLabels).forEach(([segment, label]) => {
      const option = $("daySegment").querySelector(`option[value="${segment}"]`);
      const block = data.blocks?.[segment];
      if (!option || !block) return;
      if (block.complete) option.textContent = `${label} · ready`;
      // An all-day archive is never built directly, it is published once both halves are
      // done. It holds their images meanwhile, so reporting that count would imply a
      // build the user can resume, and they cannot.
      else if (segment === "all") option.textContent = `${label} · ready to build`;
      else if (block.itemCount > 0) option.textContent = `${label} · ${displayCount(block.itemCount)} saved, unfinished`;
      // Days collected before the app moved to 12-hour blocks have a full-day archive and
      // no halves at all. Calling those halves "not built" reads as though the day were
      // missing and invites rebuilding data already held.
      else option.textContent = data.blocks?.all?.complete
        ? `${label} · already in All day` : `${label} · not built`;
    });
    const morning = !!data.blocks?.morning?.complete, evening = !!data.blocks?.evening?.complete;
    const oneHalf = morning !== evening;
    $("completeDayPrompt").classList.toggle("hidden", !oneHalf || !dayBuilt);
    if (oneHalf) $("completeDayText").textContent =
      `${morning ? "Morning" : "Evening"} is ready. Build ${morning ? "Evening" : "Morning"} to complete the day.`;
    return data;
  } catch (error) { console.warn("Block states could not be read", error); }
}
function setNavigationBusy(busy) { $("olderDay").disabled = busy; $("newerDay").disabled = busy || selectedDate >= newestDate; $("daySegment").disabled = busy; $("dayView").disabled = busy; $("rebuildDay").textContent = selectedSegment === "all" ? "Rebuild day" : "Rebuild block"; $("rebuildDay").disabled = busy || !dayBuilt; $("rebuildDay").title = dayBuilt ? (busy ? "Wait for the current operation to finish" : `Rescan this ${selectedSegment === "all" ? "day" : "12-hour block"} and merge updated listings`) : `Build this ${selectedSegment === "all" ? "day" : "block"} before rebuilding it`; }
function showBuildSetup(visible) {
  $("buildSetup").classList.toggle("hidden", !visible);
  $("loading").classList.toggle("ready-to-build", visible);
  if (visible) segmentToolbar.classList.add("hidden");
}
const coverageRank = { Soft: 0, Mature: 1, X: 2 };
function blockReadyForBuild(segment) {
  const block = currentBlocks?.[segment];
  return !!block?.complete && coverageRank[block.contentRating || "Soft"] >= coverageRank[buildCoverageRating];
}
function buildStatus(segment) {
  const block = currentBlocks?.[segment];
  if (blockReadyForBuild(segment)) return `Ready through ${block.contentRating || "PG + PG-13"}`;
  if (block?.complete) return `Ready through ${block.contentRating || "Soft"} · upgrade needed`;
  if (safeCount(block?.itemCount)) return `${displayCount(block.itemCount)} saved · continue`;
  return segment === "morning" ? "12 AM–12 PM" : "12 PM–12 AM";
}
async function refreshBuildEstimate() {
  const token = ++estimateToken;
  $("buildEstimate").textContent = "Calculating…";
  try {
    const estimate = await api(`/api/history/estimate?date=${selectedDate}&segment=${buildSegment}&contentRating=${buildCoverageRating}`);
    if (token !== estimateToken) return;
    $("buildEstimate").textContent = `${friendlyDuration(estimate.lowSeconds)}–${friendlyDuration(estimate.highSeconds)}`;
    if (estimate.fixedBenchmark) {
      $("buildEstimateSource").textContent = `Fixed benchmark: about ${displayCount(estimate.benchmarkImages)} images, ${displayCount(estimate.listingRequests)} listing requests + ${displayCount(estimate.seekRequests)} date-location requests, up to ${displayCount(estimate.pageSize)} listings each. Range compares ${estimate.cleanRequestSeconds}s clean cycles with the observed ${estimate.delayedRequestSeconds}s delayed cycles; connection speed is not the limiter.`;
    } else if (estimate.measured) {
      $("buildEstimateSource").textContent = "Based on a completed build on this computer";
    } else {
      $("buildEstimateSource").textContent = "Starting estimate until this computer has comparable completed-build measurements";
    }
  } catch (_) { if (token === estimateToken) $("buildEstimate").textContent = "Estimate unavailable"; }
}
function refreshBuildChoices() {
  const morningReady = blockReadyForBuild("morning");
  const eveningReady = blockReadyForBuild("evening");
  const morningStored = !!currentBlocks?.morning?.complete;
  const eveningStored = !!currentBlocks?.evening?.complete;
  $("buildRange").querySelectorAll("[data-segment]").forEach(button => {
    const segment = button.dataset.segment;
    button.classList.toggle("selected", segment === buildSegment);
    const small = button.querySelector("small");
    if (segment !== "all") small.textContent = buildStatus(segment);
    else if (morningReady !== eveningReady) small.textContent = `Build ${morningReady ? "Evening" : "Morning"} only`;
    else if (morningStored || eveningStored) {
      const tasks = [];
      if (!morningReady) tasks.push(`${morningStored ? "Upgrade" : "Build"} Morning`);
      if (!eveningReady) tasks.push(`${eveningStored ? "Upgrade" : "Build"} Evening`);
      small.textContent = tasks.join(" + ");
    }
    else if (!morningReady && !eveningReady) small.textContent = "Recommended · two resumable halves";
    else small.textContent = "Ready";
  });
  $("buildCoverage").querySelectorAll("[data-rating]").forEach(button =>
    button.classList.toggle("selected", button.dataset.rating === buildCoverageRating));
  const partial = buildSegment !== "all" && safeCount(currentBlocks?.[buildSegment]?.itemCount) > 0;
  $("startLoading").textContent = partial ? "Continue building" :
    buildSegment === "all" && morningReady !== eveningReady ? "Build missing half" : "Build gallery";
  refreshBuildEstimate();
}
function showBuildReady(status) {
  resetLoadingPhases();
  const morningReady = blockReadyForBuild("morning");
  const eveningReady = blockReadyForBuild("evening");
  buildSegment = morningReady !== eveningReady ? "all" :
    (selectedSegment !== "all" && safeCount(status.itemCount) ? selectedSegment : "all");
  buildCoverageRating = "Soft";
  $("loadingTitle").textContent = `Build ${displayDate(selectedDate)}`;
  $("elapsedText").classList.add("hidden");
  $("progressBar").classList.remove("indeterminate", "waiting");
  $("progressBar").style.width = "0%";
  $("startLoading").classList.remove("hidden");
  $("startLoading").disabled = false;
  $("stopLoading").classList.add("hidden");
  showBuildSetup(true);
  refreshBuildChoices();
  setNavigationBusy(false);
}
function showReady(status) {
  resetLoadingPhases();
  // All day is a local union, not a third network collection. Building it directly would
  // contradict the window labels and duplicate the work of both resumable halves.
  if (selectedSegment === "all") {
    $("loadingTitle").textContent = `Build Morning and Evening for ${displayDate(selectedDate)}`;
    $("loadingMessage").textContent = "All day appears automatically after both 12-hour blocks are complete.";
    $("progressText").textContent = "Morning and Evening will run automatically and resume from saved checkpoints.";
    $("elapsedText").classList.add("hidden");
    $("progressBar").classList.remove("indeterminate", "waiting");
    $("progressBar").style.width = "0%";
    $("startLoading").textContent = "Build full day";
    $("startLoading").classList.remove("hidden");
    $("startLoading").disabled = false;
    $("stopLoading").classList.add("hidden");
    setNavigationBusy(false);
    return;
  }
  const partial = safeCount(status.itemCount) > 0 || status.state === "cancelled";
  $("loadingTitle").textContent = `${partial ? "Continue building" : "Ready to build"} ${displayDate(selectedDate)}`;
  $("loadingMessage").textContent = partial ? "Your previous progress was saved and is ready to continue." : "The app will collect this block’s artwork listings and organize them by creator.";
  const estimate = "Most blocks take about 6 to 8 minutes";
  $("progressText").textContent = partial ? `${displayCount(status.itemCount)} images already saved · ${estimate}` : `${estimate} · Nothing will be collected until you start`;
  $("elapsedText").classList.add("hidden");
  $("progressBar").classList.remove("indeterminate");
  $("progressBar").style.width = partial ? `${Math.max(3, Math.min(95, 25 + (Number(status.progress) || 0) * .7))}%` : "0%";
  $("startLoading").textContent = partial ? "Continue building" : "Build this block";
  $("startLoading").classList.remove("hidden");
  $("startLoading").disabled = false;
  $("stopLoading").classList.add("hidden");
  setNavigationBusy(false);
}
function showStopped() { $("loadingTitle").textContent = "Loading stopped"; $("loadingMessage").textContent = "Everything collected so far has been saved. Press Continue building whenever you are ready."; $("progressText").textContent = "Safe to close the app"; $("progressBar").classList.remove("indeterminate"); $("stopLoading").classList.add("hidden"); $("startLoading").textContent = "Continue building"; $("startLoading").classList.remove("hidden"); $("startLoading").disabled = false; setNavigationBusy(false); }
async function showCompletedDay(value, token) { const [day, auth] = await Promise.all([api(`/api/history/day?date=${value}&segment=${selectedSegment}`), api("/api/auth-status")]); if (token !== activeLoadToken || selectedDate !== value || loadCancelled) return; hiddenCreators = safeCount(day.hiddenCreators); artistTotal = day.artistCount; imageTotal = day.imageCount; loadedArtists = 0; galleryToken++; dayBuilt = true; activeRebuild = false; applyAuth(auth); const sentinel = document.createElement("div"); sentinel.id = "loadSentinel"; sentinel.className = "load-sentinel"; sentinel.textContent = "Loading more artists…"; $("gallery").appendChild(sentinel); showBuildSetup(false); $("loading").classList.add("hidden"); $("gallery").classList.remove("hidden"); segmentToolbar.classList.remove("hidden"); setNavigationBusy(false); await refreshBlockLabels(value); await loadMore(); await applyPendingRestore(); }
async function beginSelectedDay(rebuild = false) { const value = selectedDate, token = ++activeLoadToken; activeBuildSegment = selectedSegment; activeRebuild = rebuild; loadCancelled = false; resetLoadingPhases(); showBuildSetup(false); $("loading").classList.remove("hidden"); $("gallery").classList.add("hidden"); clearGallery(); $("startLoading").disabled = true; $("startLoading").classList.add("hidden"); $("stopLoading").classList.remove("hidden"); $("stopLoading").disabled = false; $("elapsedText").classList.remove("hidden"); $("loadingTitle").textContent = `${rebuild ? "Rebuilding" : "Building"} ${displayDate(value)}`; setNavigationBusy(true); updateProgress({ phase: "locating", elapsedSeconds: 0, itemCount: 0, creatorCount: 0 }); const request = { ...dayRequest(value), contentRating: buildCoverageRating }; let status = await api(rebuild ? "/api/history/rebuild" : "/api/history/start", { method: "POST", body: JSON.stringify(request) }); while (!status.complete) { if (token !== activeLoadToken) return; if (loadCancelled || status.state === "cancelled") return; if (status.state === "error") throw new Error(status.error || "Daily import failed"); updateProgress(status); await new Promise(resolve => setTimeout(resolve, 750)); if (loadCancelled || token !== activeLoadToken) return; status = await api(`/api/history/status?date=${value}&segment=${selectedSegment}`); } activeBuildSegment = null; setLoadingPhase("complete"); await showCompletedDay(value, token); refreshBlockLabels(value); }
async function beginFullDay(rebuild = false) {
  const value = selectedDate, token = ++activeLoadToken;
  activeRebuild = rebuild; loadCancelled = false; resetLoadingPhases();
  showBuildSetup(false);
  $("loading").classList.remove("hidden"); $("gallery").classList.add("hidden"); clearGallery();
  $("startLoading").disabled = true; $("startLoading").classList.add("hidden");
  $("stopLoading").classList.remove("hidden"); $("stopLoading").disabled = false;
  $("elapsedText").classList.remove("hidden"); setNavigationBusy(true);
  let completedItems = 0, completedCreators = 0, completedElapsed = 0;
  for (let index = 0; index < 2; index++) {
    const segment = ["morning", "evening"][index]; activeBuildSegment = segment;
    let status = await api(`/api/history/status?date=${value}&segment=${segment}`);
    $("loadingTitle").textContent = `${rebuild ? "Rebuilding" : "Building"} full day · ${segment === "morning" ? "Morning" : "Evening"}`;
    if (rebuild || !status.complete) {
      const endpoint = rebuild && status.archiveComplete ? "/api/history/rebuild" : "/api/history/start";
      status = await api(endpoint, { method: "POST", body: JSON.stringify({
        ...dayRequest(value, segment), contentRating: buildCoverageRating }) });
      while (!status.complete) {
        if (token !== activeLoadToken || loadCancelled || status.state === "cancelled") return;
        if (status.state === "error") throw new Error(status.error || "Daily import failed");
        updateProgress({ ...status, progress: (index * 50) + (Number(status.progress) || 0) / 2,
          itemCount: completedItems + safeCount(status.itemCount), creatorCount: completedCreators + safeCount(status.creatorCount),
          elapsedSeconds: completedElapsed + safeCount(status.elapsedSeconds) });
        await new Promise(resolve => setTimeout(resolve, 750));
        status = await api(`/api/history/status?date=${value}&segment=${segment}`);
      }
    }
    completedItems += safeCount(status.itemCount); completedCreators += safeCount(status.creatorCount);
    completedElapsed += Number(status.metrics?.elapsedSeconds) || 0;
  }
  activeBuildSegment = null; setLoadingPhase("complete");
  await showCompletedDay(value, token); refreshBlockLabels(value);
}
async function loadDay(value, preferAvailable = true, preserveCurrent = false) {
  const token = ++activeLoadToken; selectedDate = value; dayBuilt = false; activeRebuild = false; loadCancelled = false;
  $("selectedDate").textContent = displayDate(value); setNavigationBusy(false); await refreshBlockLabels(value);
  if (!preserveCurrent) {
    $("loading").classList.remove("hidden"); $("gallery").classList.add("hidden"); clearGallery();
    $("summary").textContent = ""; $("startLoading").classList.add("hidden"); $("stopLoading").classList.add("hidden");
  }
  let status = await api(`/api/history/status?date=${value}&segment=${selectedSegment}`);
  if (token !== activeLoadToken || selectedDate !== value) return;
  // A completed all-day union is the most useful representation of a date and is always
  // the automatic default. Passing preferAvailable=false is the explicit user action of
  // choosing a half from the selector, so that choice still remains available.
  if (preferAvailable && selectedSegment !== "all") {
    const allDay = await api(`/api/history/status?date=${value}&segment=all`);
    if (token !== activeLoadToken || selectedDate !== value) return;
    if (allDay.complete) {
      selectedSegment = "all"; $("daySegment").value = "all"; status = allDay;
    }
  }
  if (preferAvailable && !status.complete) {
    for (const candidate of ["all", "evening", "morning"]) {
      if (candidate === selectedSegment) continue;
      const available = await api(`/api/history/status?date=${value}&segment=${candidate}`);
      if (token !== activeLoadToken || selectedDate !== value) return;
      if (available.complete) { selectedSegment = candidate; $("daySegment").value = candidate; status = available; break; }
    }
  }
  clearGallery(); $("summary").textContent = "";
  if (status.complete) { await showCompletedDay(value, token); return; }
  $("loading").classList.remove("hidden"); $("gallery").classList.add("hidden");
  $("startLoading").classList.add("hidden"); $("stopLoading").classList.add("hidden"); showBuildReady(status);
}
// Tags explain both why an image ranked where it did and why one might be hidden, so
// they are shown rather than kept as an internal signal. "Not read yet" is stated
// plainly: it is a different thing from an image that genuinely carries no tags.
function renderDetailTags(detail) {
  const box = $("detailTags");
  if (!detail.known) {
    box.innerHTML = '<p class="detail-tags-note">Tags for this image have not been read yet.</p>';
    return;
  }
  if (!detail.tags?.length) {
    box.innerHTML = '<p class="detail-tags-note">Civitai lists no tags for this image.</p>';
    return;
  }
  box.innerHTML = `<p class="detail-tags-note">Tags</p>${detail.tags.map(tag =>
    `<span class="tag-chip${tag.hidden ? " is-hidden" : ""}"${tag.hidden
      ? ' title="You hide this tag on Civitai"' : ""}>${escapeHtml(tag.name)}</span>`).join("")}`;
}
async function showDetails(image, artist) { $("detailImage").src = image.thumbnailUrl; $("detailTags").innerHTML = ""; $("detailCreator").textContent = `@${artist.username}`; $("detailPrompt").textContent = "Loading generation details…"; $("details").showModal(); try { const detail = await api(`/api/history/image?id=${image.id}`); $("detailImage").src = detail.detailImageUrl || detail.thumbnailUrl; $("detailMeta").innerHTML = `<div><dt>Image</dt><dd>${safeCount(detail.id)}</dd></div><div><dt>Artist images</dt><dd>${safeCount(artist.imageCount)}</dd></div><div><dt>Model</dt><dd>${escapeHtml(detail.baseModel || "Unknown")}</dd></div><div><dt>Size</dt><dd>${escapeHtml(detail.width || "?")} × ${escapeHtml(detail.height || "?")}</dd></div><div><dt>Created</dt><dd>${escapeHtml(ago(detail.createdAt))}</dd></div><div><dt>Reactions</dt><dd>${safeCount(detail.stats?.reactionCount)}</dd></div>`; renderDetailTags(detail);
    $("detailPrompt").textContent = detail.prompt || "No prompt metadata available."; $("civitaiLink").href = detail.civitaiUrl; $("fullLink").href = detail.url; } catch (error) { $("detailPrompt").textContent = error.message; } }
const observer = new IntersectionObserver(entries => { if (entries.some(entry => entry.isIntersecting)) loadMore().catch(error => toast(error.message)); }, { rootMargin: "800px" });
new MutationObserver(() => { const sentinel = $("loadSentinel"); if (sentinel) observer.observe(sentinel); }).observe($("gallery"), { childList: true });
$("close").onclick = () => $("details").close(); $("details").onclick = event => { if (event.target === $("details")) $("details").close(); }; $("olderDay").onclick = () => loadDay(shiftDate(selectedDate, -1)).catch(showLoadError); $("newerDay").onclick = () => loadDay(shiftDate(selectedDate, 1)).catch(showLoadError); function showLoadError(error) {
  $("loadingTitle").textContent = "History failed to load";
  $("loadingMessage").textContent = error.message;
  // Nothing here used to clear the progress line or the bar's retry animation, so a
  // retry-exhausted failure kept showing the last "Retrying now · attempt 8 of 8" text
  // frozen underneath "History failed to load" — reading as still in progress when the
  // build had, in fact, completely stopped. Everything collected up to the failure was
  // saved incrementally, so recovery is the same "Continue building" as a manual stop,
  // not a dead end — and navigation was left disabled the whole time, with no way off
  // this screen except reloading.
  $("progressText").textContent = "Everything collected so far has been saved.";
  $("progressBar").classList.remove("indeterminate", "waiting");
  $("stopLoading").classList.add("hidden");
  $("startLoading").textContent = "Continue building";
  $("startLoading").classList.remove("hidden");
  $("startLoading").disabled = false;
  setNavigationBusy(false);
}
$("stopLoading").onclick = async () => { const wasRebuild = activeRebuild; loadCancelled = true; $("stopLoading").disabled = true; try { await api("/api/history/cancel", { method: "POST", body: JSON.stringify({ date: selectedDate, segment: activeBuildSegment || selectedSegment }) }); activeBuildSegment = null; if (wasRebuild) { toast("Rebuild stopped. Your previous gallery is unchanged."); await loadDay(selectedDate); } else showStopped(); } catch (error) { toast(error.message); } };
$("buildRange").querySelectorAll("[data-segment]").forEach(button => { button.onclick = () => {
  buildSegment = button.dataset.segment; refreshBuildChoices();
}; });
$("buildCoverage").querySelectorAll("[data-rating]").forEach(button => { button.onclick = () => {
  buildCoverageRating = button.dataset.rating; refreshBuildChoices();
}; });
$("completeDay").onclick = async () => {
  const missing = currentBlocks?.morning?.complete ? "evening" : "morning";
  selectedSegment = missing; buildSegment = missing; $("daySegment").value = missing;
  await loadDay(selectedDate, false);
};
$("startLoading").onclick = async () => {
  if ($("startLoading").disabled) return;
  $("startLoading").disabled = true;
  try {
    // The first view should always be something the chosen collection coverage holds.
    // Afterward the normal browsing selector remains completely independent and local.
    const levels = { Soft: [1, 2], Mature: [1, 2, 4], X: [1, 2, 4, 8, 16] }[buildCoverageRating];
    const settings = await api("/api/settings", { method: "POST",
      body: JSON.stringify({ browsingLevels: levels }) });
    contentRating = settings.contentRating;
    visibleBrowsingLevels = new Set(settings.browsingLevels);
    showContentRating();
    selectedSegment = buildSegment; $("daySegment").value = buildSegment;
    await (buildSegment === "all" ? beginFullDay(false) : beginSelectedDay(false));
  } catch (error) { showLoadError(error); }
};
$("rebuildDay").onclick = () => { if (!dayBuilt || !confirm(`Rebuild ${displayDate(selectedDate)}? Your current gallery will remain safe if you stop.`)) return; (selectedSegment === "all" ? beginFullDay(true) : beginSelectedDay(true)).catch(showLoadError); };
async function closeApplication() { loadCancelled = true; document.body.innerHTML = '<main id="loading"><div class="history-loading"><h2>App closed</h2><p>You can close this browser tab. Launch the executable again whenever you want to continue.</p></div></main>'; try { await api("/api/app/close", { method: "POST", body: "{}" }); } catch (_) {} }
$("closeApp").onclick = closeApplication; $("closeLoading").onclick = closeApplication;
async function connectCivitai() { await api("/api/oauth/login", { method: "POST", body: "{}" }); let auth; do { await new Promise(resolve => setTimeout(resolve, 1000)); auth = await refreshAuth(); if (auth.oauthJob?.state === "error") throw new Error(auth.oauthJob.error); } while (!auth.connected); if (dayBuilt) await loadDay(selectedDate); else await refreshVisibleCreatorMetadata(); toast(`Signed in as @${auth.username}`); return auth; }
$("connect").onclick = () => connectCivitai(); $("disconnect").onclick = async () => {
  try {
    await api("/api/oauth/disconnect", { method: "POST", body: "{}" });
    applyAuth({ connected: false });
    // There is no signed-out view of this app, so signing out returns to the front door
    // rather than leaving a gallery on screen that can no longer do anything.
    try { sessionStorage.removeItem(FEED_STATE); } catch (_) {}
    dayBuilt = false;
    clearGallery(); $("gallery").classList.add("hidden");
    $("loading").classList.add("hidden"); $("summary").textContent = "";
    setChrome(false);
    $("welcomeTitle").textContent = "Sign in to Civitai";
    $("welcomeStatus").textContent = "";
    $("welcomeConnect").classList.remove("hidden"); $("welcomeConnect").disabled = false;
    $("welcome").classList.remove("hidden");
    await refreshOwnApp();
    toast("Signed out. The saved session was removed from this computer.");
  } catch (error) { toast(error.message); }
};
$("modelFilter").onclick = () => {
  const menu = $("modelMenu"), open = menu.classList.toggle("hidden");
  $("modelFilter").setAttribute("aria-expanded", String(!open));
  $("contentMenu").classList.add("hidden");
  $("contentFilter").setAttribute("aria-expanded", "false");
  if (!open) refreshModelMenu();
};
$("contentFilter").onclick = () => {
  const opening = $("contentMenu").classList.toggle("hidden");
  $("contentFilter").setAttribute("aria-expanded", String(!opening));
  $("modelMenu").classList.add("hidden");
};
$("contentMenu").querySelectorAll("[data-level]").forEach(button => {
  button.onclick = () => {
    const level = Number(button.dataset.level), next = new Set(visibleBrowsingLevels);
    next.has(level) ? next.delete(level) : next.add(level);
    if (!next.size) return toast("Select at least one browsing level.");
    chooseContentRating([...next]);
  };
});
document.addEventListener("click", event => {
  if (!event.target.closest("#modelMenu") && !event.target.closest("#modelFilter")) {
    $("modelMenu").classList.add("hidden");
    $("modelFilter").setAttribute("aria-expanded", "false");
  }
  if (!event.target.closest("#contentMenu") && !event.target.closest("#contentFilter")) {
    $("contentMenu").classList.add("hidden");
    $("contentFilter").setAttribute("aria-expanded", "false");
  }
});
$("daySegment").onchange = () => { selectedSegment = $("daySegment").value; loadDay(selectedDate, false).catch(showLoadError); };
// A view change re-pages the whole day from the server, because the ordering applies to
// every creator in the archive and not only to the cards already loaded.
// Follower counts exist only for creators the gallery has already loaded, so an
// emerging-first ordering needs the rest of the day fetched once. It runs in the
// background and the view refreshes itself when it finishes.
let sweepWatch = 0;
const sweepLabels = { followers: "Reading follower counts", tags: "Reading artwork tags" };
async function ensureViewData(kind) {
  const note = $("followerSweep"), url = `/api/history/prepare?date=${selectedDate}&segment=${selectedSegment}&kind=${kind}`;
  try {
    let state = await api(url);
    if (state.complete) { note.classList.add("hidden"); return; }
    if (!oauthConnected) { note.textContent = "Connect Civitai to prepare this view"; note.classList.remove("hidden"); return; }
    const watch = ++sweepWatch;
    note.classList.remove("hidden");
    if (!state.job?.running) await api("/api/history/prepare", { method: "POST", body: JSON.stringify({ date: selectedDate, segment: selectedSegment, kind }) });
    while (watch === sweepWatch) {
      state = await api(url);
      note.textContent = `${sweepLabels[kind]}… ${displayCount(state.known)} of ${displayCount(state.total)}`;
      if (state.complete || !state.job?.running) break;
      await new Promise(resolve => setTimeout(resolve, 1200));
    }
    if (watch !== sweepWatch) return;
    note.classList.add("hidden");
    await reloadView();
  } catch (error) { note.textContent = error.message; }
}
async function reloadView() {
  const token = ++activeLoadToken;
  // Invalidate any page already in flight, then wait for it to actually finish. Without
  // the wait, the fresh load is refused by the in-flight guard and the discarded one
  // never retries, leaving the gallery empty.
  galleryToken++;
  const deadline = Date.now() + 8000;
  while (loadingMore && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 50));
  if (token !== activeLoadToken) return;
  loadedArtists = 0;
  artistTotal = Number.MAX_SAFE_INTEGER;
  clearGallery();
  const sentinel = document.createElement("div"); sentinel.id = "loadSentinel";
  sentinel.className = "load-sentinel"; sentinel.textContent = "Loading more artists…";
  $("gallery").appendChild(sentinel);
  try { await loadMore(); } catch (error) { toast(error.message); }
  if (token === activeLoadToken && !loadedArtists) $("summary").textContent = "No creators match this view";
}
$("dayView").onchange = async () => {
  selectedView = $("dayView").value;
  if (!dayBuilt) return;
  const needs = { emerging: "followers", foryou: "tags" }[selectedView];
  if (needs) ensureViewData(needs); else { sweepWatch++; $("followerSweep").classList.add("hidden"); }
  await reloadView();
};
const yesterday = new Date(); yesterday.setDate(yesterday.getDate() - 1); newestDate = localDateString(yesterday);
// First launch runs connect, then read the account's own history, then the day. The
// gallery's default ordering is personal, so without those two steps the app opens on a
// view it cannot fill and looks broken rather than unconfigured.
function setChrome(visible) {
  ["accountStatus", "connect", "disconnect", "olderDay", "newerDay",
   "selectedDate", "rebuildDay", "summary"].forEach(id => $(id).classList.toggle("hidden", !visible));
  segmentToolbar.classList.toggle("hidden", !visible);
  document.querySelector(".view-tabs").classList.toggle("hidden", !visible);
}
async function openDay() {
  setChrome(true);
  $("welcome").classList.add("hidden");
  // Come back to where browsing left off rather than to the top of the newest day.
  const saved = readFeedState();
  if (saved?.date) {
    selectedSegment = saved.segment || selectedSegment;
    selectedView = saved.view || selectedView;
    selectedModels = new Set(saved.models || []);
    $("daySegment").value = selectedSegment; $("dayView").value = selectedView;
    $("modelFilter").textContent = modelButtonLabel();
    const state = await api(`/api/history/status?date=${saved.date}&segment=${selectedSegment}`);
    if (state.complete) { pendingRestore = saved; return loadDay(saved.date, true); }
  }
  const status = await api(`/api/history/status?date=${newestDate}&segment=all`);
  if (status.complete) { selectedSegment = "all"; $("daySegment").value = "all"; }
  return loadDay(newestDate);
}
let pendingRestore = null;
async function applyPendingRestore() {
  const saved = pendingRestore; pendingRestore = null;
  if (!saved || saved.date !== selectedDate || (saved.segment || "evening") !== selectedSegment) return;
  const target = Math.min(saved.loaded || 0, artistTotal || 0);
  let guard = 0;
  while (loadedArtists < target && guard++ < 40) { await loadMore(); }
  if (saved.scrollY) window.scrollTo({ top: saved.scrollY, behavior: "auto" });
}
async function runFirstAnalysis() {
  $("welcomeStatus").textContent = "Reading the artwork you have reacted to…";
  try {
    await api("/api/discovery/sync", { method: "POST", body: "{}" });
    for (;;) {
      const state = await api("/api/discovery/status");
      $("welcomeStatus").textContent = state.message || "Working…";
      if (!state.running) break;
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
  } catch (error) { $("welcomeStatus").textContent = `Skipping analysis: ${error.message}`; }
}
async function startup() {
  try { const settings = await api("/api/settings"); contentRating = settings.contentRating || "Soft"; visibleBrowsingLevels = new Set(settings.browsingLevels || [1, 2]); showContentRating(); } catch (_) { showContentRating(); }
  let auth = {};
  try { auth = await api("/api/auth-status"); } catch (_) { auth = { connected: false }; }
  applyAuth(auth);
  // Only a genuine first launch is onboarded. Someone with archives already made this
  // choice, and re-asking every launch would be nagging rather than helping.
  // The gallery is ordered by the user's own reactions and its actions are follows and
  // reactions, so there is nothing to show a signed-out visitor. Sign-in is the entry
  // point rather than an optional upgrade.
  if (!auth.connected) {
    setChrome(false); $("welcome").classList.remove("hidden"); $("loading").classList.add("hidden");
    await refreshOwnApp();
    return;
  }
  const summary = await api("/api/discovery/summary").catch(() => ({ hasData: true }));
  if (!summary.hasData) {
    setChrome(false);
    $("welcome").classList.remove("hidden"); $("loading").classList.add("hidden");
    $("welcomeTitle").textContent = "Getting to know your taste";
    $("welcomeBody").textContent = "Reading the artwork you have reacted to on Civitai. This takes about a minute and only happens once.";
    $("welcomeConnect").classList.add("hidden");
    await runFirstAnalysis();
  }
  return openDay();
}
$("welcomeConnect").onclick = async () => {
  $("welcomeConnect").disabled = true;
  $("welcomeStatus").textContent = "Waiting for Civitai in your browser…";
  try {
    await connectCivitai();
    // The analysis survives a sign-out now, so this is not necessarily a first-time
    // wait. Someone signing back in already has it; topping it up with anything reacted
    // to in the meantime happens quietly, not behind the full onboarding screen — "this
    // takes about a minute and only happens once" would be a lie the second time.
    const existing = await api("/api/discovery/summary").catch(() => ({ hasData: false }));
    if (existing.hasData) {
      api("/api/discovery/sync", { method: "POST", body: "{}" }).catch(() => {});
    } else {
      $("welcomeTitle").textContent = "Getting to know your taste";
      $("welcomeConnect").classList.add("hidden");
      await runFirstAnalysis();
    }
    await openDay();
  } catch (error) { $("welcomeStatus").textContent = error.message; $("welcomeConnect").disabled = false; }
};
// Using the built-in registration is a convenience, not a requirement. Anyone who would
// rather not route their access through someone else's application can point this at
// their own without editing anything.
async function refreshOwnApp() {
  try {
    const info = await api("/api/oauth/client");
    $("ownAppRedirect").textContent = info.redirectUri;
    $("ownAppInput").value = info.isCustom ? info.clientId : "";
    $("ownAppReset").classList.toggle("hidden", !info.isCustom);
    // Connecting is impossible until an application exists to authorize against, so the
    // button says why rather than failing after the click.
    $("welcomeConnect").disabled = !info.configured;
    $("welcomeConnect").title = info.configured ? ""
      : "Set up your Civitai application first";
    $("ownAppPanel").classList.toggle("is-done", info.isCustom);
    // When the build ships its own application there is nothing for a new user to set up,
    // so onboarding does not mention it at all. The setup step appears only in builds that
    // ship without one, where it is genuinely required. Bringing your own application is
    // still possible through CIVITAI_OAUTH_CLIENT_ID.
    const needsSetup = !info.hasBuiltin;
    $("ownAppToggle").classList.add("hidden");
    $("ownAppPanel").classList.toggle("hidden", !needsSetup && !info.isCustom);
    $("ownAppToggle").textContent = info.isCustom
      ? "Connecting through your own Civitai application"
      : "Prefer to use your own Civitai application?";
    $("ownAppLead").textContent = info.hasBuiltin
      ? "By default this connects through the application registered by whoever built the app. If you would rather your access ran entirely under your own registration, create one on Civitai and paste its client ID here."
      : "This app has no Civitai application of its own, on purpose — that would route everyone's access through one person's account. Connecting takes a one-time setup of your own, and everything then runs under your registration.";
    $("ownAppInput").disabled = info.fromEnvironment;
    $("ownAppSave").disabled = info.fromEnvironment;
    if (info.fromEnvironment) $("ownAppStatus").textContent =
      "Set by CIVITAI_OAUTH_CLIENT_ID in this environment, which takes precedence.";
    return info;
  } catch (error) { $("ownAppStatus").textContent = error.message; return {}; }
}
async function saveOwnApp(clientId) {
  $("ownAppSave").disabled = true;
  try {
    await api("/api/oauth/client", { method: "POST", body: JSON.stringify({ clientId }) });
    await refreshOwnApp();
    await refreshAuth();
    $("ownAppStatus").textContent = clientId
      ? "Saved. Connect Civitai to authorize against your application."
      : "Removed. Set up an application to connect again.";
  } catch (error) { $("ownAppStatus").textContent = error.message; }
  finally { $("ownAppSave").disabled = false; }
}
$("ownAppToggle").onclick = () => {
  $("ownAppToggle").dataset.opened = "1";
  $("ownAppPanel").classList.toggle("hidden");
};
$("ownAppSave").onclick = () => saveOwnApp($("ownAppInput").value.trim());
$("ownAppReset").onclick = () => saveOwnApp("");
startup().catch(showLoadError);
let discoveryPolling = false, discoveryLoaded = false;
// Suggestions belong where browsing happens. They are spaced through the For You feed
// rather than listed on a separate page, so meeting a new creator feels like scrolling
// rather than like reading a report.
// Filtering by the model that generated the artwork. Selections are kept while you move
// between days, windows and views, because a filter you have to reapply is a filter you
// stop using.
function modelQuery() {
  return [...selectedModels].map(name => `&model=${encodeURIComponent(name)}`).join("");
}
function modelButtonLabel() {
  const count = selectedModels.size;
  return count === 0 ? "Model: all" : count === 1 ? `Model: ${[...selectedModels][0]}` : `Model: ${count} selected`;
}
async function refreshModelMenu() {
  const menu = $("modelMenu");
  try {
    const data = await api(`/api/history/models?date=${selectedDate}&segment=${selectedSegment}`);
    const models = data.models || [];
    if (!models.length) { menu.innerHTML = '<p class="filter-empty">No model information for this day.</p>'; return; }
    menu.innerHTML = `<div class="filter-head"><strong>Generation model</strong><button id="modelClear" class="quiet-button">Clear</button></div>` +
      models.map(entry => `<label class="filter-row"><input type="checkbox" value="${escapeHtml(entry.model)}"${selectedModels.has(entry.model) ? " checked" : ""}><span>${escapeHtml(entry.model)}</span><em>${displayCount(entry.images)}</em></label>`).join("");
    menu.querySelectorAll("input[type=checkbox]").forEach(box => {
      box.onchange = () => {
        box.checked ? selectedModels.add(box.value) : selectedModels.delete(box.value);
        $("modelFilter").textContent = modelButtonLabel();
        saveFeedState();
        reloadView();
      };
    });
    $("modelClear").onclick = () => {
      if (!selectedModels.size) return;
      selectedModels.clear();
      menu.querySelectorAll("input[type=checkbox]").forEach(box => { box.checked = false; });
      $("modelFilter").textContent = modelButtonLabel();
      saveFeedState();
      reloadView();
    };
  } catch (error) { menu.innerHTML = `<p class="filter-empty">${escapeHtml(error.message)}</p>`; }
}
function profileUrl(username) { return `https://civitai.red/user/${encodeURIComponent(username || "")}`; }
function followerText(creator) { return creator.followers === null || creator.followers === undefined ? "" : ` · ${displayCount(creator.followers)} followers`; }
function emergingPill(creator) { return creator.emerging ? '<span class="pill emerging" title="Fewer than 1,000 followers">EMERGING</span>' : ""; }
// Widths are applied through CSSOM afterwards because the page's Content Security
// Policy forbids inline style attributes.
function barRow(label, value, fraction) { return `<div class="bar-row"><span class="bar-label">${escapeHtml(label)}</span><span class="bar-track"><span class="bar-fill" data-fill="${Math.max(0, Math.min(100, fraction * 100)).toFixed(1)}"></span></span><span class="bar-value">${escapeHtml(value)}</span></div>`; }
function applyBarWidths(container) { container.querySelectorAll(".bar-fill").forEach(fill => { fill.style.width = `${fill.dataset.fill}%`; }); }
function rankList(items, empty, extra = "") { return items.length ? `<div class="rank-list ${extra}">${items.join("")}</div>` : `<p class="empty-note">${escapeHtml(empty)}</p>`; }
function metricCard(label, value, hint, accent) { return `<div class="metric-card${accent ? " accent" : ""}"><span class="label">${escapeHtml(label)}</span><span class="value">${escapeHtml(displayCount(value))}</span><span class="hint">${escapeHtml(hint)}</span></div>`; }
// Categorical slots validated against this dark surface: adjacent-pair separation
// holds for normal vision and for colour-vision deficiency. Fixed order, never cycled.
const reactionColors = { Like: "#3987e5", Heart: "#d95926", Laugh: "#199e70", Cry: "#c98500", Dislike: "#9085e9" };
const DONUT_RADIUS = 38, DONUT_GAP = 2;
function donut(mix, totalRecords) {
  const circumference = 2 * Math.PI * DONUT_RADIUS;
  const drawn = mix.filter(entry => entry.count > 0);
  let offset = 0;
  const arcs = drawn.map(entry => {
    const raw = (entry.count / (totalRecords || 1)) * circumference;
    // Keep a hairline for tiny shares; the legend beside it carries the exact count.
    const length = drawn.length > 1 ? Math.max(raw - DONUT_GAP, 1) : circumference;
    const arc = `<circle class="donut-arc" cx="50" cy="50" r="${DONUT_RADIUS}" fill="none" stroke="${reactionColors[entry.reaction] || "#9085e9"}" stroke-width="13" stroke-dasharray="${length.toFixed(2)} ${(circumference - length).toFixed(2)}" stroke-dashoffset="${(-offset).toFixed(2)}"><title>${escapeHtml(entry.reaction)}: ${displayCount(entry.count)} (${entry.percent}%)</title></circle>`;
    offset += raw;
    return arc;
  }).join("");
  const chart = drawn.length
    ? `<svg class="donut" viewBox="0 0 100 100" role="img" aria-label="Reaction mix"><g transform="rotate(-90 50 50)"><circle cx="50" cy="50" r="${DONUT_RADIUS}" fill="none" stroke="#2b2d34" stroke-width="13"></circle>${arcs}</g></svg>`
    : `<svg class="donut" viewBox="0 0 100 100" role="img" aria-label="No reactions yet"><circle cx="50" cy="50" r="${DONUT_RADIUS}" fill="none" stroke="#2b2d34" stroke-width="13"></circle></svg>`;
  const legend = mix.map(entry => `<div class="donut-legend-row"><span class="swatch" data-swatch="${escapeHtml(entry.reaction)}"></span><span class="donut-legend-name">${escapeHtml(entry.reaction)}</span><span class="donut-legend-value">${displayCount(entry.count)} · ${entry.percent}%</span></div>`).join("");
  return `<div class="metric-card reaction-card"><span class="label">Reaction mix</span><div class="donut-body"><div class="donut-wrap">${chart}<span class="donut-centre"><b>${displayCount(totalRecords)}</b><small>reactions</small></span></div><div class="donut-legend">${legend}</div></div></div>`;
}
async function followFromDashboard(row) {
  const button = row.querySelector(".follow-button");
  if (!socialWrite) return toast("Civitai did not grant follow access.");
  const following = button.classList.contains("is-following");
  button.disabled = true;
  try {
    const result = await api("/api/follow", { method: "POST", body: JSON.stringify({
      userId: Number(row.dataset.userId), username: row.dataset.username, following: !following }) });
    button.classList.toggle("is-following", result.following);
    button.textContent = result.following ? "✓ Following" : "+ Follow";
    toast(result.following ? `Now following @${row.dataset.username}` : `Unfollowed @${row.dataset.username}`);
    // The headline count is derived from the same record the server just updated, so
    // refresh it rather than guessing. The row stays put until the next analysis.
    const data = await api("/api/discovery/summary");
    $("summaryRow").querySelectorAll(".metric-card .value")[3].textContent = displayCount(data.creatorsNotFollowed);
    $("summaryRow").querySelectorAll(".metric-card .value")[0].textContent = displayCount(data.followedCreators);
  } catch (error) { toast(error.message); }
  finally { button.disabled = !socialWrite; }
}
function renderDiscovery(data) {
  const body = $("discoveryBody"), has = !!data.hasData;
  body.classList.toggle("hidden", !has);
  $("resetDiscovery").classList.toggle("hidden", !has);
  $("syncDiscovery").textContent = has ? "Refresh from Civitai" : "Analyse my reactions";
  if (!has) return;
  const total = safeCount(data.reactedImages);
  $("summaryRow").innerHTML = [
    metricCard("Creators you follow", data.followedCreators, "Exact count from your Civitai account", true),
    metricCard("Images you reacted to", total, "Your complete reaction history"),
    metricCard("Creators you reacted to", data.creatorsReactedTo, "Distinct artists in that history"),
    metricCard("Not yet followed", data.creatorsNotFollowed, "Reacted to 5+ times, but you do not follow them"),
    donut(data.reactionMix || [], data.reactionRecords),
  ].join("");
  let fingerprintPanel = $("creativeFingerprint");
  if (!fingerprintPanel) {
    fingerprintPanel = document.createElement("section");
    fingerprintPanel.id = "creativeFingerprint";
    fingerprintPanel.className = "panel wide fingerprint-panel";
    fingerprintPanel.innerHTML = `<div class="panel-head"><div><h3>Your creative fingerprint</h3><p id="fingerprintNote" class="panel-note"></p></div></div><div class="fingerprint-grid"><div><h4>Strong visual signals</h4><div id="fingerprintTags" class="fingerprint-tags"></div></div><div><h4>Model signals</h4><div id="fingerprintModels"></div></div></div>`;
    document.querySelector(".panel-grid").prepend(fingerprintPanel);
  }
  const fingerprint = data.recentWork || {};
  fingerprintPanel.classList.toggle("hidden", !safeCount(fingerprint.images));
  if (safeCount(fingerprint.images)) {
    const coverage = fingerprint.complete ? "your public upload history" : "the uploads collected so far";
    $("fingerprintNote").textContent = `Built from ${displayCount(fingerprint.images)} images across ${coverage}. Future refreshes stop at the first known upload and add only new images. Strong tags appear in at least 10% of the archive and occur at least 50% more often here than in the Civitai comparison sample; generic and one-off tags are left out.`;
    $("fingerprintTags").innerHTML = (fingerprint.strongTags || []).map(tag =>
      `<span class="fingerprint-tag"><b>${escapeHtml(tag.name)}</b><small>${displayCount(tag.images)} of ${displayCount(fingerprint.images)}${tag.lift ? ` · ×${tag.lift} distinctive` : ""}</small></span>`).join("") || '<span class="empty-note">More comparison data is needed to identify strong tags.</span>';
    $("fingerprintModels").innerHTML = rankList((fingerprint.models || []).slice(0, 8).map(model => {
      const version = model.versionName && model.versionName !== model.modelName ? ` · ${model.versionName}` : "";
      const label = model.modelName ? `${model.modelName}${version}` : (model.versionName || `Model version ${model.id}`);
      const name = model.modelId
        ? `<a href="https://civitai.red/models/${escapeHtml(model.modelId)}?modelVersionId=${escapeHtml(model.id)}" target="_blank" rel="noopener">${escapeHtml(label)}</a>`
        : escapeHtml(label);
      return `<div class="rank-item"><span class="rank-name">${name}</span><span class="rank-value">${displayCount(model.images)} images · ${model.percent}%</span></div>`;
    }), "No model information was published with these images.");
  }
  const topTagMax = Math.max(1, ...(data.topTags || []).map(tag => tag.images));
  $("topTags").innerHTML = (data.topTags || []).map(tag => barRow(tag.name, `${displayCount(tag.images)} · ${tag.percent}%`, tag.images / topTagMax)).join("");
  applyBarWidths($("topTags"));
  $("distinctiveNote").textContent = data.baselineImages ? `Tags you react to far more often than a ${displayCount(data.baselineImages)}-image sample of Civitai shows. “×4” means four times the usual rate.` : "A Civitai comparison sample is not available yet.";
  $("distinctiveTags").innerHTML = rankList((data.distinctiveTags || []).map(tag => `<div class="rank-item"><span class="rank-name">${escapeHtml(tag.name)}</span><span class="rank-value"><span class="pill lift">×${tag.lift}</span> ${displayCount(tag.images)} images</span></div>`), "No tag stands out from the sample yet.");
  $("topCreators").innerHTML = rankList((data.topCreators || []).map(creator => `<div class="rank-item"><span class="rank-name"><a href="${escapeHtml(profileUrl(creator.username))}" target="_blank" rel="noopener">@${escapeHtml(creator.username)}</a>${creator.following ? '<span class="pill">FOLLOWING</span>' : ""}${emergingPill(creator)}</span><span class="rank-value">${displayCount(creator.images)} images${followerText(creator)}</span></div>`), "No creators recorded yet.");
  const notFollowed = data.reactedNotFollowed || [];
  $("notFollowed").innerHTML = rankList(notFollowed.map(creator => `<div class="rank-item" data-user-id="${escapeHtml(creator.id)}" data-username="${escapeHtml(creator.username)}"><span class="rank-name"><a href="${escapeHtml(profileUrl(creator.username))}" target="_blank" rel="noopener">@${escapeHtml(creator.username)}</a>${emergingPill(creator)}</span><span class="rank-value">${displayCount(creator.images)} images${followerText(creator)}</span><button class="follow-button" ${socialWrite ? "" : "disabled"} title="${socialWrite ? "" : "Civitai did not grant follow and reaction access."}">+ Follow</button></div>`), "No creator you react to 5 or more times goes unfollowed.", "dense");
  $("notFollowed").querySelectorAll(".rank-item").forEach(row => { row.querySelector(".follow-button").onclick = () => followFromDashboard(row); });
  $("notFollowedNote").textContent = notFollowed.length
    ? `Creators you've reacted to 5 or more times without following — the clearest signal you have. Showing the top ${notFollowed.length} of ${displayCount(data.creatorsNotFollowed)}. The same signal marks their card ♥ in the daily gallery.`
    : "Creators you've reacted to 5 or more times without following.";
  // Suggestions moved into the For You feed, where browsing happens.
  const age = data.lastSyncAt ? (Date.now() - new Date(data.lastSyncAt).getTime()) / 1000 : 0;
  const when = age < 120 ? "moments ago" : ago(data.lastSyncAt);
  $("discoverySubtitle").textContent = `Read from your Civitai account ${when}. ${displayCount(total)} reacted images across ${displayCount(data.distinctTags)} tags. Everything stays on this computer.`;
}
// Content Controls are read from Civitai at sign-in and applied to the daily gallery.
// That filtering is otherwise invisible unless the user notices fewer creators than
// expected, so this states it plainly and links straight to where it is managed.
function renderHiddenPreferencesNote(data) {
  const note = $("hiddenPreferencesNote"), total = safeCount(data.creators) + safeCount(data.tags) + safeCount(data.images);
  $("hiddenPreferencesText").textContent = !data.importedAt
    ? "Civitai Content Controls will be applied to the daily gallery once your account is read."
    : total
      ? `Your Civitai Content Controls are applied to the daily gallery: ${displayCount(data.creators)} hidden creator${safeCount(data.creators) === 1 ? "" : "s"}, ${displayCount(data.tags)} hidden tag${safeCount(data.tags) === 1 ? "" : "s"}, ${displayCount(data.images)} hidden image${safeCount(data.images) === 1 ? "" : "s"}.`
      : "Your Civitai Content Controls are applied to the daily gallery. Nothing is hidden there yet.";
  note.classList.remove("hidden");
}
async function loadHiddenPreferencesNote() {
  try { renderHiddenPreferencesNote(await api("/api/discovery/hidden")); }
  catch (error) { console.warn("Content controls summary unavailable", error); }
}
$("hiddenPreferencesRefresh").onclick = async () => {
  const button = $("hiddenPreferencesRefresh");
  button.disabled = true;
  try {
    renderHiddenPreferencesNote(await api("/api/discovery/hidden?refresh=1"));
    toast("Re-read your Civitai Content Controls.");
  } catch (error) { toast(error.message); }
  finally { button.disabled = false; }
};
function applyDiscoverySync(sync) {
  const running = !!sync.running;
  $("syncDiscovery").disabled = running;
  $("stopDiscovery").classList.toggle("hidden", !running);
  $("resetDiscovery").disabled = running;
  const message = $("discoveryMessage");
  message.classList.toggle("error", sync.phase === "error");
  message.textContent = sync.error ? `${sync.message} ${sync.error}` : sync.message || "";
  return running;
}
async function refreshDiscovery() { const data = await api("/api/discovery/summary"); applyDiscoverySync(data.sync || {}); renderDiscovery(data); return data; }
async function pollDiscovery() {
  if (discoveryPolling) return;
  discoveryPolling = true;
  try {
    // The caller just rendered current data before calling this. Only re-render again
    // once a sync is actually observed finishing — otherwise this redundantly re-fetches
    // and re-renders moments after the first render, for no reason, on every tab open.
    // A fast click landing in that window (a real user's or a test's) was getting its own
    // DOM update silently replaced by the second, stale-by-comparison render.
    let observedRunning = false;
    while (applyDiscoverySync(await api("/api/discovery/status"))) {
      observedRunning = true;
      await new Promise(resolve => setTimeout(resolve, 900));
    }
    if (observedRunning) await refreshDiscovery();
  }
  catch (error) { toast(error.message); }
  finally { discoveryPolling = false; }
}
function showView(name) {
  const discovery = name === "discovery";
  if (discovery) pauseSeenTracking();
  $("tabGallery").classList.toggle("active", !discovery); $("tabGallery").setAttribute("aria-pressed", String(!discovery));
  $("tabDiscovery").classList.toggle("active", discovery); $("tabDiscovery").setAttribute("aria-pressed", String(discovery));
  segmentToolbar.classList.toggle("hidden", discovery);
  ["olderDay", "newerDay", "selectedDate", "rebuildDay", "summary"].forEach(id => $(id).classList.toggle("hidden", discovery));
  $("discovery").classList.toggle("hidden", !discovery);
  $("loading").classList.toggle("hidden", discovery || dayBuilt);
  $("gallery").classList.toggle("hidden", discovery || !dayBuilt);
  if (!discovery) { resumeSeenTracking(); return; }
  // Connection state is otherwise only applied once a completed day loads, so opening
  // this tab on an unbuilt day would leave the sync button believing it is disconnected.
  refreshAuth().catch(error => console.warn("Connection state could not be refreshed", error));
  if (discoveryLoaded) return;
  discoveryLoaded = true;
  loadHiddenPreferencesNote();
  refreshDiscovery().then(() => pollDiscovery()).catch(error => { $("discoveryMessage").textContent = error.message; });
}
$("tabGallery").onclick = () => showView("gallery");
$("tabDiscovery").onclick = () => showView("discovery");
$("syncDiscovery").onclick = async () => {
  if (!oauthConnected) return toast("Connect Civitai to analyse your reactions.");
  $("syncDiscovery").disabled = true;
  try { applyDiscoverySync(await api("/api/discovery/sync", { method: "POST", body: "{}" })); pollDiscovery(); }
  catch (error) { $("syncDiscovery").disabled = false; toast(error.message); }
};
$("stopDiscovery").onclick = async () => { $("stopDiscovery").disabled = true; try { await api("/api/discovery/sync/stop", { method: "POST", body: "{}" }); } catch (error) { toast(error.message); } finally { $("stopDiscovery").disabled = false; } };
$("resetDiscovery").onclick = async () => {
  if (!confirm("Delete the discovery data stored on this computer? Your archived daily galleries and your Civitai reactions are not affected.")) return;
  try { await api("/api/discovery/reset", { method: "POST", body: "{}" }); await refreshDiscovery(); toast("Discovery data deleted from this computer."); }
  catch (error) { toast(error.message); }
};
