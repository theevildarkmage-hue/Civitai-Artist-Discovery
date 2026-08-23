# Beta releases

Version **0.3.3-beta.1** is the current public beta of Civitai Artist Discovery. It keeps
the existing local Python/SQLite architecture while incorporating the fixes and features
validated during alpha testing.

The 0.3.3 beta introduces a touch-friendly **Gallery preferences** panel. Viewed-card
dimming now lives beside optional high-volume artist filtering and explicit Balanced,
Strict, and Unadjusted modes for **Emerging First**. Strict mode can optionally hide
artists above a selected daily reaction total, while its safe default applies no cutoff.
All of these controls re-filter the local gallery without downloading the day again.

Initial and automatic profile analysis now uses more conservative API pacing, honors
Civitai's requested retry delay, and avoids request bursts between analysis phases. This
reduces first-launch rate limiting without changing the user's connection requirements.

The 0.3.2 beta adds an optional, user-approved updater backed by this project's GitHub
releases. It displays release notes before downloading, verifies GitHub's SHA-256 digest,
preserves the portable `data/` folder, rolls back a failed replacement, and restarts the
app. It also makes hidden-tag filtering fail closed before a preview is displayed and
keeps the feed in place when image details are opened.

Personalized discovery now makes its background tag-preparation state visible, reduces
the advantage held by creators who post very large batches, and uses separate familiarity
thresholds: an unfollowed artist receives a gallery heart after reactions to five distinct
images and appears under Worth Following after ten. Follower enrichment now keeps partial
successes and reuses cached counts instead of blanking most cards when one request fails.

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
- A Gallery preferences cog for viewed-card dimming, optional high-volume creator
  filtering, and volume-resistant or strict Emerging First discovery.
- Automatic fallback to original artwork when a generated CDN preview is unavailable.
- A My Profile dashboard for distinctive tags, model signals, reaction preferences, and
  creator recommendations.
- Secure OAuth token storage through Windows DPAPI or Linux Secret Service.
- Optional daily GitHub release checks with a plain-text changelog, verified portable
  download, preserved `data/`, rollback on copy failure, and automatic restart.

This remains pre-1.0 software. Windows is the primary tested platform; Linux source use
is experimental and Linux packaging is not currently provided. Packages are unsigned
and may trigger Windows SmartScreen or managed-device policy.

For installation, operation, privacy, testing, and build details, see the project
[README](../README.md).
