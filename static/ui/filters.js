// Common filter surface. The page owns filter state and queries; this component owns
// presentation, active chips, keyboard dismissal, and the reset/remove interactions.
export function mountFilters(actions) {
  const toolbar = document.querySelector('.segment-toolbar');
  const content = document.getElementById('contentFilter');
  const model = document.getElementById('modelFilter');
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
  const panel = document.createElement('section');
  panel.id = 'filterPanel';
  panel.className = 'filter-panel hidden';
  panel.setAttribute('aria-label', 'Gallery filters');
  panel.innerHTML = '<div class="filter-head"><h2>Filters</h2><button type="button" class="quiet-button" aria-label="Close filters">×</button></div><div class="filter-sections"></div><button type="button" class="filter-reset">Reset filters</button>';
  panel.querySelector('.filter-sections').append(document.getElementById('contentMenu'), document.getElementById('modelMenu'));
  // Keep the legacy label as a state notification point until page state is extracted.
  model.classList.add('hidden');
  panel.append(model);
  document.body.append(panel);
  let opener = button;
  const close = (restoreFocus = false) => {
    panel.classList.add('hidden');
    button.setAttribute('aria-expanded', 'false');
    content.setAttribute('aria-expanded', 'false');
    if (restoreFocus) opener.focus();
  };
  const open = (trigger = button) => {
    opener = trigger;
    panel.style.setProperty('--filter-top', `${Math.ceil(button.getBoundingClientRect().bottom + 8)}px`);
    panel.classList.remove('hidden');
    button.setAttribute('aria-expanded', 'true');
    content.setAttribute('aria-expanded', 'true');
    actions.refreshModels();
  };
  button.onclick = () => panel.classList.contains('hidden') ? open() : close();
  window.addEventListener('resize', () => {
    if (!panel.classList.contains('hidden')) panel.style.setProperty('--filter-top', `${Math.ceil(button.getBoundingClientRect().bottom + 8)}px`);
  });
  content.onclick = () => open(content);
  content.setAttribute('aria-controls', 'filterPanel');
  for (const trigger of [button, content]) trigger.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !panel.classList.contains('hidden')) {
      event.preventDefault();
      close(true);
      return;
    }
    if (event.key !== 'ArrowDown') return;
    event.preventDefault();
    open(trigger);
    panel.querySelector('.rating-pills button:not(:disabled)')?.focus();
  });
  panel.querySelector('.quiet-button').onclick = () => close(true);
  panel.addEventListener('keydown', event => {
    if (event.key === 'Escape') { event.preventDefault(); close(true); }
  });
  document.addEventListener('click', event => {
    if (!panel.contains(event.target) && !chips.contains(event.target) && !button.contains(event.target)) close();
  });
  panel.querySelector('.filter-reset').onclick = () => actions.reset();

  const paint = () => {
    const { models, levels } = actions.state();
    const ratingChanged = levels.length !== 2 || !levels.includes(1) || !levels.includes(2);
    const count = models.length + Number(ratingChanged);
    button.textContent = count ? `Filters · ${count}` : 'Filters';
    button.dataset.activeCount = String(count);
    panel.querySelector('.filter-reset').disabled = !count || content.disabled;
    chips.querySelectorAll('.model-chip').forEach(chip => chip.remove());
    for (const name of models) {
      const chip = document.createElement('button');
      chip.className = 'filter-button model-chip';
      chip.type = 'button';
      chip.textContent = `${name} ×`;
      chip.setAttribute('aria-label', `Remove model filter ${name}`);
      chip.onclick = () => { actions.removeModel(name); button.focus(); };
      chips.append(chip);
    }
  };
  const observer = new MutationObserver(paint);
  for (const element of [content, model]) observer.observe(element, {
    childList: true, subtree: true, attributes: true, attributeFilter: ['disabled'],
  });
  paint();
}
