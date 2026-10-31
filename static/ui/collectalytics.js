import { api } from './api.js';

// Collectalytics: statistics for one collection the account owns or follows, a creator
// check for "what have I added from them lately", and a Civitai-ready stats snippet.
// The server hands over every accepted item; the counting is done here so days follow
// the reader's own time zone.
// The last opened collection and the day/week choice, kept across restarts.
const STATE_KEY = 'civitaiCollectalytics';
const SNIPPET_KEY = 'civitaiCollectalyticsSnippet';
const TOP_CREATORS = 20;
const RECENT = 15;
const CREATOR_THUMBS = 36;
const DAY_MS = 86400000;
const SVG = 'http://www.w3.org/2000/svg';
const DEFAULT_LINES = '{creators} unique creators 🎶\n{items} {noun} collected\n{followers} followers';

const $ = id => document.getElementById(id);

function readJSON(storage, key) {
  try { return JSON.parse(storage.getItem(key) || 'null') || {}; } catch (_) { return {}; }
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function svg(tag, attributes = {}, text) {
  const node = document.createElementNS(SVG, tag);
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
  if (text !== undefined) node.textContent = text;
  return node;
}

const number = value => Number(value || 0).toLocaleString();

function dayKey(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

function startOfDay(date) { return new Date(date.getFullYear(), date.getMonth(), date.getDate()); }

function startOfWeek(date) {
  const day = startOfDay(date);
  day.setDate(day.getDate() - (day.getDay() + 6) % 7);
  return day;
}

function formatDay(date, withYear = false) {
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', ...(withYear ? { year: 'numeric' } : {}) });
}

function formatStamp(date) {
  return date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

const relative = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });
function ago(date, now = Date.now()) {
  const seconds = (date.getTime() - now) / 1000;
  for (const [unit, size] of [['year', 31536000], ['month', 2592000], ['week', 604800], ['day', 86400], ['hour', 3600], ['minute', 60]]) {
    if (Math.abs(seconds) >= size) return relative.format(Math.round(seconds / size), unit);
  }
  return 'just now';
}

// The gap between two additions, in words: "12 days", "3 hours".
function span(ms) {
  const hours = ms / 3600000;
  if (hours < 1) return 'under an hour';
  if (hours < 48) return `${Math.round(hours)} hour${Math.round(hours) === 1 ? '' : 's'}`;
  const days = Math.round(hours / 24);
  return `${days} days`;
}

function escapeHTML(value) {
  return String(value).replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]);
}

// The collection's name is its brand, so it is linked exactly as written:
// "Music Focus 🎸" becomes "The [Music Focus 🎸] collection".
export function collectionTitle(name) {
  const clean = String(name || '').trim() || 'Untitled';
  return /\bcollection\b/i.test(clean) ? `The [${clean}]` : `The [${clean}] collection`;
}

// "2h 48m 20s": every unit from the largest one present down to seconds.
export function formatDuration(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  const hours = Math.floor(total / 3600), minutes = Math.floor(total % 3600 / 60), rest = total % 60;
  if (hours) return `${hours}h ${minutes}m ${rest}s`;
  return minutes ? `${minutes}m ${rest}s` : `${rest}s`;
}

// The summed length of the videos, and how many have no length on Civitai.
export function videoLength(items) {
  const videos = items.filter(item => item.mediaType === 'video');
  const timed = videos.filter(item => Number(item.duration) > 0);
  return { videos: videos.length, missing: videos.length - timed.length,
    seconds: timed.reduce((sum, item) => sum + Number(item.duration), 0) };
}

// What a collection is "of", for "177 videos collected".
export function itemNoun(items) {
  const kinds = new Set(items.map(item => item.kind === 'image' ? item.mediaType : item.kind));
  const plural = items.length !== 1;
  const word = kinds.size !== 1 ? 'item' : ({ video: 'video', image: 'image', model: 'model', post: 'post', article: 'article' })[[...kinds][0]] || 'item';
  return plural ? `${word}s` : word;
}

// The analysis of one collection's items, everything the panels draw from.
export function analyse(items) {
  const dated = [];
  const creators = new Map();
  for (const item of items) {
    const added = item.addedAt ? new Date(item.addedAt) : null;
    const entry = { ...item, added: added && !Number.isNaN(added.getTime()) ? added : null };
    const key = entry.creatorId != null ? `id:${entry.creatorId}` : `name:${(entry.creator || '').toLowerCase()}`;
    entry.creatorKey = key;
    if (!creators.has(key)) creators.set(key, { key, name: entry.creator || 'Unknown creator', profileUrl: entry.profileUrl, items: [] });
    creators.get(key).items.push(entry);
    if (entry.added) dated.push(entry);
  }
  dated.sort((a, b) => b.added - a.added || b.collectionItemId - a.collectionItemId);
  const byAdded = (a, b) => (b.added?.getTime() ?? -Infinity) - (a.added?.getTime() ?? -Infinity) || b.collectionItemId - a.collectionItemId;
  for (const creator of creators.values()) {
    creator.items.sort(byAdded);
    creator.last = creator.items.find(item => item.added)?.added || null;
  }
  // Each item's previous addition from the same creator, for the "Recently added" list.
  const previous = new Map();
  for (const creator of creators.values()) {
    const datedItems = creator.items.filter(item => item.added);
    datedItems.forEach((item, index) => previous.set(item.collectionItemId, datedItems[index + 1] || null));
  }
  const ranked = [...creators.values()].sort((a, b) => b.items.length - a.items.length || a.name.localeCompare(b.name));
  return { items, dated, creators, ranked, previous, first: dated.at(-1)?.added || null, last: dated[0]?.added || null };
}

// Additions per day or per week, with every empty bucket in between.
export function buckets(dated, unit) {
  if (!dated.length) return [];
  const start = unit === 'week' ? startOfWeek : startOfDay;
  const counts = new Map();
  for (const item of dated) {
    const key = dayKey(start(item.added));
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  const result = [];
  const end = start(dated[0].added);
  for (let cursor = start(dated.at(-1).added); cursor <= end;) {
    const key = dayKey(cursor);
    result.push({ key, date: new Date(cursor), count: counts.get(key) || 0 });
    cursor = new Date(cursor.getFullYear(), cursor.getMonth(), cursor.getDate() + (unit === 'week' ? 7 : 1));
  }
  return result;
}

// The snippet as Civitai-safe HTML and as plain text. Only tags that survive the
// article editor, model descriptions and post descriptions are produced: a heading
// (or a bold line), one paragraph with line breaks, <strong> and a plain link.
export function snippet({ title, lines, heading }, values, url) {
  // Typed text is escaped piece by piece; only the placeholders gain markup.
  const fill = (text, html) => text.split(/(\{\w+\})/).map(part => {
    const name = part.match(/^\{(\w+)\}$/)?.[1];
    if (!name || !(name in values)) return html ? escapeHTML(part) : part;
    const value = String(values[name]);
    if (!html) return value;
    return name === 'noun' ? escapeHTML(value) : `<strong>${escapeHTML(value)}</strong>`;
  }).join('');
  const titleHTML = (() => {
    const match = title.match(/^(.*?)\[(.+?)\](.*)$/s);
    if (!match) return escapeHTML(fill(title, false));
    const [, before, link, after] = match;
    return `${escapeHTML(fill(before, false))}<a href="${escapeHTML(url)}">${escapeHTML(fill(link, false))}</a>${escapeHTML(fill(after, false))}`;
  })();
  const titleText = fill(title.replace(/\[(.+?)\]/s, '$1'), false);
  const body = lines.split('\n').map(line => line.trim()).filter(Boolean);
  const head = heading === 'p' ? `<p><strong>${titleHTML}</strong></p>` : `<${heading}>${titleHTML}</${heading}>`;
  const html = body.length ? `${head}<p>${body.map(line => fill(line, true)).join('<br>')}</p>` : head;
  const text = [titleText, ...body.map(line => fill(line, false))].join('\n');
  return { html, text };
}

export function mountCollectalytics() {
  const saved = readJSON(localStorage, STATE_KEY);
  const state = {
    collections: null, selected: saved.collectionId ?? null, report: null, analysis: null,
    bucket: saved.bucket === 'week' ? 'week' : 'day', allCreators: false, creatorKey: null,
    day: null, loading: 0, listing: null, active: false, account: undefined,
    // Bumped when the account changes, so answers for the previous one are dropped.
    epoch: 0, stale: false,
  };
  const section = $('collectalytics');
  if (!section) return { setActive() {} };

  function save() {
    localStorage.setItem(STATE_KEY, JSON.stringify({ collectionId: state.selected, bucket: state.bucket }));
  }

  function message(text, error = false) {
    $('caMessage').textContent = text;
    $('caMessage').classList.toggle('error', error);
  }

  function loadCollections(refresh = false) {
    if (state.listing?.epoch === state.epoch) return state.listing.promise;
    const listing = { epoch: state.epoch };
    listing.promise = readCollections(refresh, listing.epoch).finally(() => {
      if (state.listing === listing) state.listing = null;
    });
    state.listing = listing;
    return listing.promise;
  }

  async function readCollections(refresh, epoch) {
    message('Reading your collections from Civitai…');
    try {
      const { collections } = await api(`/api/collectalytics/collections${refresh ? '?refresh=1' : ''}`);
      if (epoch !== state.epoch) return;
      state.collections = collections;
      renderPicker();
      if (!collections.length) { message('No collections found that you own or follow.'); return; }
      const keep = collections.find(item => item.id === state.selected);
      await open(keep ? keep.id : collections[0].id, false);
    } catch (error) {
      if (epoch !== state.epoch) return;
      state.collections = null;
      message(error.message, true);
      $('caBody').classList.add('hidden');
    }
  }

  function renderPicker() {
    const select = $('caCollection');
    select.replaceChildren();
    const groups = [['Owned', state.collections.filter(item => item.owned)],
      ['Followed', state.collections.filter(item => !item.owned)]];
    for (const [label, items] of groups) {
      if (!items.length) continue;
      const group = element('optgroup');
      group.label = label;
      for (const item of items) {
        const option = element('option', '', item.ownerName ? `${item.name} — ${item.ownerName}` : item.name);
        option.value = String(item.id);
        group.append(option);
      }
      select.append(group);
    }
    select.disabled = !state.collections.length;
    if (state.selected != null) select.value = String(state.selected);
  }

  async function open(id, refresh) {
    const token = ++state.loading;
    state.selected = id;
    state.creatorKey = null;
    state.day = null;
    save();
    $('caCollection').value = String(id);
    $('caRefresh').disabled = true;
    // The editor belongs to the report on screen; it waits until the new one arrives.
    setSnippetEnabled(false);
    const name = state.collections?.find(item => item.id === id)?.name || 'the collection';
    message(`Reading ${name} from Civitai… a large collection takes a few seconds.`);
    try {
      const report = await api(`/api/collectalytics/collection?id=${id}${refresh ? '&refresh=1' : ''}`, { timeoutMs: 180000 });
      if (token !== state.loading) return;
      state.report = report;
      state.analysis = analyse(report.items);
      const fetched = new Date(report.fetchedAt);
      message(`${number(report.items.length)} accepted items read ${Number.isNaN(fetched.getTime()) ? '' : `at ${fetched.toLocaleTimeString(undefined, { timeStyle: 'short' })}`}.` +
        (report.truncated ? ' This collection is larger than the app reads at once, so only the most recently added items are counted.' : ''));
      render();
    } catch (error) {
      if (token !== state.loading) return;
      message(error.message, true);
      $('caBody').classList.add('hidden');
    } finally {
      if (token === state.loading) $('caRefresh').disabled = false;
    }
  }

  function setSnippetEnabled(enabled) {
    for (const id of ['caTitle', 'caLines', 'caHeading', 'caSnippetReset', 'caCopyRich', 'caCopySource']) $(id).disabled = !enabled;
  }

  function render() {
    $('caBody').classList.remove('hidden');
    setSnippetEnabled(true);
    renderStats();
    renderDays();
    renderRecent();
    renderCreators();
    renderCreatorOptions();
    // Opens on whoever was added last: the question is usually about the newest pick.
    const newest = state.analysis.dated[0];
    if (newest) selectCreator(newest.creatorKey, false);
    else renderCreator();
    renderSnippet();
  }

  function renderStats() {
    const { report, analysis } = state;
    const tiles = [
      [number(report.items.length), `${itemNoun(report.items)} collected`],
      [number(analysis.creators.size), 'unique creators'],
      [report.followers == null ? '—' : number(report.followers), report.followers == null ? 'followers (not reported for private collections)' : 'followers'],
    ];
    const length = videoLength(report.items);
    if (length.videos && length.missing < length.videos) {
      tiles.push([formatDuration(length.seconds), length.missing
        ? `of video content · ${number(length.missing)} video${length.missing === 1 ? ' has' : 's have'} no length on Civitai`
        : 'of video content']);
    }
    if (analysis.first && analysis.last) {
      const days = Math.max(1, Math.round((startOfDay(analysis.last) - startOfDay(analysis.first)) / DAY_MS) + 1);
      tiles.push([(analysis.dated.length / days).toFixed(1), `per day over ${number(days)} day${days === 1 ? '' : 's'}`]);
      tiles.push([ago(analysis.last), `last added · ${formatStamp(analysis.last)}`]);
    }
    $('caStats').replaceChildren(...tiles.map(([value, label]) => {
      const tile = element('div', 'ca-stat');
      tile.append(element('strong', '', value), element('span', '', label));
      return tile;
    }));
    const collection = report.collection;
    const link = $('caOpen');
    link.href = collection.url;
    link.textContent = `Open ${collection.name} on Civitai ↗`;
    $('caOwner').textContent = collection.owned ? 'Your collection' : (collection.ownerName ? `By ${collection.ownerName}` : 'Followed');
  }

  // -- items per day ----------------------------------------------------------------

  function renderDays() {
    const rows = buckets(state.analysis.dated, state.bucket);
    for (const button of $('caBucket').querySelectorAll('button')) {
      button.setAttribute('aria-checked', String(button.dataset.bucket === state.bucket));
    }
    const chart = $('caDays');
    chart.replaceChildren();
    const undated = state.analysis.items.length - state.analysis.dated.length;
    const unit = state.bucket === 'week' ? 'week' : 'day';
    const busiest = rows.reduce((best, row) => (row.count > (best?.count ?? 0) ? row : best), null);
    $('caDayNote').textContent = rows.length
      ? `${number(rows.length)} ${unit}s since ${formatDay(rows[0].date, true)}. Busiest: ${label(busiest)}, with ${number(busiest.count)}. Days are in your time zone.` +
        (undated ? ` ${number(undated)} item${undated === 1 ? ' has' : 's have'} no added date and ${undated === 1 ? 'is' : 'are'} left out.` : '')
      : 'No added dates to chart.';
    if (!rows.length) return;
    const step = state.bucket === 'week' ? 28 : 16;
    const left = 36, top = 18, bottom = 30, height = 220;
    const width = Math.max(chart.parentElement.clientWidth - 4, left + rows.length * step + 12);
    const plot = height - top - bottom;
    const max = Math.max(1, ...rows.map(row => row.count));
    const tick = Math.max(1, Math.ceil(max / 4));
    const ceiling = tick * Math.ceil(max / tick);
    const columnWidth = (width - left - 12) / rows.length;
    chart.setAttribute('viewBox', `0 0 ${width} ${height}`);
    chart.setAttribute('width', String(width));
    chart.setAttribute('height', String(height));
    chart.setAttribute('aria-label', `Items added per ${unit}, bar chart. Busiest ${unit}: ${busiest ? `${formatDay(busiest.date, true)}, ${busiest.count}` : 'none'}.`);
    for (let value = 0; value <= ceiling; value += tick) {
      const y = top + plot - value / ceiling * plot;
      chart.append(svg('line', { x1: left, x2: width - 8, y1: y, y2: y, class: value ? 'ca-grid-line' : 'ca-axis' }),
        svg('text', { x: left - 6, y: y + 4, 'text-anchor': 'end', class: 'ca-tick' }, String(value)));
    }
    // A date label roughly every 70px, always on the first column.
    const every = Math.max(1, Math.ceil(70 / columnWidth));
    rows.forEach((row, index) => {
      const x = left + index * columnWidth;
      const barWidth = Math.max(2, Math.min(columnWidth - 2, 22));
      const barX = x + (columnWidth - barWidth) / 2;
      const barHeight = row.count / ceiling * plot;
      if (row.count) chart.append(svg('path', { d: topRounded(barX, top + plot - barHeight, barWidth, barHeight, 3), class: `ca-bar${state.day === row.key ? ' selected' : ''}` }));
      if (index % every === 0) chart.append(svg('text', { x: x + columnWidth / 2, y: height - 10, 'text-anchor': 'middle', class: 'ca-tick' }, formatDay(row.date)));
      const hit = svg('rect', { x, y: top, width: columnWidth, height: plot, class: 'ca-hit', tabindex: '0', role: 'button',
        'aria-label': `${label(row)}: ${row.count}` });
      hit.addEventListener('mouseenter', () => tooltip(row, hit));
      hit.addEventListener('focus', () => tooltip(row, hit));
      hit.addEventListener('mouseleave', () => tooltip(null));
      hit.addEventListener('blur', () => tooltip(null));
      const choose = () => { state.day = state.day === row.key ? null : row.key; renderDays(); };
      hit.addEventListener('click', choose);
      hit.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); choose(); } });
      chart.append(hit);
    });
    // Newest days are the interesting end, so a wide chart opens scrolled to them.
    chart.parentElement.scrollLeft = chart.parentElement.scrollWidth;
    renderDayItems(rows);
  }

  function label(row) {
    if (state.bucket !== 'week') return row.date.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' });
    const end = new Date(row.date.getFullYear(), row.date.getMonth(), row.date.getDate() + 6);
    return `Week of ${formatDay(row.date)} – ${formatDay(end, true)}`;
  }

  function topRounded(x, y, width, height, radius) {
    const r = Math.min(radius, width / 2, height);
    return `M${x},${y + height}V${y + r}Q${x},${y} ${x + r},${y}H${x + width - r}Q${x + width},${y} ${x + width},${y + r}V${y + height}Z`;
  }

  function tooltip(row, target) {
    const tip = $('caTooltip');
    if (!row) { tip.classList.add('hidden'); return; }
    tip.replaceChildren(element('strong', '', number(row.count)), document.createTextNode(` ${row.count === 1 ? 'item' : 'items'} · ${label(row)}`));
    tip.classList.remove('hidden');
    const box = target.getBoundingClientRect();
    const frame = tip.offsetParent.getBoundingClientRect();
    tip.style.left = `${Math.min(Math.max(0, box.left - frame.left + box.width / 2 - tip.offsetWidth / 2), frame.width - tip.offsetWidth)}px`;
    tip.style.top = `${box.top - frame.top - tip.offsetHeight - 6}px`;
  }

  function renderDayItems(rows) {
    const list = $('caDayItems');
    const row = rows.find(item => item.key === state.day);
    list.classList.toggle('hidden', !row);
    if (!row) { list.replaceChildren(); return; }
    const end = new Date(row.date.getFullYear(), row.date.getMonth(), row.date.getDate() + (state.bucket === 'week' ? 7 : 1));
    const items = state.analysis.dated.filter(item => item.added >= row.date && item.added < end);
    const head = element('div', 'ca-day-head');
    const close = element('button', 'link-button', 'Close');
    close.type = 'button';
    close.addEventListener('click', () => { state.day = null; renderDays(); });
    head.append(element('strong', '', `${label(row)} · ${number(items.length)} added`), close);
    const grid = element('div', 'ca-thumbs');
    grid.append(...items.map(item => thumb(item, true)));
    list.replaceChildren(head, grid);
  }

  // -- recent additions and creators ------------------------------------------------

  function creatorButton(creator) {
    const button = element('button', 'ca-creator-link', creator.name);
    button.type = 'button';
    button.addEventListener('click', () => selectCreator(creator.key, true));
    return button;
  }

  function renderRecent() {
    const { analysis } = state;
    const now = Date.now();
    $('caRecent').replaceChildren(...analysis.dated.slice(0, RECENT).map(item => {
      const creator = analysis.creators.get(item.creatorKey);
      const previous = analysis.previous.get(item.collectionItemId);
      const row = element('li', 'ca-recent-row');
      const text = element('div', 'ca-recent-text');
      const name = element('div', 'ca-recent-name');
      name.append(creatorButton(creator), element('small', '', ago(item.added, now)));
      const note = previous
        ? `Previous from them ${span(item.added - previous.added)} earlier · ${number(creator.items.length)} in all`
        : creator.items.length === 1 ? 'First from this creator' : `First from them · ${number(creator.items.length)} in all`;
      text.append(name, element('small', previous && item.added - previous.added < 7 * DAY_MS ? 'ca-recent-note soon' : 'ca-recent-note', note));
      row.append(thumb(item), text);
      return row;
    }));
  }

  function renderCreators() {
    const { analysis } = state;
    const shown = state.allCreators ? analysis.ranked : analysis.ranked.slice(0, TOP_CREATORS);
    const max = Math.max(1, ...analysis.ranked.map(creator => creator.items.length));
    const total = analysis.items.length || 1;
    const top = analysis.ranked[0];
    $('caCreatorNote').textContent = top
      ? `${number(analysis.creators.size)} creators. The top creator has ${(top.items.length / total * 100).toFixed(1)}% of the collection; the top five have ${(analysis.ranked.slice(0, 5).reduce((sum, creator) => sum + creator.items.length, 0) / total * 100).toFixed(1)}%.`
      : 'No items yet.';
    $('caCreatorBars').replaceChildren(...shown.map(creator => {
      const row = element('button', `ca-bar-row${creator.key === state.creatorKey ? ' selected' : ''}`);
      row.type = 'button';
      row.title = `${creator.name}: ${creator.items.length}, last added ${creator.last ? formatStamp(creator.last) : 'unknown'}`;
      const track = element('span', 'ca-bar-track');
      const fill = element('span', 'ca-bar-fill');
      fill.style.width = `${creator.items.length / max * 100}%`;
      track.append(fill);
      row.append(element('span', 'ca-bar-name', creator.name), track, element('span', 'ca-bar-value', number(creator.items.length)),
        element('span', 'ca-bar-last', creator.last ? ago(creator.last) : ''));
      row.addEventListener('click', () => selectCreator(creator.key, true));
      return row;
    }));
    const button = $('caAllCreators');
    button.classList.toggle('hidden', analysis.ranked.length <= TOP_CREATORS);
    button.textContent = state.allCreators ? `Show top ${TOP_CREATORS}` : `Show all ${number(analysis.ranked.length)}`;
  }

  function renderCreatorOptions() {
    $('caCreators').replaceChildren(...[...state.analysis.creators.values()]
      .sort((a, b) => a.name.localeCompare(b.name)).map(creator => {
        const option = element('option');
        option.value = creator.name;
        return option;
      }));
    $('caCreator').value = '';
  }

  function selectCreator(key, scroll) {
    state.creatorKey = key;
    const creator = state.analysis.creators.get(key);
    if (creator) $('caCreator').value = creator.name;
    renderCreator();
    renderCreators();
    if (scroll) $('caCreatorPanel').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  function findCreator(text) {
    const wanted = text.trim().toLowerCase();
    if (!wanted) return null;
    const creators = [...state.analysis.creators.values()];
    return creators.find(creator => creator.name.toLowerCase() === wanted) || null;
  }

  function renderCreator() {
    const box = $('caCreatorReport');
    const creator = state.analysis?.creators.get(state.creatorKey);
    if (!creator) {
      const typed = $('caCreator').value.trim();
      box.replaceChildren(element('p', 'ca-empty', typed ? `Nothing from “${typed}” in this collection.` : 'Type a name, or pick one from the lists here.'));
      return;
    }
    const { analysis } = state;
    const now = Date.now();
    const rank = analysis.ranked.indexOf(creator) + 1;
    const datedItems = creator.items.filter(item => item.added);
    const recent = days => datedItems.filter(item => now - item.added < days * DAY_MS).length;
    const head = element('div', 'ca-creator-head');
    const name = element('a', '', creator.name);
    name.href = creator.profileUrl || '#';
    name.target = '_blank';
    name.rel = 'noopener';
    head.append(name, element('span', '', `${number(creator.items.length)} item${creator.items.length === 1 ? '' : 's'} · #${rank} of ${number(analysis.creators.size)} · ${(creator.items.length / (analysis.items.length || 1) * 100).toFixed(1)}%`));
    const verdict = element('p', 'ca-verdict');
    if (creator.last) {
      verdict.append('Last added ', element('strong', '', ago(creator.last, now)), ` (${formatStamp(creator.last)}).`);
      verdict.append(` ${number(recent(7))} in the past 7 days, ${number(recent(30))} in the past 30.`);
      if (datedItems.length > 1) {
        const gaps = datedItems.slice(0, -1).map((item, index) => item.added - datedItems[index + 1].added);
        verdict.append(` Typically ${span(gaps.sort((a, b) => a - b)[Math.floor(gaps.length / 2)])} between additions.`);
      }
    } else {
      verdict.textContent = 'No added dates for this creator.';
    }
    const grid = element('div', 'ca-thumbs');
    grid.append(...creator.items.slice(0, CREATOR_THUMBS).map(item => thumb(item, true)));
    const parts = [head, verdict, timeline(creator), grid];
    if (creator.items.length > CREATOR_THUMBS) parts.push(element('p', 'ca-empty', `And ${number(creator.items.length - CREATOR_THUMBS)} older.`));
    box.replaceChildren(...parts);
  }

  // Every addition as a faint tick across the collection's life; this creator's in colour.
  function timeline(creator) {
    const { first, last, dated } = state.analysis;
    const chart = svg('svg', { class: 'ca-timeline', viewBox: '0 0 600 56', preserveAspectRatio: 'none', role: 'img' });
    if (!first || !last) return chart;
    const spanMs = Math.max(1, last - first);
    const x = date => 6 + (date - first) / spanMs * 588;
    chart.setAttribute('aria-label', `When ${creator.name}'s items were added, between ${formatDay(first, true)} and ${formatDay(last, true)}.`);
    chart.append(svg('line', { x1: 6, x2: 594, y1: 28, y2: 28, class: 'ca-axis' }));
    for (const item of dated) chart.append(svg('line', { x1: x(item.added), x2: x(item.added), y1: 22, y2: 34, class: 'ca-tick-mark' }));
    for (const item of creator.items) {
      if (!item.added) continue;
      const dot = svg('circle', { cx: x(item.added), cy: 28, r: 5, class: 'ca-dot' });
      dot.append(svg('title', {}, formatStamp(item.added)));
      chart.append(dot);
    }
    const wrap = element('div', 'ca-timeline-wrap');
    const scale = element('div', 'ca-timeline-scale');
    scale.append(element('span', '', formatDay(first, true)), element('span', '', formatDay(last, true)));
    wrap.append(chart, scale);
    return wrap;
  }

  function thumb(item, captioned = false) {
    const link = element('a', 'ca-thumb');
    if (item.url) { link.href = item.url; link.target = '_blank'; link.rel = 'noopener'; }
    const added = item.added ? formatStamp(item.added) : 'unknown date';
    link.title = `${item.creator || 'Unknown'} · added ${added}${item.name ? ` · ${item.name}` : ''}`;
    if (item.thumbUrl) {
      const image = element('img');
      image.loading = 'lazy';
      image.alt = '';
      image.src = item.thumbUrl;
      image.addEventListener('error', () => { image.remove(); link.classList.add('missing'); }, { once: true });
      link.append(image);
    } else {
      link.classList.add('missing');
      link.append(element('span', 'ca-thumb-note', item.thumbHidden ? 'Hidden by your filters' : item.kind));
    }
    // Images open in the app's own detail dialog; Civitai stays a middle-click away.
    if (item.kind === 'image' && item.thumbUrl && item.itemId) {
      link.addEventListener('click', event => {
        if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || !window.CivitaiShowDetails) return;
        event.preventDefault();
        const creator = state.analysis?.creators.get(item.creatorKey);
        window.CivitaiShowDetails(
          { id: item.itemId, type: item.mediaType === 'video' ? 'video' : 'image', thumbnailUrl: item.thumbUrl, url: item.thumbUrl,
            detailPath: `/api/collectalytics/image?id=${encodeURIComponent(item.itemId)}` },
          { username: item.creator || 'Unknown', imageCount: creator?.items.length ?? 1 });
      });
    }
    if (item.mediaType === 'video') link.append(element('span', 'ca-thumb-badge', '▶'));
    if (captioned && item.added) link.append(element('span', 'ca-thumb-caption', formatDay(item.added, item.added.getFullYear() !== new Date().getFullYear())));
    return link;
  }

  // -- snippet ---------------------------------------------------------------------

  function snippetSettings() {
    const all = readJSON(localStorage, SNIPPET_KEY);
    return all[state.report.collection.id] || null;
  }

  function storeSnippet() {
    const all = readJSON(localStorage, SNIPPET_KEY);
    // Only what differs from the defaults is kept, so a better default still reaches
    // a collection whose other fields were edited.
    const defaults = snippetDefaults();
    const changed = Object.fromEntries(Object.entries({ title: $('caTitle').value, lines: $('caLines').value, heading: $('caHeading').value })
      .filter(([name, value]) => value !== defaults[name]));
    if (Object.keys(changed).length) all[state.report.collection.id] = changed;
    else delete all[state.report.collection.id];
    localStorage.setItem(SNIPPET_KEY, JSON.stringify(all));
  }

  function snippetDefaults() {
    return { title: collectionTitle(state.report.collection.name), lines: DEFAULT_LINES, heading: 'h2' };
  }

  function renderSnippet() {
    const stored = snippetSettings();
    const defaults = snippetDefaults();
    $('caTitle').value = stored?.title ?? defaults.title;
    $('caLines').value = stored?.lines ?? defaults.lines;
    $('caHeading').value = stored?.heading ?? defaults.heading;
    updatePreview();
  }

  function currentSnippet() {
    const { report, analysis } = state;
    const values = { creators: number(analysis.creators.size), items: number(report.items.length),
      followers: report.followers == null ? '?' : number(report.followers), noun: itemNoun(report.items),
      duration: formatDuration(videoLength(report.items).seconds) };
    return snippet({ title: $('caTitle').value, lines: $('caLines').value, heading: $('caHeading').value }, values, report.collection.url);
  }

  function updatePreview() {
    if (!state.report) return;
    const { html } = currentSnippet();
    // Everything typed is escaped by snippet(); the markup itself is a fixed set of tags.
    const template = document.createElement('template');
    template.innerHTML = html;
    for (const link of template.content.querySelectorAll('a')) { link.target = '_blank'; link.rel = 'noopener'; }
    $('caPreview').replaceChildren(template.content);
    $('caSource').textContent = html;
    $('caFollowersNote').classList.toggle('hidden', state.report.followers != null || !$('caLines').value.includes('{followers}'));
  }

  async function copy(rich) {
    const { html, text } = currentSnippet();
    try {
      if (rich && window.ClipboardItem && navigator.clipboard?.write) {
        await navigator.clipboard.write([new ClipboardItem({
          'text/html': new Blob([html], { type: 'text/html' }),
          'text/plain': new Blob([text], { type: 'text/plain' }),
        })]);
      } else if (!rich && navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(html);
      } else {
        fallbackCopy(rich, html);
      }
      status(rich ? 'Copied. Paste it into the Civitai editor.' : 'HTML copied.');
    } catch (_) {
      try { fallbackCopy(rich, html); status(rich ? 'Copied. Paste it into the Civitai editor.' : 'HTML copied.'); }
      catch (_) { status('The browser refused the clipboard. Select the preview and copy it by hand.', true); }
    }
  }

  // Without the async clipboard (a LAN address is not a secure context), copying a
  // selection still carries the rendered HTML.
  function fallbackCopy(rich, html) {
    const selection = window.getSelection();
    const range = document.createRange();
    let scratch = null;
    if (rich) {
      range.selectNodeContents($('caPreview'));
    } else {
      scratch = element('textarea', 'ca-scratch');
      scratch.value = html;
      document.body.append(scratch);
      scratch.select();
    }
    if (rich) { selection.removeAllRanges(); selection.addRange(range); }
    const done = document.execCommand('copy');
    selection.removeAllRanges();
    scratch?.remove();
    if (!done) throw new Error('copy refused');
  }

  function status(text, error = false) {
    const node = $('caCopyStatus');
    node.textContent = text;
    node.classList.toggle('error', error);
  }

  // -- wiring ----------------------------------------------------------------------

  $('caCollection').addEventListener('change', event => open(Number(event.target.value), false));
  $('caRefresh').addEventListener('click', () => {
    if (!state.collections) { loadCollections(true); return; }
    open(state.selected, true);
  });
  $('caBucket').addEventListener('click', event => {
    const button = event.target.closest('button[data-bucket]');
    if (!button || button.dataset.bucket === state.bucket) return;
    state.bucket = button.dataset.bucket;
    state.day = null;
    save();
    renderDays();
  });
  $('caAllCreators').addEventListener('click', () => { state.allCreators = !state.allCreators; renderCreators(); });
  $('caCreator').addEventListener('input', event => {
    if (!state.analysis) return;
    const creator = findCreator(event.target.value);
    state.creatorKey = creator?.key ?? null;
    renderCreator();
    renderCreators();
  });
  for (const id of ['caTitle', 'caLines', 'caHeading']) {
    $(id).addEventListener('input', () => { if (!state.report) return; storeSnippet(); updatePreview(); });
  }
  $('caSnippetReset').addEventListener('click', () => {
    if (!state.report) return;
    const all = readJSON(localStorage, SNIPPET_KEY);
    delete all[state.report.collection.id];
    localStorage.setItem(SNIPPET_KEY, JSON.stringify(all));
    renderSnippet();
  });
  $('caCopyRich').addEventListener('click', () => copy(true));
  $('caCopySource').addEventListener('click', () => copy(false));
  // Signing in, out or as someone else changes which collections there are.
  window.addEventListener('discovery-auth-changed', ({ detail }) => {
    const account = detail?.connected ? String(detail.id ?? detail.username ?? '') : null;
    if (state.account === undefined) { state.account = account; return; }
    if (account === state.account) return;
    state.account = account;
    state.epoch++;
    state.collections = null;
    state.report = null;
    state.analysis = null;
    state.stale = false;
    state.loading++;
    $('caBody').classList.add('hidden');
    if (state.active) loadCollections();
  });
  // The chart is drawn to its frame's width, so a resized window redraws it.
  let frameWidth = 0;
  new ResizeObserver(([entry]) => {
    const width = Math.round(entry.contentRect.width);
    if (!width || width === frameWidth) return;
    frameWidth = width;
    if (state.analysis) renderDays();
  }).observe($('caDays').parentElement);
  // Content levels and hidden lists decide which stills may show.
  // While the tab is hidden the report is only marked, and reread on return.
  window.addEventListener('community-controls-changed', () => {
    if (!state.report) return;
    if (state.active) { open(state.selected, false); return; }
    state.stale = true;
    $('caBody').classList.add('hidden');
  });

  return {
    setActive(active) {
      state.active = active;
      if (!active) return;
      if (!state.collections) { loadCollections(); return; }
      if (state.stale) { state.stale = false; open(state.selected, false); }
    },
  };
}
