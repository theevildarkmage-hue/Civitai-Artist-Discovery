// Placeholder geometry reserves the same column layout as cards without starting any
// artwork requests. The returned cleanup owns only the nodes from this particular load.
export function showGallerySkeleton(gallery) {
  const columns = getComputedStyle(gallery).gridTemplateColumns.split(' ').length || 1;
  const placeholders = Array.from({length: Math.min(12, columns * 2)}, () => {
    const element = document.createElement('div');
    element.className = 'gallery-skeleton';
    element.setAttribute('aria-hidden', 'true');
    element.innerHTML = '<div class="skeleton-identity"><i></i><span></span></div><div class="skeleton-artwork"></div><div class="skeleton-footer"></div>';
    gallery.insertBefore(element, gallery.querySelector('#loadSentinel'));
    return element;
  });
  return () => placeholders.forEach(element => element.remove());
}

export function showPageError(container, message, retry) {
  container.replaceChildren();
  container.setAttribute('role', 'alert');
  const text = document.createElement('span');
  text.textContent = `${message} `;
  const button = document.createElement('button');
  button.type = 'button';
  button.textContent = 'Try again';
  button.onclick = async () => {
    button.disabled = true;
    try { await retry(); }
    catch (error) { showPageError(container, error.message, retry); }
    finally { button.disabled = false; }
  };
  container.append(text, button);
}
