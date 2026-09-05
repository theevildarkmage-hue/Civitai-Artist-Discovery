// Gallery, Time Machine, and details share preview fallback and loading feedback.
// Visibility checks belong to the caller: never assign artwork before they succeed.
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
