// One validated, serializable browsing snapshot. Account content settings remain the
// authority for ratings; session restoration must never silently enable mature content.
export function browsingState(value = {}) {
  value = value && typeof value === 'object' ? value : {};
  const models = Array.isArray(value.models) || value.models instanceof Set ? [...value.models] : [];
  const levels = Array.isArray(value.levels) || value.levels instanceof Set ? [...value.levels] : [1, 2];
  const safeLevels = [...new Set(levels.filter(level => [1, 2, 4, 8, 16].includes(level)))];
  return {
    date: typeof value.date === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value.date) ? value.date : null,
    segment: ['morning', 'evening', 'all'].includes(value.segment) ? value.segment : 'evening',
    view: ['foryou', 'discovery', 'followed', 'new', 'emerging'].includes(value.view) ? value.view : 'foryou',
    models: [...new Set(models.filter(model => typeof model === 'string' && model.trim()))],
    levels: safeLevels.length ? safeLevels : [1, 2],
    loaded: Number.isFinite(Number(value.loaded)) ? Math.max(0, Math.floor(Number(value.loaded))) : 0,
    scrollY: Number.isFinite(Number(value.scrollY)) ? Math.max(0, Math.round(Number(value.scrollY))) : 0,
  };
}

export function modelParameters(value) {
  return browsingState(value).models.map(name => `&model=${encodeURIComponent(name)}`).join('');
}

export function readBrowsingState(storage, key) {
  try {
    const value = JSON.parse(storage.getItem(key) || 'null');
    return value && typeof value === 'object' ? browsingState(value) : null;
  } catch (_) { return null; }
}
