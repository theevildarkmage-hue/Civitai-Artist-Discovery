// Common filter surface. The page owns filter state and queries; this component owns
// presentation, active chips, keyboard dismissal, and the reset/remove interactions.
import { anchorPopover } from './menu.js';

export function mountFilters(actions) {
  const toolbar = document.querySelector('.segment-toolbar');
  const content = document.getElementById('contentFilter');
  const model = document.getElementById('modelFilter');
  const modelMenu = document.getElementById('modelMenu');
  const button = document.createElement('button');
  button.id = 'filterToggle';
  button.className = 'filter-button';
  button.type = 'button';
  button.setAttribute('aria-expanded', 'false');
  button.setAttribute('aria-controls', 'filterPanel');
  content.before(button);
  const chips = document.createElement('div');
  chips.className = 'active-filters';
  chips.setAttribute('aria-label', 'Active filters');
  chips.append(content);
  toolbar.append(chips);
  const restrictions = document.createElement('span');
  restrictions.id = 'sharedControlsStatus'; restrictions.className = 'filter-button';
  restrictions.setAttribute('role', 'status'); restrictions.hidden = true; chips.append(restrictions);
  window.addEventListener('shared-controls-summary', ({detail}) => {
    const parts = [[detail.hiddenCreators,'hidden creators'],[detail.blockedUsers,'blocked users'],
      [detail.hiddenTags,'hidden tags'],[detail.hiddenArtworks,'hidden artworks']].filter(([n]) => n > 0);
    restrictions.textContent = parts.map(([n,label]) => `${n} ${label}`).join(' ? ');
    restrictions.hidden = !parts.length;
    restrictions.title = 'Shared Civitai content controls apply alongside the rating selection.';
  });
  const panel = document.createElement('section');
  panel.id = 'filterPanel';
  panel.className = 'filter-panel hidden';
  panel.setAttribute('aria-label', 'Gallery filters');
  panel.innerHTML = '<div class="filter-head"><h2>Filters</h2><button type="button" class="quiet-button" aria-label="Close filters">×</button></div><div class="filter-sections"></div><button type="button" class="filter-reset">Reset filters</button>';
  panel.querySelector('.filter-sections').append(document.getElementById('contentMenu'), modelMenu);
  const media = document.createElement('label');
  media.className = 'preference-field media-filter';
  media.innerHTML = '<span>Media type</span><select id="mediaType" aria-label="Media type"><option value="all">All</option><option value="image">Images only</option><option value="video">Videos only</option></select>';
  panel.querySelector('.filter-sections').prepend(media);
  const mediaSelect = media.querySelector('select');
  mediaSelect.onchange = () => actions.setMedia(mediaSelect.value);
  // Keep the legacy label as a state notification point until page state is extracted.
  model.classList.add('hidden');
  panel.append(model);
  const search = document.createElement('input');
  search.type = 'search'; search.className = 'model-search';
  search.placeholder = 'Search models'; search.setAttribute('aria-label', 'Search generation models');
  const more = document.createElement('button');
  more.type = 'button'; more.className = 'model-more';
  let showAllModels = false;
  modelMenu.before(search); modelMenu.after(more);
  const applyModelSearch = () => {
    const rows = [...modelMenu.querySelectorAll('.filter-row')];
    const query = search.value.trim().toLocaleLowerCase();
    rows.forEach((row, index) => {
      const match = !query || row.textContent.toLocaleLowerCase().includes(query);
      row.classList.toggle('search-hidden', !match || (!query && !showAllModels && index >= 8));
    });
    more.hidden = !!query || rows.length <= 8;
    more.textContent = showAllModels ? 'Show fewer models' : 'Show all ' + rows.length + ' models';
  };
  window.applyGalleryModelSearch = applyModelSearch;
  search.addEventListener('input', applyModelSearch);
  more.onclick = () => { showAllModels = !showAllModels; applyModelSearch(); };
  new MutationObserver(() => requestAnimationFrame(applyModelSearch)).observe(panel, {
    childList: true, subtree: true,
  });
  document.body.append(panel);
  const compact = document.createElement('section');
  compact.id = 'contentPanel';
  compact.className = 'content-panel hidden';
  compact.setAttribute('aria-label', 'Content ratings');
  compact.innerHTML = '<div class="filter-head"><strong>Content ratings</strong><button type="button" class="quiet-button" aria-label="Close content ratings">\u00d7</button></div><div class="rating-pills" role="group" aria-label="Browsing levels"></div>';
  const choices = [...document.querySelectorAll('#contentMenu [data-level]')].map(original => {
    const choice = document.createElement('button');
    choice.type = 'button'; choice.dataset.level = original.dataset.level;
    const label = original.textContent.replace(/^\u2713\s*/, '');
    choice.setAttribute('aria-label', label);
    choice.onclick = () => actions.toggleLevel(Number(choice.dataset.level));
    compact.querySelector('.rating-pills').append(choice);
    return { choice, label };
  });
  document.body.append(compact);
  content.setAttribute('aria-controls', compact.id);
  const position = anchorPopover(panel, () => button);
  const positionContent = anchorPopover(compact, () => content);
  const closeContent = (restoreFocus = false) => {
    compact.classList.add('hidden'); content.setAttribute('aria-expanded', 'false');
    if (restoreFocus) content.focus();
  };
  const close = (restoreFocus = false) => {
    panel.classList.add('hidden');
    button.setAttribute('aria-expanded', 'false');
    if (restoreFocus) button.focus();
  };
  const open = async () => {
    closeContent();
    panel.classList.remove('hidden');
    position();
    button.setAttribute('aria-expanded', 'true');
    search.disabled = true;
    if (!document.body.classList.contains('community-view')) await actions.refreshModels();
    search.disabled = false;
    applyModelSearch();
  };
  const openContent = () => {
    close(); compact.classList.remove('hidden');
    positionContent(); content.setAttribute('aria-expanded', 'true');
  };
  button.onclick = () => panel.classList.contains('hidden') ? open() : close();
  content.onclick = () => compact.classList.contains('hidden') ? openContent() : closeContent();
  for (const trigger of [button, content]) trigger.addEventListener('keydown', event => {
    const surface = trigger === content ? compact : panel;
    const dismiss = trigger === content ? closeContent : close;
    if (event.key === 'Escape' && !surface.classList.contains('hidden')) {
      event.preventDefault();
      dismiss(true);
      return;
    }
    if (event.key !== 'ArrowDown') return;
    event.preventDefault();
    if (trigger === content) openContent(); else open();
    surface.querySelector('.rating-pills button:not(:disabled)')?.focus();
  });
  panel.querySelector('.quiet-button').onclick = () => close(true);
  panel.addEventListener('keydown', event => {
    if (event.key === 'Escape') { event.preventDefault(); close(true); }
  });
  compact.querySelector('.quiet-button').onclick = () => closeContent(true);
  compact.addEventListener('keydown', event => {
    if (event.key === 'Escape') { event.preventDefault(); closeContent(true); }
  });
  document.addEventListener('click', event => {
    if (!panel.contains(event.target) && !button.contains(event.target)) close();
    if (!compact.contains(event.target) && !content.contains(event.target)) closeContent();
  });
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || event.defaultPrevented || event.target.closest('dialog[open]')) return;
    if (!compact.classList.contains('hidden')) { event.preventDefault(); closeContent(true); }
    else if (!panel.classList.contains('hidden')) { event.preventDefault(); close(true); }
  });
  panel.querySelector('.filter-reset').onclick = () => actions.reset();

  const paint = () => {
    applyModelSearch();
    const { models, levels, media = 'all' } = actions.state();
    for (const { choice, label } of choices) {
      const selected = levels.includes(Number(choice.dataset.level));
      choice.classList.toggle('selected', selected);
      choice.setAttribute('aria-pressed', String(selected));
      choice.textContent = `${selected ? '\u2713 ' : ''}${label}`;
      choice.disabled = content.disabled;
    }
    mediaSelect.value = media;
    const ratingChanged = levels.length !== 2 || !levels.includes(1) || !levels.includes(2);
    const count = models.length + Number(ratingChanged) + Number(media !== 'all');
    button.textContent = count ? `Filters · ${count}` : 'Filters';
    button.dataset.activeCount = String(count);
    panel.querySelector('.filter-reset').disabled = !count || content.disabled;
    chips.querySelectorAll('.model-chip').forEach(chip => chip.remove());
    if (media !== 'all') {
      const chip = document.createElement('button');
      chip.className = 'filter-button model-chip';
      chip.textContent = `${media === 'image' ? 'Images only' : 'Videos only'} ×`;
      chip.setAttribute('aria-label', 'Remove media filter');
      chip.onclick = () => actions.setMedia('all');
      chips.append(chip);
    }
    for (const name of models) {
      const chip = document.createElement('button');
      chip.className = 'filter-button model-chip';
      chip.type = 'button';
      chip.textContent = `${name} ×`;
      chip.setAttribute('aria-label', `Remove model filter ${name}`);
      chip.onclick = () => { actions.removeModel(name); button.focus(); };
      chips.append(chip);
    }
    const summary = document.getElementById('summary');
    if (summary && summary.parentElement !== chips) chips.append(summary);
  };
  const observer = new MutationObserver(paint);
  for (const element of [content, model]) observer.observe(element, {
    childList: true, subtree: true, attributes: true, attributeFilter: ['disabled'],
  });
  paint();
  return { update: paint };
}
