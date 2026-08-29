# Beta releases

Version **0.3.3-beta.2** is the current public beta of Civitai Artist Discovery. It keeps
the existing local Python/SQLite architecture while incorporating the fixes and features
validated during alpha testing.

Daily collection continues to use Civitai's public v1 image API. A faster collector built
on the search index behind Civitai's own image browser was written and measured -- a Soft
half-day fell from an estimated six minutes to 13 seconds across 13 requests -- but it was
not shipped. Civitai's Terms of Service (11.4) allow automated access only through
interfaces expressly provided for it and only with the caller's own credentials, and that
index is reached with the search key Civitai's frontend ships to browsers. It stays behind
`CIVITAI_HISTORY_BACKEND=search`, off by default, for local diagnosis only.

That problem turned out not to be a defect. Measured against the public API on
2026-08-28: it exposes no date filter (`period` changes sort metrics, not which rows come
back), the timestamp half of its cursor is ignored, and cursor traversal stops at roughly
offset 49,000. Because that ceiling counts rows rather than time, each browsing level
reaches a different depth -- PG about 5 days, XXX about 2 -- and a block needs every
required level, so all-ratings days are reachable for barely two days. Partitioning by
`baseModels` was measured as a way around it and rejected: 12.3% of listings carry no
usable base model and would be dropped silently.

So the window is a property of Civitai's API, not a bug, and the app now treats it as one.
Each unfinished feed is asked how far back it reaches before any collecting starts -- one
1-row request per level, under a kilobyte each -- and an out-of-reach date now fails in
well under a second instead of after a full date-location sweep. The message names the
oldest day that can still be built and makes clear that days already archived are
unaffected. The build screen shows the same boundary before the button is pressed, so the
limit is visible in advance rather than discovered by failing.

The 0.3.3 Beta 2 collector no longer trusts an early end-of-feed response as proof that a
large gallery is complete. Each half-day now records independent PG/PG-13, R, X, and XXX
checkpoints and becomes ready only after every required feed crosses the requested time
boundary. X and XXX are traversed separately so an all-ratings day does not exhaust
Civitai's deep-history result window before reaching the day. Existing pages remain
checkpointed through overloads, restarts, and retries, while legacy galleries whose saved
timestamps reveal incomplete coverage are reopened for a resumable repair instead of
continuing to present a truncated artist count.

New collections also retain Civitai's visual listing hash. A request-free local duplicate
report measures hash coverage, repeated uploads, cross-creator copies, and cross-day
copies. Existing archive rows remain valid and are reported as unhashed until Civitai
returns those listings again.

This migration was validated end to end against August 23, 2026. Both half-days crossed
all four required boundaries, SQLite passed its integrity check, and the merged gallery
contained 84,020 distinct day memberships. The run also recovered from temporary Civitai
service-overload responses without losing completed pages. Because part of the migration
reused earlier checkpoints, those measurements validate completeness and recovery rather
than replace the fixed cold-build timing benchmark.

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
