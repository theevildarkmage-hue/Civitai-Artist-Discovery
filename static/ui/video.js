// Video artwork. A video card shows its still frame exactly like an image card; the
// playback file is fetched only while someone is actually watching that one card, and
// it is released again as soon as they stop. Only one card plays at a time.
const HOVER_DELAY_MS = 250;
let playingCard = null;
// A card that scrolls away while playing (touch has no pointerleave) stops by itself.
const offscreen = new IntersectionObserver(entries => {
  entries.forEach(entry => { if (!entry.isIntersecting) entry.target.stopVideo?.(); });
});

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
  let source = '', hoverTimer = 0, frameOnly = false;

  function label(playing) {
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
    clearTimeout(hoverTimer);
    if (playingCard === el) playingCard = null;
    offscreen.unobserve(el);
    el.classList.remove('video-playing', 'video-loading');
    label(false);
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
  function play() {
    // Artwork is attached only after the tag check clears it, and never mid-navigation.
    if (!source || !el.dataset.imagesActive || el.getAttribute('aria-busy') === 'true') return;
    if (playingCard && playingCard !== el) playingCard.stopVideo?.();
    playingCard = el;
    el.classList.add('video-loading');
    if (!frameOnly) video.poster = main.currentSrc || main.src || '';
    video.preload = 'auto';
    video.src = source;
    video.play().catch(() => { if (playingCard === el) stop(); });
    offscreen.observe(el);
    label(true);
  }
  video.addEventListener('playing', () => {
    if (playingCard !== el) return;
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
    if (playingCard !== el) showFirstFrame();
  });
  toggle.addEventListener('click', event => {
    event.stopPropagation();
    if (playingCard === el) stop(); else play();
  });
  const stage = el.querySelector('.image-stage');
  // A short dwell keeps a pointer sweeping across the grid from starting downloads.
  stage.addEventListener('pointerenter', event => {
    if (event.pointerType !== 'mouse' || !source) return;
    clearTimeout(hoverTimer);
    hoverTimer = setTimeout(play, HOVER_DELAY_MS);
  });
  stage.addEventListener('pointerleave', event => {
    if (event.pointerType === 'mouse') stop();
  });
  el.stopVideo = stop;
  label(false);

  // Called on every paint with the item the card now shows.
  return function showItem(item) {
    const next = item?.type === 'video' && item.videoUrl ? item.videoUrl : '';
    if (next !== source) { frameOnly = false; stop(); source = next; }
    toggle.hidden = !source;
    el.classList.toggle('is-video', !!source);
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
