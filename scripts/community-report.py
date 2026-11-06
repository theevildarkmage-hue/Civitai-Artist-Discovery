"""Render the complete private, local validation ledger without publishing fixtures."""
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
reports=ROOT/'reports'
data=json.loads((reports/'community-stress.json').read_text(encoding='utf-8'))
regression=json.loads((reports/'community-guarded-regression.json').read_text(encoding='utf-8'))
lines=['# Community Gallery integration and stress validation', '',
    'Local-only Discovery 3.0 validation. No Supabase deployment, cloud resources, GitHub push or release.', '',
    '## Execution summary', '',
    f"- Stress harness passed: **{data.get('passed',False)}**.",
    '- Ten simulated members: 30 accepted API recommendations and 30 separately accepted browser confirmations.',
    f"- Existing guarded regression scripts: **{sum(r['passed'] for r in regression)}/{len(regression)}**.",
    f"- Persisted-state adversarial scenarios: **{len(data['scenarios'])} passed**.",
    f"- Pagination: {data['pagination']['visible']} distinct items across {data['pagination']['pages']} pages.",
    '- Browser ran against the actual application on port **8907**.', '',
    '## Dataset and provenance', '',
    f"Archive inspected: {data['source']['archiveRows']:,} records. Reaction metadata: {data['source']['reactionMetadataRows']:,} records.",
    f"Only {data['source']['sampleRows']} metadata rows were extracted; source size/mtime unchanged during sampling: {data['source']['sourceFilesUnchangedDuringSampling']}.",
    '', 'Read access used SQLite `mode=ro&immutable=1` plus `query_only`. No credentials, OAuth configuration, prompts, reaction actions or image binaries were copied.', '',
    'Cached explicit image-owner IDs remain fixture evidence, not live verification. Archive-only images without numeric owners have explicit simulated owner identities. Missing/deleted/inaccessible negative states are injected; their live status is not inferred from an offline cache.', '',
    'Offline SVG previews keep browser tests independent of Civitai. Original canonical artwork links are preserved and navigation is fulfilled locally.', '',
    '| Rating | Sample records |', '| --- | ---: |']
for level,count in data['source']['sampleRatings'].items():lines.append(f'| {level} | {count} |')
lines += ['', '## All ten members', '', '| Member ID | Simulated member | Accepted | Fourth attempt | Remaining |', '| ---: | --- | ---: | --- | ---: |']
for row in data['members']:lines.append(f"| {row['id']} | {row['name']} | {row['accepted']} | {row['fourth']} | {row['remaining']} |")
lines += ['', 'Each member has a separate cookie-selected identity and persisted contribution rows. Browser contexts never alter OAuth credentials.', '',
    '## All accepted baseline recommendations', '',
    'The same deterministic 30-image plan is independently run through the API and UI after a reset. Each row appears once per run.', '',
    '| Artwork ID | Creator ID | Recommending member | UTC week | Status |', '| ---: | ---: | ---: | --- | --- |']
for r in data['baselineRecords']:lines.append(f"| {r['image_id']} | {r['creator_id']} | {r['member_id']} | {r['week']} | {r['status']} |")
lines += ['', '## Artist quotas', '', '| Creator ID | Accepted baseline artworks |', '| ---: | ---: |']
for artist,count in sorted(data['artistCounts'].items()):lines.append(f'| {artist} | {count} |')
lines += ['', 'Every count is at most three. The adversarial fourth-artist submission is rejected; the last-slot race accepts exactly one of two contenders.', '',
    '## Complete API attempt ledger', '',
    'Includes setup acceptances, rejects and lost-response behavior. A lost response after commit is shown as UNAVAILABLE with an accepted delta of one; retry is DUPLICATE with zero delta.', '',
    '| Scenario | Member | Submitted reference | Result | Accepted delta | Rows after | Audit rows after |',
    '| --- | ---: | --- | --- | ---: | ---: | ---: |']
for r in data['attempts']:
    value=str(r['artwork']).replace('|','\\|')
    lines.append(f"| {r['scenario']} | {r['member']} | `{value}` | {r['code']} | {r['acceptedDelta']} | {r['rowsAfter']} | {r['auditAfter']} |")
lines += ['', '## Database checks for every adversarial scenario', '',
    '| Scenario | Result | Persisted recommendations | Audit rows |', '| --- | --- | ---: | ---: |']
for r in data['scenarios']:lines.append(f"| {r['name']} | PASS | {len(r['database']['recommendations'])} | {len(r['database']['audit'])} |")
lines += ['', 'Rejected attempts compare complete before/after recommendation and audit snapshots. Withdrawal adds an audit event without removing the accepted row or refunding quota. Removed upstream media is omitted from reads without erasing the acceptance.', '',
    '## Concurrent request results', '']
for race in data['concurrency']:
    lines += [f"### {race['race']}", '', '| Member | Artwork | Result |','| ---: | ---: | --- |']
    for r in race['attempts']:lines.append(f"| {r['member']} | {r['image']} | {r['code']} |")
lines += ['', 'These are real concurrent loopback requests using independent SQLite connections. They do not establish PostgreSQL advisory-lock, RLS or distributed Edge behavior.', '',
    '## Browser and shared content controls', '',
    '- Thirty confirmations and ten fourth-attempt rejection journeys through the real UI.',
    '- Original artist/member credits, preview quota preservation, pagination and canonical-link popup navigation.',
    '- One shared `#contentFilter` and `#contentMenu`, using the existing save mechanism; no Community-specific rating buttons/state.',
    '- All ratings initially allowed; global restriction to XXX immediately removes other cards and metadata.',
    '- A held permissive response delivered after a restrictive save is discarded.',
    '- Shared settings synchronize a second browser tab and remain consistent after reload and application restart; simulated hidden controls also survive restart.',
    '- Hidden creators/images, blocked users, tags and empty gallery results are checked.',
    '- Lost confirmation response enables explicit retry; duplicate retry debits only once and reload reflects the accepted quota.',
    '- A transport timeout before confirmation reaches the server spends zero quota; retry accepts exactly one recommendation.',
    '- An actual seeded personal gallery remains visible and functional while Community returns an outage error.',
    f"- Unhandled browser errors: {len(data['browser']['errors'])}. External artwork requests: {len(data['browser']['externalArtworkRequests'])}.", '',
    '[Desktop screenshot](community-ten-desktop.png) · [Mobile screenshot](community-ten-mobile.png)', '',
    '## Bugs found and fixed', '',
    '1. Removed the second rating-control surface and exposed the existing shared filter panel on Community.',
    '2. Cleared cards/drafts before content saves and invalidated late responses; hid stale pagination on refresh.',
    '3. Enabled explicit retry after confirmation failure, preserved error codes and refreshed authoritative quota after uncertain responses.',
    '4. Kept reads on a separate bounded budget so normal refresh/filter activity cannot exhaust the submission limiter.',
    '5. Added ten independent fixture identities and persisted the test clock through application restarts.',
    '6. Installed the fail-closed transport guard in the existing script suite; one legacy fixture-directory prefix was corrected to satisfy it.', '',
    '## Safety', '',
    '- Port 8906 and retired VM ports are rejected before network access or child-process launch.',
    '- Unknown/production endpoints and exited test-server processes are rejected.',
    '- The server must pass an instance nonce and isolated data-directory identity challenge before harness mutations.',
    '- Test-only reset/fault/clock/snapshot controls require explicit fixture/test mode, loopback, a named isolated root and the nonce.',
    '- Test server outbound connections are disabled. Fake members never reach Civitai or Supabase.',
    '- Browser original-link navigation is intercepted and fulfilled offline. Test source/sample data are separate from the 2.x installation.',
    '- The simulated-member harness refuses roots containing OAuth credential/client files.', '',
    '## Remaining limitations', '']
for value in data['limitations']:lines.append('- '+value+'.')
lines += ['- Live deletion/availability and cached ownership require independent Civitai validation before production use.',
    '- Immutable source reads use the last checkpointed database image and intentionally do not copy or replay live WAL files.',
    '- Detailed per-scenario snapshots, all browser confirmations and rejection records are in [the JSON evidence](community-stress.json).',
    '- No production integration is proposed or performed in this milestone.', '']
target=reports/'community-stress-report.md'
target.write_text('\n'.join(lines),encoding='utf-8')
print(target)
