// Apply a shared keyboard contract to existing popovers without replacing page-specific
// selection handlers. Panels keep their current checkbox/select semantics, not ARIA menus.
const popoverOwners = new Map();
export function anchorPopover(panel, getTrigger) {
  popoverOwners.set(panel, getTrigger);
  let frame = 0;
  const position = () => {
    if (panel.classList.contains('hidden')) return;
    for (const [other, owner] of popoverOwners) {
      if (other === panel || other.classList.contains('hidden')) continue;
      other.classList.add('hidden');
      owner()?.setAttribute('aria-expanded', 'false');
    }
    const trigger = getTrigger();
    if (!trigger) return;
    const bounds = trigger.getBoundingClientRect(), margin = 12, gap = 8;
    panel.style.position = 'fixed';
    panel.style.right = 'auto';
    panel.style.overflowY = 'auto';
    panel.style.maxWidth = `${Math.max(0, innerWidth - margin * 2)}px`;
    const width = panel.getBoundingClientRect().width;
    panel.style.left = `${Math.max(margin, Math.min(bounds.left, innerWidth - width - margin))}px`;
    const below = Math.max(0, innerHeight - bounds.bottom - gap - margin);
    const above = Math.max(0, bounds.top - gap - margin);
    const placeBelow = below >= Math.min(panel.scrollHeight + 2, 300) || below >= above;
    panel.style.maxHeight = `${placeBelow ? below : above}px`;
    const height = panel.getBoundingClientRect().height;
    const top = placeBelow ? bounds.bottom + gap : bounds.top - gap - height;
    panel.style.top = `${Math.max(margin, Math.min(top, innerHeight - height - margin))}px`;
  };
  const schedule = () => {
    if (frame) return;
    frame = requestAnimationFrame(() => { frame = 0; position(); });
  };
  new MutationObserver(schedule).observe(panel, { attributes: true, attributeFilter: ['class'] });
  new ResizeObserver(schedule).observe(panel);
  window.addEventListener('resize', schedule);
  window.addEventListener('scroll', schedule, { passive: true });
  return position;
}

export function enhancePopover(trigger, panel) {
  if (!trigger || !panel) return;
  anchorPopover(panel, () => trigger);
  trigger.setAttribute('aria-controls', panel.id);
  const close = event => {
    if (event.key !== 'Escape' || panel.classList.contains('hidden')) return;
    event.preventDefault();
    panel.classList.add('hidden');
    trigger.setAttribute('aria-expanded', 'false');
    trigger.focus();
  };
  trigger.addEventListener('keydown', close);
  trigger.addEventListener('keydown', event => {
    if (event.key !== 'ArrowDown') return;
    event.preventDefault();
    if (trigger.getAttribute('aria-expanded') !== 'true') trigger.click();
    requestAnimationFrame(() => panel.querySelector('button:not(:disabled), input:not(:disabled), select:not(:disabled)')?.focus());
  });
  panel.addEventListener('keydown', close);
}
