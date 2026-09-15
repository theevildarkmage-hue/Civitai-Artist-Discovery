// Video artwork. A video card shows its still frame like an image card and plays by
// itself, muted, while it is mostly on screen. The playback file is fetched only for
// cards in view and the download is aborted as soon as a card leaves.
const VISIBLE_SHARE = 0.5;
// A card flicked past during a fast scroll never starts a download.
const SETTLE_MS = 300;
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
const cards = new Set();
// Card videos pause while something covers the gallery (the details dialog) or the tab
// is in the background, and resume where they are visible once it is uncovered.
let suspended = false;
const visibility = new IntersectionObserver(entries => {
  entries.forEach(entry => entry.target.setVideoVisible?.(entry.intersectionRatio >= VISIBLE_SHARE));
}, { threshold: [0, VISIBLE_SHARE] });

function refreshAll() { [...cards].forEach(el => el.resumeVideo?.()); }
document.addEventListener('visibilitychange', refreshAll);
reducedMotion.addEventListener?.('change', refreshAll);

export function suspendCardVideos(value) {
  suspended = !!value;
  refreshAll();
}

export function attachCardVideo(el, main) {
  const video = document.createElement('video');
  video.className = 'card-video';
  video.muted = true; video.loop = true; video.playsInline = true;
  video.preload = 'none'; video.hidden = true;
  video.setAttribute('aria-hidden', 'true');
  main.after(video);
  const toggle = document.createElement('button');
  toggle.type = 'button'; toggle.className = 'video-toggle'; toggle.hidden = true;
  el.querySelector('.image-stage').append(toggle);
  // frameOnly: the CDN had no still for this video (it redirects some videos straight to
  // the original file), so the card shows the video's own first frame instead. That reads
  // the file's header and first frame, not the whole file.
  let source = '', frameOnly = false, visible = false, pausedByUser = false, playing = false, attached = false, settleTimer = 0;

  function label() {
    toggle.textContent = playing ? '❚❚' : '▶';
    toggle.setAttribute('aria-label', playing ? 'Pause video' : 'Play video');
    toggle.title = playing ? 'Pause video' : 'Play video';
  }
  function showFirstFrame() {
    video.preload = 'metadata';
    video.src = `${source}#t=0.1`;
    video.hidden = false;
  }
  function stop() {
    playing = false;
    el.classList.remove('video-playing', 'video-loading');
    label();
    if (frameOnly && source) {
      // Reassigning the source aborts the playing download and keeps only a frame.
      video.pause(); showFirstFrame(); return;
    }
    if (video.getAttribute('src')) {
      video.pause();
      // Dropping the source aborts the download; pausing alone keeps buffering it.
      video.removeAttribute('src');
      video.load();
    }
    video.preload = 'none';
    video.hidden = true;
  }
  function ready() {
    // Artwork is attached only after the tag check clears it, and never mid-navigation.
    return !!source && !!el.dataset.imagesActive && el.getAttribute('aria-busy') !== 'true' &&
      document.body.contains(el);
  }
  function play() {
    if (playing || !ready()) return;
    playing = true;
    el.classList.add('video-loading');
    if (!frameOnly) video.poster = main.currentSrc || main.src || '';
    video.preload = 'auto';
    video.src = source;
    video.play().catch(() => { if (playing) stop(); });
    label();
  }
  // Decide from scratch whether this card should be playing right now.
  function update() {
    // A card dropped with the rest of the gallery (a day or view change) is never
    // removed through removeCard, so it has to notice being detached by itself. Cards
    // are first painted before insertion, which is not a removal.
    if (!el.isConnected) { if (attached) el.releaseVideo(); return; }
    attached = true;
    const wanted = visible && !pausedByUser && !suspended && !document.hidden && !reducedMotion.matches;
    if (wanted) play(); else if (playing) stop();
  }
  video.addEventListener('playing', () => {
    if (!playing) return;
    video.hidden = false;
    el.classList.remove('video-loading');
    el.classList.add('video-playing');
  });
  video.addEventListener('error', () => {
    if (!video.getAttribute('src')) return;
    frameOnly = false;
    stop();
  });
  // An image that fails to load is not retried with the original: an <img> cannot show
  // an MP4, so a video card falls back to its first frame instead.
  main.addEventListener('error', () => {
    if (!source || frameOnly || !el.dataset.imagesActive) return;
    frameOnly = true;
    if (!playing) showFirstFrame();
  });
  toggle.addEventListener('click', event => {
    event.stopPropagation();
    if (playing) { pausedByUser = true; stop(); }
    // Pressing play is an explicit request, so it overrides reduced motion.
    else { pausedByUser = false; play(); }
  });
  el.setVideoVisible = value => {
    clearTimeout(settleTimer);
    if (value) { settleTimer = setTimeout(() => { visible = true; update(); }, SETTLE_MS); return; }
    visible = false;
    // Pausing lasts while the card stays on screen; scrolling back resumes it.
    pausedByUser = false;
    update();
  };
  el.resumeVideo = update;
  el.stopVideo = stop;
  el.releaseVideo = () => { clearTimeout(settleTimer); stop(); visibility.unobserve(el); cards.delete(el); };
  cards.add(el);
  visibility.observe(el);
  label();

  // Called on every paint with the item the card now shows.
  return function showItem(item) {
    const next = item?.type === 'video' && item.videoUrl ? item.videoUrl : '';
    if (next !== source) { frameOnly = false; pausedByUser = false; stop(); source = next; }
    toggle.hidden = !source;
    el.classList.toggle('is-video', !!source);
    update();
  };
}

// The details dialog plays the larger file with controls, starting muted.
export function showDetailVideo(artwork, url) {
  let video = document.getElementById('detailVideo');
  if (!video) {
    video = document.createElement('video');
    video.id = 'detailVideo';
    video.controls = true; video.loop = true; video.playsInline = true; video.muted = true;
    artwork.after(video);
  }
  if (!url) { clearDetailVideo(); return; }
  video.poster = artwork.classList.contains('image-error') ? '' : (artwork.currentSrc || artwork.src || '');
  if (video.getAttribute('src') !== url) video.src = url;
  video.hidden = false;
  artwork.hidden = true;
  video.play().catch(() => {});
}

export function clearDetailVideo() {
  const video = document.getElementById('detailVideo');
  const artwork = document.getElementById('detailImage');
  if (artwork) artwork.hidden = false;
  if (!video) return;
  video.pause();
  video.removeAttribute('src');
  video.load();
  video.hidden = true;
}
