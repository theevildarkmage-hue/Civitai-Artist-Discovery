function mirrorRange(select, { id, values, labels }) {
  const wrap = document.createElement('div');
  wrap.className = 'preference-slider';
  wrap.innerHTML = `<input id="${id}" type="range" min="0" max="${values.length - 1}" step="1">
    <div class="slider-labels">${labels.map(label => `<span>${label}</span>`).join('')}</div>`;
  const range = wrap.querySelector('input');
  const sync = () => {
    const index = Math.max(0, values.indexOf(select.value));
    range.value = String(index); range.disabled = select.disabled;
    range.setAttribute('aria-valuetext', labels[index]);
  };
  range.oninput = () => {
    select.value = values[Number(range.value)];
    range.setAttribute('aria-valuetext', labels[Number(range.value)]);
  };
  range.onchange = () => select.dispatchEvent(new Event('change', {bubbles: true}));
  select.classList.add('control-source');
  select.after(wrap);
  new MutationObserver(sync).observe(select, {attributes: true, attributeFilter: ['disabled']});
  select.addEventListener('change', sync);
  sync();
}

export function mountPreferences() {
  const panel = document.getElementById('preferencesMenu');
  panel.querySelector('.filter-head strong').textContent = 'Preferences';
  const appearance = document.createElement('h3'); appearance.textContent = 'Appearance';
  panel.querySelector('.filter-head').after(appearance);
  const cardField = document.querySelector('.ui-field:has(#cardSize)');
  const cardSelect = document.getElementById('cardSize');
  cardField.classList.add('card-size-source');
  const cardPreference = document.createElement('label');
  cardPreference.className = 'preference-field card-size-preference';
  cardPreference.innerHTML = '<span>Card size</span>';
  const clone = cardSelect.cloneNode(true); clone.id = 'preferenceCardSize';
  clone.onchange = () => { cardSelect.value = clone.value; cardSelect.dispatchEvent(new Event('change')); };
  cardSelect.addEventListener('change', () => { clone.value = cardSelect.value; });
  cardPreference.append(clone);
  appearance.after(cardPreference);
  mirrorRange(clone, {id: 'cardSizeSlider', values: ['0.6', '0.8', '1'], labels: ['Small', 'Medium', 'Large']});
  const discovery = document.createElement('h3'); discovery.textContent = 'Discovery';
  const emerging = document.getElementById('emergingModeChoices').closest('label');
  emerging.before(discovery);
  mirrorRange(document.getElementById('prefHighVolumeThreshold'), {
    id: 'highVolumeSlider', values: ['50', '100', '200'], labels: ['50', '100', '200 images/day'],
  });
  mirrorRange(document.getElementById('prefEmergingLimit'), {
    id: 'strictLimitSlider', values: ['0', '100', '250', '500'], labels: ['None', '100', '250', '500 reactions'],
  });
}
