# Beta releases

Version **0.3.1-beta.1** is the current public beta of Civitai Artist Discovery. It keeps
the existing local Python/SQLite architecture while incorporating the fixes and features
validated during alpha testing.

The 0.3.1 beta refreshes My Profile automatically when its last successful update is more
than 24 hours old, while deferring during gallery collection. It also adds a persistent
option to disable the visual dimming of viewed cards without clearing seen history or
changing discovery order. If a generated Civitai CDN preview is missing, gallery cards
and the detail dialog now retry the original artwork once instead of remaining black.

The full-day progress handoff fix from 0.3.0 Beta 2 remains included: Evening visibly
restarts its Find, Collect, and Organize phases after Morning completes, while the overall
bar continues from 50% to 100%.

## Release highlights

- Portable-only storage in `data/` beside the application, with no Registry settings.
- Windows notification-area controls for opening and stopping the local application.
- Civitai Red collection with independent PG, PG-13, R, X, and XXX viewing filters.
- Resumable Morning, Evening, and full-day gallery collection with fixed API-capacity
  time estimates.
- Large, Medium, and Small artist cards with consistently scaled headers and controls.
- Popular ranking based on total daily reactions, with the most-reacted image first.
- Personalized For You discovery using reaction history and a cached fingerprint of the
  connected account's public uploads.
- Automatic profile refresh after 24 hours, with incremental upload-fingerprint reads.
- Persistent control over viewed-card dimming while preserving seen-history ranking.
- Automatic fallback to original artwork when a generated CDN preview is unavailable.
- A My Profile dashboard for distinctive tags, model signals, reaction preferences, and
  creator recommendations.
- Secure OAuth token storage through Windows DPAPI or Linux Secret Service.

This remains pre-1.0 software. Windows is the primary tested platform; Linux source use
is experimental and Linux packaging is not currently provided. Packages are unsigned
and may trigger Windows SmartScreen or managed-device policy.

For installation, operation, privacy, testing, and build details, see the project
[README](../README.md).
