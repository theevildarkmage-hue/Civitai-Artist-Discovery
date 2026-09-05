import { mountShell, groupPageControls } from './shell.js';
import { enhancePopover } from './menu.js';
import { api } from './api.js';
import { showArtwork, showCardArtwork, wireArtworkFallback } from './artwork.js';
import { showGallerySkeleton } from './feedback.js';
import { bindDialog } from './dialog.js';

mountShell();
// Temporary bridge for the classic page controller; new modules import directly.
window.CivitaiUI = Object.freeze({ api, showArtwork, showCardArtwork, wireArtworkFallback, showGallerySkeleton });

// Transitional classic controller preserves existing integration tests and global
// callbacks while page behavior moves into modules one tested slice at a time.
const controller = document.createElement('script');
controller.src = '/app.js';
controller.onload = () => {
  groupPageControls();
  for (const [id, labelledBy, closeIds] of [
    ['details', 'detailCreator', ['close']],
    ['updateDialog', 'updateTitle', ['closeUpdate', 'laterUpdate']],
  ]) {
    bindDialog(document.getElementById(id), {
      labelledBy, closeButtons: closeIds.map(id => document.getElementById(id)),
    });
  }
  document.getElementById('close').setAttribute('aria-label', 'Close image details');
  for (const [trigger, panel] of [['contentFilter', 'contentMenu'], ['modelFilter', 'modelMenu'], ['galleryPreferences', 'preferencesMenu']]) {
    enhancePopover(document.getElementById(trigger), document.getElementById(panel));
  }
};
controller.onerror = () => {
  const message = document.createElement('p');
  message.setAttribute('role', 'alert');
  message.textContent = 'The application could not load. Refresh this page to try again.';
  document.querySelector('main').replaceChildren(message);
};
document.body.append(controller);
