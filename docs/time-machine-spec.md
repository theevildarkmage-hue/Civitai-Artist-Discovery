# Time Machine — specification

Status: **proposed, not built**. Measurements taken 2026-08-30 against the public v1 API.

## What it is

A tab showing the **oldest image you have not yet seen** from each creator you follow —
one card per creator. Viewing a card advances that creator's pointer, so the next refresh
shows their next-oldest image, and so on until their history is exhausted.

## Why it is worth building

Every limitation this app fights comes from paging Civitai's **global** feed: the ~49,000
offset ceiling, the ~2-day reachable window, days expiring, the whole reason auto-capture
exists. A per-creator query is a different feed — short, and `sort=Oldest` starts at the
far end of it.

Measured: `@Shorgall`'s oldest images are from **2024-09-11**, reachable right now, in one
request. The daily archive structurally cannot reach that and never will.

So this gives the app deep history it cannot otherwise have, from the endpoint it is most
clearly entitled to use.

## API mechanics (measured, not assumed)

| question | answer |
| --- | --- |
| `username` + `sort=Oldest` | works; returns that creator's earliest images first |
| `limit=200` per creator | works — 194 images in one request, 2.34s, 12 KB |
| several creators per request | **no**: `username=a,b` returns HTTP 400. One creator per request |
| deep paging | `metadata.nextCursor` present; the per-creator feed is short, so the global 49,000 ceiling is not a factor |
| total image count | **not returned.** `metadata` carries only `nextCursor` and `nextPage` |
| follow list | already cached in `data/following.json`, ~5 KB, no per-day cost |

All parameters used are documented on `/api/v1/images`, the public API named in Civitai's
Terms of Service 11.4. Nothing here uses a borrowed credential or an undocumented service.

## Cost model

Follow list is **483 creators** (resolved below).

| operation | requests | notes |
| --- | --- | --- |
| resolve 483 ids to usernames | 5 | `user.getCreator` batches 100 per request |
| prime every creator | 483 | one 200-image page each, ~2.3s → **25-40 min** |
| yield from that | ~96,000 images | ~200 per creator |
| a full pass of the tab | 0 | one image per creator, served from cache |
| refill one exhausted creator | 1 | resumes from its stored cursor |

At one image per creator per pass, priming buys roughly **200 passes** before any creator
needs refetching. The per-request cost is the same as the daily collector; the difference
is that it is paid once per creator rather than once per day.

## Storage

Two new tables. Keyed by **username**, not by follow state, so progress survives an
unfollow and re-follow.

```
creator_history(username, image_id, created_at, url, browsing_level,
                post_id, width, height, base_model, stats, position)
creator_progress(username, next_position, cursor, exhausted, primed_at, updated_at)
```

`position` is the index within that creator's oldest-first ordering, so advancing is a
pointer move rather than a re-query. `cursor` is Civitai's own, stored verbatim, for
refilling. `exhausted` marks a creator whose history has been seen to the end.

This is separate from the daily archive: different lifecycle, different reachability, and
nothing about it should be able to corrupt a collected day.

## Behaviour

1. The tab lists one card per followed creator: their oldest image at `next_position`.
2. Marking a card seen increments that creator's `next_position`.
3. When `next_position` reaches the end of the cached page, refill from `cursor`.
4. When a refill returns nothing, set `exhausted` and show the creator as caught up.
5. Content rating filters through the existing `visible_levels`, as every other view does.

## Open questions — need answers before building

1. ~~**291 vs 483.**~~ **Resolved.** They are different representations, not the same list
   in two states: `followed_creators` in `taste.sqlite3` holds **483 creator ids**, while
   `data/following.json` holds **291 usernames**. Neither is stale — they overlap by zero
   because one stores integers and the other strings.

   `followed_creators` is authoritative. The API needs a username, so the ids must be
   resolved first: `user.getCreator` accepts `{"id"}` and batches 100 per request, so 483
   ids costs 5 requests. Worth understanding separately why `following.json` holds only
   291 of the 483 before relying on it for anything else.
2. **Prime up front or lazily?** 483 requests is 25-40 minutes. Up front makes the tab
   instant afterwards; lazy makes the first visit slow but spreads the cost.
3. **What counts as "seen"?** The existing seen-creators logic marks a card viewed. Is
   scrolling past enough, or must the image be opened? This decides how fast a user
   burns through history.
4. **No progress indicator is possible.** The API returns no total, so "12 of 847" cannot
   be shown without paging a creator's whole history first. Is "oldest first, keep going"
   enough, or is the count worth paying for?
5. **Creators with no images** at the selected content rating — hide, or show as empty?

## Build size

Comparable to the auto-capture work in this branch.

| part | estimate |
| --- | --- |
| `discovery/timemachine.py` — priming, refill, pointer, exhaustion | ~250 lines |
| schema + migrations for two tables | ~40 lines |
| endpoints: list, advance, refill, status | ~60 lines in `server.py` |
| tab, card grid, empty and caught-up states | ~150 lines in `static/` |
| tests: pointer/exhaustion, refill, cost, UI | 3 files |

Call it a **medium build** — a day's focused work, most of it in the pointer and
exhaustion logic rather than the API calls, which are simple.

## What this does not do

It does not help the daily archive. The two are independent: this reaches deep into
individual creators, the archive reaches wide across a single day. Neither substitutes for
the other, and the ~2-day window on daily collection is unaffected.
