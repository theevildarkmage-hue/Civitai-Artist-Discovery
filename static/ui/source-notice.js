// The source launch notice explains the Community selection once; the combined
// version/environment indicator keeps showing it. Dismissal is remembered per environment, so
// hiding the public notice never hides the warning about a Development launch.
const KEY = 'cad.sourceNoticeDismissed.';

export function mountSourceNotice() {
  const version = document.getElementById('appVersion');
  const channel = document.getElementById('appChannel');
  if (version && channel && !version.closest('.app-build-info')) {
    const group = document.createElement('span');
    group.className = `app-build-info ${channel.classList.contains('app-channel-public') ? 'app-channel-public' : 'app-channel-development'}`;
    group.setAttribute('role','group');
    group.setAttribute('aria-label','Application version and Community environment');
    version.before(group); group.append(version,channel);
  }
  const notice = document.getElementById('sourceLaunchNotice');
  if (!notice) return;
  const key = KEY + (notice.dataset.environment || 'unknown');
  let dismissed = false;
  try { dismissed = localStorage.getItem(key) === '1'; } catch (_) {}
  if (dismissed) { notice.remove(); return; }
  notice.querySelector('[data-source-notice-dismiss]')?.addEventListener('click', () => {
    try { localStorage.setItem(key, '1'); } catch (_) {}
    notice.remove();
    if (version) { version.tabIndex = -1; version.focus({preventScroll:true}); }
  });
}
