import { mountShell, groupPageControls } from './shell.js';
import { enhancePopover } from './menu.js';
import { api, finishControlChange } from './api.js';
import { createSelectionQueue } from './selection-queue.js';
import { showArtwork, showVideoPoster } from './artwork.js';
import { showGallerySkeleton, showPageError } from './feedback.js';
import { bindDialog } from './dialog.js';
import { createCreatorCard } from './cards.js';
import { mountFilters } from './filters.js';
import { mountPageLayout } from './pages.js';
import { browsingState, modelParameters, readBrowsingState } from './state.js';
import { toggleCreatorFollow } from './creator-actions.js';
import { renderProfile } from './profile.js';
import { startPerformanceTrace } from './performance.js';
import { mountCalendar } from './calendar.js';
import { reportClientEvent } from './diagnostics.js';
import { mountPreferences } from './preferences.js';
import { mountCollectalytics } from './collectalytics.js';
import { mountCommunity } from './community.js';
import { createInfinitePager } from './pagination.js';
import { mountDevelopmentDiagnostics } from './development.js';
import { showDetailVideo, clearDetailVideo, suspendCardVideos, refreshVideoEligibility, resetVideoRecoveryBudget } from './video.js';

if (new URLSearchParams(location.search).get('uiPerf') === '1') {
  window.CivitaiPerformance = startPerformanceTrace();
}

mountShell();
window.reportClientEvent = reportClientEvent;
// Temporary bridge for the classic page controller; new modules import directly.
window.CivitaiUI = Object.freeze({ api, finishControlChange, createSelectionQueue, showArtwork, showVideoPoster, showGallerySkeleton, showPageError, createCreatorCard, createInfinitePager, browsingState, modelParameters, readBrowsingState, toggleCreatorFollow, renderProfile, showDetailVideo, clearDetailVideo, suspendCardVideos, refreshVideoEligibility, resetVideoRecoveryBudget });

// Transitional classic controller preserves existing integration tests and global
// callbacks while page behavior moves into modules one tested slice at a time.
const controller = document.createElement('script');
controller.src = '/app.js';
controller.onload = () => {
  mountCommunity();
  mountDevelopmentDiagnostics().catch(() => {});
  groupPageControls();
  mountPageLayout();
  const preferences = mountPreferences();
  window.updateGalleryPreferencesUI = preferences.update;
  window.CivitaiCollectalytics = mountCollectalytics();
  const calendar = mountCalendar({ api,
    state: () => window.galleryCalendarState(),
    select: (date, segment) => window.selectGalleryDate(date, segment),
    rebuild: () => document.getElementById('rebuildDay').click(),
  });
  window.updateGalleryCalendar = calendar.update;
  const filters = mountFilters({
    state: () => window.galleryFilterState(),
    refreshModels: () => window.refreshModelMenu(),
    removeModel: name => window.removeGalleryModel(name).catch(error => window.toast(error.message)),
    reset: () => window.resetGalleryFilters(),
    setMedia: value => window.setGalleryMedia(value),
    toggleLevel: level => window.toggleBrowsingLevel(level),
  });
  window.updateGalleryFilters = filters.update;
  for (const [id, labelledBy, closeIds] of [
    ['details', 'detailCreator', ['close']],
    ['updateDialog', 'updateTitle', ['closeUpdate', 'laterUpdate']],
  ]) {
    bindDialog(document.getElementById(id), {
      labelledBy, closeButtons: closeIds.map(id => document.getElementById(id)),
    });
  }
  document.getElementById('close').setAttribute('aria-label', 'Close image details');
  for (const [trigger, panel] of [['galleryPreferences', 'preferencesMenu']]) {
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
