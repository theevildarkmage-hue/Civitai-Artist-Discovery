import { icon } from './icons.js';

// Adopt existing controls so their state and handlers survive incremental page extraction.
// Page controllers own their data; the shell owns shared placement and navigation styling.
export function mountShell() {
  const header = document.querySelector('body > header');
  const brand = header.firstElementChild;
  const account = document.createElement('div');
  account.className = 'toolbar account-toolbar';
  account.setAttribute('aria-label', 'Account and application');
  for (const id of ['accountStatus', 'connect', 'disconnect', 'updateAvailable', 'closeApp']) {
    account.append(document.getElementById(id));
  }
  const navigation = document.createElement('div');
  navigation.className = 'gallery-navigation toolbar';
  navigation.setAttribute('aria-label', 'Gallery date');
  for (const id of ['olderDay', 'selectedDate', 'newerDay', 'rebuildDay', 'summary']) {
    navigation.append(document.getElementById(id));
  }
  brand.classList.add('app-brand');
  header.classList.add('app-header');
  header.replaceChildren(brand, account);
  const tabs = document.querySelector('.view-tabs');
  tabs.setAttribute('aria-label', 'Main navigation');
  tabs.after(navigation);
  for (const [id, name] of [['tabGallery', 'gallery'], ['tabTimeMachine', 'history'], ['tabDiscovery', 'profile']]) {
    const button = document.getElementById(id);
    button.insertAdjacentHTML('afterbegin', icon(name));
  }
  const measure = () => document.documentElement.style.setProperty('--tabs-h', `${Math.ceil(tabs.getBoundingClientRect().height)}px`);
  new ResizeObserver(measure).observe(tabs);
  measure();
  const skip = document.createElement('a');
  skip.className = 'skip-link';
  skip.href = '#mainContent';
  skip.textContent = 'Skip to content';
  const main = document.querySelector('main');
  main.id = 'mainContent';
  main.tabIndex = -1;
  document.body.prepend(skip);
}

export function groupPageControls() {
  for (const id of ['daySegment', 'dayView', 'cardSize']) {
    const select = document.getElementById(id);
    const label = document.querySelector(`label[for="${id}"]`);
    const field = document.createElement('div');
    field.className = 'ui-field';
    label.before(field);
    field.append(label, select);
  }
  const tabs = document.querySelector('.view-tabs');
  const toolbar = document.querySelector('.segment-toolbar');
  tabs.classList.add('command-bar');
  tabs.append(toolbar);
  // Settings belong to the application, not only the Gallery page.
  tabs.append(document.getElementById('galleryPreferences'));
  document.querySelector('.gallery-navigation').classList.add('legacy-gallery-navigation');
}
