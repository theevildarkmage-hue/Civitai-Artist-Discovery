import {api} from './api.js';
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
  const network = document.createElement('section');
  network.className='update-preference';network.id='lanPreference';
  network.innerHTML='<div><strong>Local network access</strong><small>Use only on a trusted private network. Paired devices use this computer’s signed-in account. Keep the link private.</small><small id="lanStatus" role="status"></small><div id="lanLinks"></div></div><label><input id="lanEnabled" type="checkbox"> Allow LAN access</label><button id="lanRestart" type="button" hidden>Restart to apply</button>';
  panel.append(network);
  const lanEnabled=network.querySelector('#lanEnabled'),lanStatus=network.querySelector('#lanStatus');
  const links=network.querySelector('#lanLinks'),restart=network.querySelector('#lanRestart');
  async function loadNetwork() {
    try {
      const status=await api('/api/lan/status');
      lanEnabled.checked=status.enabled;links.replaceChildren();
      restart.hidden=status.enabled===status.listening;restart.disabled=!!status.busyReason;
      lanStatus.textContent=restart.hidden?(status.enabled?'Open a private link on your other device. Links expire when the app restarts.':'Off. Only this computer can connect.'):
        (status.enabled?'Restart the app to allow network connections.':'Network access is blocked. Restart to close the network listener.');
      if(!restart.hidden&&status.busyReason)lanStatus.textContent+=' '+status.busyReason;
      if(status.enabled&&status.listening)for(const url of status.links) {
        const link=document.createElement('a');link.href=url;link.textContent=url;link.rel='noreferrer';link.style.overflowWrap='anywhere';links.append(link,document.createElement('br'));
      }
      if(status.enabled&&status.listening&&!status.links.length)lanStatus.textContent='No private IPv4 network address found. Connect this computer to your local network.';
    } catch (_) {lanEnabled.disabled=true;restart.hidden=true;lanStatus.textContent='Manage network sharing on the computer running Discovery.';}
  }
  lanEnabled.onchange=async()=>{
    lanEnabled.disabled=true;
    try {await api('/api/settings',{method:'POST',body:JSON.stringify({allowLanAccess:lanEnabled.checked})});await loadNetwork();}
    catch(error){lanEnabled.checked=!lanEnabled.checked;lanStatus.textContent=error.message;}
    finally{lanEnabled.disabled=false;}
  };
  restart.onclick=async()=>{
    try {
      restart.disabled=true;lanStatus.textContent='Restarting...';
      await api('/api/app/restart',{method:'POST',body:'{}'});
      for(let attempt=0;attempt<30;attempt++) {
        await new Promise(resolve=>setTimeout(resolve,1000));
        try {const response=await fetch('/api/history/config',{cache:'no-store'});if(response.ok){location.reload();return;}}catch(_){}
      }
      throw new Error('Restart is taking longer than expected. Reopen Discovery on this computer.');
    }
    catch(error){lanStatus.textContent=error.message;}
  };
  document.getElementById('galleryPreferences').addEventListener('click',loadNetwork);
  loadNetwork();
  panel.append(profileSettings.querySelector('.settings-reset'));
  profileSettings.remove();
  document.querySelector('.capture-preference strong').textContent = 'Save recent days';
  document.getElementById('capturePreferenceStatus').textContent = '';
  document.getElementById('updateChecks').parentElement.lastChild.textContent = ' Automatic';
  document.getElementById('captureEnabled').parentElement.lastChild.textContent = ' On';
  document.querySelector('.settings-reset strong').textContent = 'Local profile data';
  const reset = document.getElementById('resetDiscovery');
  reset.textContent = 'Delete local data';
  reset.classList.remove('hidden');
  update();
  return { update };
}
