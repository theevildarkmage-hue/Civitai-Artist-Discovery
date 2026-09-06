const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
  'September', 'October', 'November', 'December'];

function localDate(value) {
  const [year, month, day] = value.split('-').map(Number);
  return new Date(year, month - 1, day);
}
function key(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

export function mountCalendar({ api, state, select, rebuild }) {
  const toolbar = document.querySelector('.segment-toolbar');
  const button = document.createElement('button');
  button.id = 'calendarToggle';
  button.type = 'button';
  button.className = 'filter-button calendar-toggle';
  button.setAttribute('aria-expanded', 'false');
  button.setAttribute('aria-controls', 'calendarPanel');
  const panel = document.createElement('section');
  panel.id = 'calendarPanel';
  panel.className = 'calendar-panel hidden';
  panel.setAttribute('aria-label', 'Choose gallery date');
  panel.innerHTML = `<div class="filter-head"><strong class="calendar-title"></strong>
    <span class="calendar-nav"><button type="button" data-month="-1" aria-label="Previous month">‹</button>
    <button type="button" data-month="1" aria-label="Next month">›</button>
    <button type="button" class="quiet-button" aria-label="Close calendar">×</button></span></div>
    <div class="calendar-week" aria-hidden="true"><span>Mon</span><span>Tue</span><span>Wed</span><span>Thu</span><span>Fri</span><span>Sat</span><span>Sun</span></div>
    <div class="calendar-grid"></div>
    <div class="calendar-legend"><span><i class="saved"></i> Saved</span><span><i class="partial"></i> Partial</span></div>
    <div class="calendar-window" role="group" aria-label="Gallery window">
      <button type="button" data-segment="all">All day</button><button type="button" data-segment="morning">Morning</button><button type="button" data-segment="evening">Evening</button>
    </div><div class="calendar-actions"><button type="button" class="latest-saved">Latest saved</button><button type="button" class="rebuild-calendar">Rebuild selected</button></div>`;
  toolbar.prepend(button);
  document.body.append(panel);
  let coverage = new Map(), shown = null, opener = button;
  const paintButton = () => {
    const current = state();
    const label = current.date ? localDate(current.date).toLocaleDateString(undefined,
      {month: 'short', day: 'numeric'}) : 'Choose date';
    const windowName = {all: 'All day', morning: 'Morning', evening: 'Evening'}[current.segment];
    button.textContent = `${label} · ${windowName}`;
  };
  const close = (focus = false) => {
    panel.classList.add('hidden'); button.setAttribute('aria-expanded', 'false');
    if (focus) opener.focus();
  };
  const render = () => {
    const current = state(), newest = localDate(current.newest);
    shown ||= localDate(current.date || current.newest);
    panel.querySelector('.calendar-title').textContent = `${MONTHS[shown.getMonth()]} ${shown.getFullYear()}`;
    const grid = panel.querySelector('.calendar-grid');
    grid.replaceChildren();
    const first = new Date(shown.getFullYear(), shown.getMonth(), 1);
    const offset = (first.getDay() + 6) % 7;
    const start = new Date(first); start.setDate(1 - offset);
    for (let index = 0; index < 42; index++) {
      const date = new Date(start); date.setDate(start.getDate() + index);
      const value = key(date), saved = coverage.get(value);
      const cell = document.createElement('button');
      cell.type = 'button'; cell.textContent = date.getDate(); cell.dataset.date = value;
      cell.className = date.getMonth() === shown.getMonth() ? '' : 'outside';
      if (value === current.date) { cell.classList.add('selected'); cell.setAttribute('aria-current', 'date'); }
      if (saved?.all || (saved?.morning && saved?.evening)) cell.classList.add('saved');
      else if (saved?.morning || saved?.evening) cell.classList.add('partial');
      cell.disabled = date > newest;
      cell.setAttribute('aria-label', date.toLocaleDateString(undefined,
        {weekday: 'long', month: 'long', day: 'numeric', year: 'numeric'}));
      cell.onclick = async () => { close(); await select(value); };
      grid.append(cell);
    }
    panel.querySelectorAll('[data-segment]').forEach(choice => {
      choice.classList.toggle('selected', choice.dataset.segment === current.segment);
      choice.setAttribute('aria-pressed', String(choice.dataset.segment === current.segment));
    });
    panel.querySelector('[data-month="1"]').disabled = shown.getFullYear() === newest.getFullYear() && shown.getMonth() >= newest.getMonth();
    panel.querySelector('.rebuild-calendar').hidden = !current.built;
    paintButton();
  };
  const open = async () => {
    opener = button; shown = localDate(state().date || state().newest);
    panel.classList.remove('hidden'); button.setAttribute('aria-expanded', 'true');
    render();
    try {
      const data = await api('/api/history/calendar');
      coverage = new Map((data.days || []).map(day => [day.date, day])); render();
    } catch (_) { /* Date selection remains usable when coverage hints fail. */ }
  };
  button.onclick = () => panel.classList.contains('hidden') ? open() : close();
  button.onkeydown = event => {
    if (event.key === 'ArrowDown') { event.preventDefault(); open().then(() => panel.querySelector('[aria-current="date"]')?.focus()); }
    if (event.key === 'Escape') { event.preventDefault(); close(true); }
  };
  panel.querySelectorAll('[data-month]').forEach(nav => nav.onclick = () => {
    shown = new Date(shown.getFullYear(), shown.getMonth() + Number(nav.dataset.month), 1); render();
  });
  panel.querySelector('.quiet-button').onclick = () => close(true);
  panel.querySelectorAll('[data-segment]').forEach(choice => choice.onclick = async () => {
    close(); await select(state().date, choice.dataset.segment);
  });
  panel.querySelector('.latest-saved').onclick = async () => {
    const saved = [...coverage.values()].filter(day => day.all || day.morning || day.evening).at(-1);
    close(); await select(saved?.date || state().newest, saved?.all ? 'all' : saved?.evening ? 'evening' : 'morning');
  };
  panel.querySelector('.rebuild-calendar').onclick = () => { close(); rebuild(); };
  panel.onkeydown = event => { if (event.key === 'Escape') { event.preventDefault(); close(true); } };
  document.addEventListener('click', event => {
    if (!panel.contains(event.target) && !button.contains(event.target)) close();
  });
  return { update: paintButton };
}
