// Gallery, Time Machine, and details share preview fallback and loading feedback.
// Visibility checks belong to the caller: never assign artwork before they succeed.
export function cardPreviewUrl(source, width, density = 1) {
  if (!source || !Number.isFinite(width) || width <= 0) return source;
  let url;
  try { url = new URL(source); } catch (_) { return source; }
  // Only change the CDN transformation format already produced by the archive.
  // Originals, local fixtures, data URLs, and other transformation options stay intact.
  if (url.protocol !== 'https:' || url.hostname !== 'image.civitai.com' ||
      !/\/width=\d+\/[^/]+$/.test(url.pathname)) return source;
  const required = width * Math.min(2, Math.max(1, density || 1));
  const target = [384, 512, 768, 1024, 1280].find(value => value >= required) || 1280;
  url.pathname = url.pathname.replace(/\/width=\d+\//, `/width=${target}/`);
  return url.href;
}

export function showCardArtwork(image, previewUrl, originalUrl) {
  const bounds = image.getBoundingClientRect();
  const visible = bounds.bottom > 0 && bounds.top < innerHeight;
  image.loading = visible ? 'eager' : 'lazy';
  image.fetchPriority = visible ? 'high' : 'low';
  showArtwork(image, cardPreviewUrl(previewUrl, bounds.width, devicePixelRatio), originalUrl);
}

export function wireArtworkFallback(image) {
  if (!image || image.dataset.fallbackWired) return;
  image.dataset.fallbackWired = '1';
  image.addEventListener('load', () => {
    image.classList.remove('image-error', 'image-pending');
  });
  image.addEventListener('error', () => {
    const fallback = image.dataset.fallbackUrl;
    if (fallback && image.dataset.fallbackPending === '1') {
      image.dataset.fallbackPending = '0';
      image.src = fallback;
      return;
    }
    image.classList.remove('image-pending');
    image.classList.add('image-error');
  });
}

export function showArtwork(image, previewUrl, originalUrl) {
  wireArtworkFallback(image);
  const preview = previewUrl || originalUrl || '';
  const fallback = originalUrl && originalUrl !== preview ? originalUrl : '';
  // Painting reactions or metadata must not restart the same artwork request/fallback.
  if (image.dataset.previewUrl === preview && image.getAttribute('src')) return;
  image.dataset.previewUrl = preview;
  image.classList.remove('image-error');
  image.classList.toggle('image-pending', !!preview);
  image.dataset.fallbackUrl = fallback;
  image.dataset.fallbackPending = fallback ? '1' : '0';
  image.decoding = 'async';
  if (preview) image.src = preview;
  else {
    image.removeAttribute('src');
    image.classList.add('image-error');
  }
}
