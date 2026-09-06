import { api } from './api.js';
const escapeHtml = value => String(value).replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));

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
  select.addEventListener('change', sync);
  sync();
}

export function mountPreferences() {
  const panel = document.getElementById('preferencesMenu');
  panel.querySelector('.filter-head strong').textContent = 'Settings';
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

  const dimRow = document.getElementById('prefDimSeen').closest('label');
  const highToggle = document.getElementById('prefHideHighVolume');
  const highSelect = document.getElementById('prefHighVolumeThreshold');
  highToggle.closest('label').classList.add('retired-preference');
  highSelect.closest('label').classList.add('retired-preference');
  for (const id of ['prefEmergingMode', 'prefEmergingLimit']) {
    document.getElementById(id).closest('label').classList.add('retired-preference');
  }
  document.getElementById('emergingModeHelp').classList.add('retired-preference');
  document.getElementById('emergingLimitHelp').classList.add('retired-preference');

  const frequent = document.createElement('label');
  frequent.className = 'preference-field frequent-posters';
  frequent.innerHTML = '<span><b>Hide frequent posters</b><small>Hide creators who posted at least this many images that day.</small></span><div class="preference-slider"><input id="highVolumeSlider" type="range" min="0" max="4" step="1"><div class="slider-labels"><span>Off</span><span>50</span><span>100</span><span>150</span><span>200/day</span></div></div>';
  dimRow.after(frequent);
  const slider = frequent.querySelector('input');
  const values = [0, 50, 100, 150, 200];
  const update = () => {
    const threshold = highToggle.checked ? Number(highSelect.value) : 0;
    slider.value = String(Math.max(0, values.indexOf(threshold)));
    slider.setAttribute('aria-valuetext', threshold ? `${threshold} images per day` : 'Off');
  };
  slider.oninput = () => {
    const threshold = values[Number(slider.value)];
    slider.setAttribute('aria-valuetext', threshold ? `${threshold} images per day` : 'Off');
  };
  slider.onchange = () => panel.dispatchEvent(new CustomEvent('high-volume-change', {
    detail: values[Number(slider.value)],
  }));

  const application = document.createElement('h3');
  application.textContent = 'Application';
  frequent.after(application);
  const profileSettings = document.getElementById('profileSettings');
  for (const preference of profileSettings.querySelectorAll('.update-preference')) {
    panel.append(preference);
  }
  panel.append(profileSettings.querySelector('.settings-reset'));
  const hiddenHeading = document.createElement('h3'); hiddenHeading.textContent = 'Hidden artists';
  const hiddenArtists = document.createElement('div'); hiddenArtists.className = 'hidden-artists-setting';
  panel.insertBefore(hiddenHeading, application);
  panel.insertBefore(hiddenArtists, application);
  async function renderHiddenArtists() {
    try {
      const data = await api('/api/hidden-creators');
      hiddenArtists.innerHTML = data.artists?.length
        ? data.artists.map(artist => `<div><span>@${escapeHtml(artist.username)}</span><button type="button" data-username="${escapeHtml(artist.username)}">Show again</button></div>`).join('')
        : '<small>No artists hidden in this app.</small>';
      hiddenArtists.querySelectorAll('button').forEach(button => button.onclick = async () => {
        button.disabled = true;
        await api('/api/hidden-creators', { method: 'POST', body: JSON.stringify({ username: button.dataset.username, hidden: false }) });
        await renderHiddenArtists();
      });
    } catch (error) { hiddenArtists.innerHTML = `<small>${error.message}</small>`; }
  }
  document.addEventListener('hidden-creators-changed', renderHiddenArtists);
  renderHiddenArtists();
  profileSettings.remove();
  document.querySelector('.capture-preference strong').textContent = 'Save recent days';
  document.getElementById('capturePreferenceStatus').textContent = '';
  document.getElementById('updateChecks').parentElement.lastChild.textContent = ' Automatic';
  document.getElementById('captureEnabled').parentElement.lastChild.textContent = ' On';
  document.querySelector('.settings-reset strong').textContent = 'Local profile data';
  update();
  return { update };
}
