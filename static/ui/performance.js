// Opt-in, in-memory diagnostics. No account identifiers, image URLs, prompts, or
// telemetry requests are recorded. Enable with ?uiPerf=1 and inspect snapshot().
export function startPerformanceTrace() {
  const start = performance.now();
  const metrics = { firstPreviewFrameMs: null, longTasks: 0, longTaskMs: 0, layoutShift: 0 };
  const observers = [];
  for (const type of ['longtask', 'layout-shift']) {
    if (!PerformanceObserver.supportedEntryTypes.includes(type)) continue;
    const observer = new PerformanceObserver(list => {
      for (const entry of list.getEntries()) {
        if (type === 'longtask') { metrics.longTasks++; metrics.longTaskMs += entry.duration; }
        else if (!entry.hadRecentInput) metrics.layoutShift += entry.value;
      }
    });
    observer.observe({ type, buffered: true });
    observers.push(observer);
  }
  const onLoad = event => {
    if (metrics.firstPreviewFrameMs !== null || !event.target.matches?.('.image-button img')) return;
    requestAnimationFrame(() => {
      if (metrics.firstPreviewFrameMs === null) metrics.firstPreviewFrameMs = Math.round(performance.now() - start);
    });
  };
  document.addEventListener('load', onLoad, true);
  return Object.freeze({
    snapshot: () => {
      const resources = performance.getEntriesByType('resource');
      const previews = resources.filter(entry => entry.initiatorType === 'img');
      return { ...metrics, elapsedMs: Math.round(performance.now() - start),
        imageRequests: previews.length,
        // Cross-origin transfer sizes may be zero without Timing-Allow-Origin.
        observableImageBytes: previews.reduce((sum, entry) => sum + entry.transferSize, 0),
        requests: resources.filter(entry => new URL(entry.name).origin === location.origin &&
          new URL(entry.name).pathname.startsWith('/api/')).map(entry => ({
            endpoint: new URL(entry.name).pathname, durationMs: Math.round(entry.duration),
          })),
      };
    },
    stop: () => { observers.forEach(observer => observer.disconnect()); document.removeEventListener('load', onLoad, true); },
  });
}
