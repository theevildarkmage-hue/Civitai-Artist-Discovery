// The source launch notice explains the Community selection once; the channel pill
// beside the version keeps showing it. Dismissal is remembered per environment, so
// hiding the public notice never hides the warning about a Development launch.
const KEY = 'cad.sourceNoticeDismissed.';

export function mountSourceNotice() {
  const notice = document.getElementById('sourceLaunchNotice');
  if (!notice) return;
  const key = KEY + (notice.dataset.environment || 'unknown');
  let dismissed = false;
  try { dismissed = localStorage.getItem(key) === '1'; } catch (_) {}
  if (dismissed) { notice.remove(); return; }
  notice.querySelector('[data-source-notice-dismiss]')?.addEventListener('click', () => {
    try { localStorage.setItem(key, '1'); } catch (_) {}
    notice.remove();
  });
}
