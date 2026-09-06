// Shared page structure adopts the existing controls so their API behavior and state
// remain owned by the page controllers during migration.
export function mountPageLayout() {
  for (const heading of document.querySelectorAll('.discovery-head')) {
    heading.classList.add('page-heading');
  }
  const profile = document.getElementById('discovery');
  profile.querySelector('.page-heading h2').textContent = 'Your taste profile';
  const settings = document.createElement('section');
  settings.id = 'profileSettings';
  settings.className = 'panel page-settings';
  settings.setAttribute('aria-labelledby', 'profileSettingsTitle');
  settings.innerHTML = '<div class="panel-head"><div><h3 id="profileSettingsTitle">Settings</h3><p class="panel-note">Background collection, application updates, and local profile data.</p></div></div>';
  for (const preference of profile.querySelectorAll('.update-preference')) settings.append(preference);
  const reset = document.createElement('div');
  reset.className = 'settings-reset';
  reset.innerHTML = '<div><strong>Local profile analysis</strong><p class="panel-note">Clear the analysis saved on this computer.</p></div>';
  reset.append(document.getElementById('resetDiscovery'));
  settings.append(reset);
  profile.append(settings);
}
