// Apply a shared keyboard contract to existing popovers without replacing page-specific
// selection handlers. Panels keep their current checkbox/select semantics, not ARIA menus.
export function enhancePopover(trigger, panel) {
  if (!trigger || !panel) return;
  trigger.setAttribute('aria-controls', panel.id);
  trigger.addEventListener('keydown', event => {
    if (event.key !== 'ArrowDown') return;
    event.preventDefault();
    if (trigger.getAttribute('aria-expanded') !== 'true') trigger.click();
    requestAnimationFrame(() => panel.querySelector('button:not(:disabled), input:not(:disabled), select:not(:disabled)')?.focus());
  });
  panel.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    event.preventDefault();
    panel.classList.add('hidden');
    trigger.setAttribute('aria-expanded', 'false');
    trigger.focus();
  });
}
