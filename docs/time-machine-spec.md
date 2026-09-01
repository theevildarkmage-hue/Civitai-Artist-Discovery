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

## Decisions (settled 2026-08-30)

1. **Lazy, with a visible background prime.** On first visit the tab starts priming every
   followed creator — one 200-image page each — and renders cards as their data lands.
   Purely lazy fetching cannot keep up with fast scrolling at ~2.3s per request, so the
   prime runs regardless of where the user has scrolled to; laziness only decides what is
   *rendered* first, not what is *fetched*.

2. **Seen means scrolled past**, reusing the existing dimmed-card rule rather than
   inventing a second one. `seenObserver` in `static/app.js` already marks a card seen when
   it is scrolled *away* after dwelling, not merely for having been on screen, and
   `taste.mark_seen` persists it. The Time Machine advances a creator's pointer on the same
   signal, so "seen" means one thing throughout the app.

3. **The progress bar counts creators, not images.** Each creator whose first page has been
   fetched moves the bar one notch; all followed creators primed is 100%. The denominator
   is the follow count, which is known.

   This matters because a per-creator *history* bar is not possible: the images endpoint
   returns no total, `user.getCreator` carries only model-upload counts, and paging a
   prolific creator to their end would cost hundreds of requests on its own (the heaviest
   creator in the archive averages 12,551 images per 27 days — an estimated ~1,700 pages
   of back-catalogue). Counting creators avoids the problem entirely.

4. **Creators with nothing at the selected content rating are hidden**, not shown empty.

### Consequences worth knowing

The median creator posts ~10 images per 27 days, so their entire back-catalogue fits in
the single page the prime already fetches — most creators are complete after priming and
never need a refill. The cost is concentrated in the heavy tail: the top 10% would need
roughly 15 pages each, and the heaviest handful far more. Refills are per-creator and
on-demand, so that cost is only paid for creators the user actually reads to the end of.

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
