import { showPageError } from './feedback.js';

// Daily and Community share one viewport observer and one single-flight coordinator.
// A short/filtered page can leave its sentinel on screen. Bound automatic filling;
// subsequent scroll/wheel/touch/keyboard intent supplies one additional page budget.
const owners = new WeakMap(), active = new Set();
const observer = new IntersectionObserver(entries => {
  for (const entry of entries) if (entry.isIntersecting) owners.get(entry.target)?.approach();
}, {rootMargin:'800px'});
let frame = 0;
function demand() {
  if (frame) return;
  frame = requestAnimationFrame(() => {
    frame = 0;
    for (const pager of active) pager.demand();
  });
}
window.addEventListener('scroll', demand, {passive:true});
window.addEventListener('wheel', demand, {passive:true});
window.addEventListener('touchmove', demand, {passive:true});
window.addEventListener('keydown', event => {
  if (['PageDown','End','ArrowDown',' '].includes(event.key) &&
      !event.target.closest('input,textarea,select,dialog,[contenteditable="true"]')) demand();
});

export function createInfinitePager({container, sentinel, load, hasMore, visible,
  loading='Loading more artists…', exhausted="You're caught up for this view.", onError=()=>{}}) {
  let enabled = false, target = null, flight = null, request = null, epoch = 0, failure = null, budget = 2;
  const mutations = new MutationObserver(() => connect());
  const element = () => typeof sentinel === 'function' ? sentinel() : sentinel;
  function connect(rearm = false) {
    const next = enabled ? element() : null;
    if (target === next && !rearm) return;
    if (target) { observer.unobserve(target); owners.delete(target); }
    target = next;
    if (target) { owners.set(target, pager); observer.observe(target); }
  }
  function paint() {
    const node = element();
    if (!node) return;
    node.setAttribute('role','status'); node.setAttribute('aria-live','polite');
    node.setAttribute('aria-busy', flight ? 'true' : 'false');
    if (failure) showPageError(node, 'Could not load this page. '+failure.message, ()=>pager.retry(), 'Retry loading artists');
    else node.textContent = hasMore() ? loading : (typeof exhausted === 'function' ? exhausted() : exhausted);
  }
  function next(manual = false) {
    // Startup/scroll restoration can request Daily's first page without a tab click.
    if (manual && visible() && !enabled) pager.resume();
    if (flight) return flight;
    if (failure || !hasMore() || (!manual && (!enabled || !visible() || budget <= 0))) {
      paint(); return Promise.resolve();
    }
    if (!manual) budget--;
    const version = epoch, controller = new AbortController(); request = controller;
    const task = Promise.resolve().then(() => load({signal:controller.signal, controller})).catch(error => {
      if (version === epoch && error.name !== 'AbortError') { failure = error; onError(error); }
    }).finally(() => {
      if (version !== epoch) return;
      flight = null; request = null; paint(); connect(true);
    });
    flight = task; paint();
    return task;
  }
  function cancel(clearFailure = true) {
    ++epoch; request?.abort(); request = null; flight = null;
    if (clearFailure) failure = null;
    paint();
  }
  const pager = {
    request:next, cancel,
    reset() { cancel(); budget = 2; connect(true); },
    retry() { failure = null; return next(true); },
    resume() {
      if (enabled) return;
      enabled = true; budget = 1; active.add(pager);
      mutations.observe(container,{childList:true}); connect();
    },
    pause() {
      enabled = false; active.delete(pager); mutations.disconnect(); connect(); cancel(false);
    },
    approach() { if (visible()) next(); },
    demand() {
      if (!enabled || !visible() || document.querySelector('dialog[open]')) return;
      budget = 1;
      const node = element(), bounds = node?.getBoundingClientRect();
      if (bounds && bounds.top <= innerHeight+800 && bounds.bottom >= 0) next();
    },
    get pending() { return !!flight; },
  };
  return pager;
}
