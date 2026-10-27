// Common filter surface. The page owns filter state and queries; this component owns
// presentation, active chips, keyboard dismissal, and the reset/remove interactions.
import { anchorPopover } from './menu.js';

export function mountFilters(actions) {
  const toolbar = document.querySelector('.segment-toolbar');
  const content = document.getElementById('contentMenu');
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
  toolbar.append(chips);
  let sharedControls = {};
  const paintRestrictions = () => {
    const detail = {...sharedControls, hiddenTags: actions.state().applyTagFilters === false ? 0 : sharedControls.hiddenTags};
    if (actions.state().applyAccountFilters === false) { detail.hiddenCreators = 0; detail.blockedUsers = 0; }
    if (actions.state().applyImageFilters === false) detail.hiddenArtworks = 0;
    const parts = [[detail.hiddenCreators,'hidden creators'],[detail.blockedUsers,'blocked users'],
      [detail.hiddenTags,'hidden tags'],[detail.hiddenArtworks,'hidden artworks']].filter(([n]) => n > 0);
    restrictions.textContent = parts.map(([n,label]) => `${n} ${label}`).join(' · ');
    restrictions.hidden = !parts.length;
    restrictions.title = 'Active Civitai content controls apply alongside the rating selection.';
  };
  const restrictions = document.createElement('span');
  restrictions.id = 'sharedControlsStatus'; restrictions.className = 'filter-button';
  restrictions.setAttribute('role', 'status'); restrictions.hidden = true; chips.append(restrictions);
  window.addEventListener('shared-controls-summary', ({detail}) => {
    sharedControls = detail;
    paintRestrictions();
  });
  const panel = document.createElement('section');
  panel.id = 'filterPanel';
  panel.className = 'filter-panel hidden';
  panel.setAttribute('aria-label', 'Gallery filters');
  panel.innerHTML = '<div class="filter-head"><h2>Filters</h2><button type="button" class="quiet-button" aria-label="Close filters">×</button></div><div class="filter-sections"></div><button type="button" class="filter-reset">Reset filters</button>';
  panel.querySelector('.filter-sections').append(document.getElementById('contentMenu'), modelMenu);
  const tagFilter = document.createElement('label');
  tagFilter.className = 'filter-row tag-filter-toggle';
  tagFilter.innerHTML = '<input id="applyTagFilters" type="checkbox" checked><span>Apply my tag filters</span>';
  content.append(tagFilter);
  const tagCheckbox = tagFilter.querySelector('input');
  tagCheckbox.onchange = () => actions.setTagFilters(tagCheckbox.checked);
  const accountFilter = document.createElement('label');
  accountFilter.className = 'filter-row account-filter-toggle';
  accountFilter.innerHTML = '<input id="applyAccountFilters" type="checkbox" checked><span>Exclude hidden and blocked accounts</span>';
  content.append(accountFilter);
  const accountCheckbox = accountFilter.querySelector('input');
  accountCheckbox.onchange = () => actions.setAccountFilters(accountCheckbox.checked);
  const imageFilter = document.createElement('label');
  imageFilter.className = 'filter-row image-filter-toggle';
  imageFilter.innerHTML = '<input id="applyImageFilters" type="checkbox" checked><span>Exclude hidden artworks<small>Includes images hidden after reporting them.</small></span>';
  content.append(imageFilter);
  const imageCheckbox = imageFilter.querySelector('input');
  imageCheckbox.onchange = () => actions.setImageFilters(imageCheckbox.checked);
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
  const position = anchorPopover(panel, () => button);
  const close = (restoreFocus = false) => {
    panel.classList.add('hidden');
    button.setAttribute('aria-expanded', 'false');
    if (restoreFocus) button.focus();
  };
  const open = async () => {
    panel.classList.remove('hidden');
    position();
    button.setAttribute('aria-expanded', 'true');
    search.disabled = true;
    await actions.refreshModels();
    search.disabled = false;
    applyModelSearch();
  };
  button.onclick = () => panel.classList.contains('hidden') ? open() : close();
  button.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !panel.classList.contains('hidden')) {
      event.preventDefault();close(true);return;
    }
    if (event.key !== 'ArrowDown') return;
    event.preventDefault();open();
    panel.querySelector('.rating-pills button:not(:disabled)')?.focus();
  });
  panel.querySelector('.quiet-button').onclick = () => close(true);
  panel.addEventListener('keydown', event => {
    if (event.key === 'Escape') { event.preventDefault(); close(true); }
  });
  document.addEventListener('click', event => {
    if (!panel.contains(event.target) && !button.contains(event.target)) close();
  });
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || event.defaultPrevented || event.target.closest('dialog[open]')) return;
    if (!panel.classList.contains('hidden')) { event.preventDefault(); close(true); }
  });
  panel.querySelector('.filter-reset').onclick = () => actions.reset();

  const paint = () => {
    applyModelSearch();
    const { models, levels, media = 'all', applyTagFilters = true, applyAccountFilters = true, applyImageFilters = true, controlFiltersSaving = false } = actions.state();
    tagCheckbox.checked = applyTagFilters;
    if (tagCheckbox.disabled !== controlFiltersSaving) tagCheckbox.disabled = controlFiltersSaving;
    accountCheckbox.checked = applyAccountFilters;
    if (accountCheckbox.disabled !== controlFiltersSaving) accountCheckbox.disabled = controlFiltersSaving;
    imageCheckbox.checked = applyImageFilters;
    if (imageCheckbox.disabled !== controlFiltersSaving) imageCheckbox.disabled = controlFiltersSaving;
    paintRestrictions();
    mediaSelect.value = media;
    const ratingChanged = levels.length !== 2 || !levels.includes(1) || !levels.includes(2);
    const count = models.length + Number(ratingChanged) + Number(media !== 'all') + Number(!applyTagFilters) + Number(!applyAccountFilters) + Number(!applyImageFilters);
    button.textContent = count ? `Filters · ${count}` : 'Filters';
    button.dataset.activeCount = String(count);
    panel.querySelector('.filter-reset').disabled = !count;
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
