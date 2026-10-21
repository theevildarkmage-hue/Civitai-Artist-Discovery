"""Request-scoped gallery projection; never rewrites the persisted artist index."""

from collections import defaultdict
from datetime import datetime
import json

from .models import base_labels


def media_type(value):
    if value not in ("all", "image", "video"):
        raise ValueError("Media type must be all, image, or video")
    return value


class GallerySelection:
    def __init__(self, archive, key, models=(), media="all", hidden=()):
        self.archive, self.key = archive, key
        self.models, self.media = tuple(sorted(set(models))), media_type(media)
        self.signature = (self.models, self.media, tuple(archive.visible_levels))
        self.excluded = set(hidden or ())
        self.groups = defaultdict(list)
        levels = archive.visible_levels
        with archive.connect() as db:
            rows = db.execute(
                """SELECT i.id,i.username_key,i.username,i.created_at,i.stats,
                i.base_model,i.type,COALESCE(i.browsing_level,CASE COALESCE(i.nsfw_level,'None')
                WHEN 'None' THEN 1 WHEN 'Soft' THEN 2 WHEN 'Mature' THEN 4 WHEN 'X' THEN 8 END) level
                FROM block_images b JOIN images i ON i.id=b.image_id WHERE b.block_key=?
                ORDER BY i.created_at DESC,i.id DESC""",
                (key,),
            )
            for row in rows:
                if (
                    row["id"] in self.excluded
                    or row["level"] not in levels
                    or (self.media != "all" and row["type"] != self.media)
                    or (
                        self.models
                        and not (
                            row["base_model"] in self.models
                            or set(base_labels(row["base_model"])) & set(self.models)
                        )
                    )
                ):
                    self.excluded.add(row["id"])
                    continue
                item = dict(row)
                item["reactions"] = max(
                    0, int(json.loads(item["stats"]).get("reactionCount") or 0)
                )
                self.groups[row["username_key"]].append(item)
        self.covers = {
            name: max(
                items,
                key=lambda item: (item["reactions"], item["created_at"], item["id"]),
            )["id"]
            for name, items in self.groups.items()
        }
        self.totals = {
            name: sum(item["reactions"] for item in items)
            for name, items in self.groups.items()
        }
        self.order = sorted(
            self.groups,
            key=lambda name: (
                -self.totals[name],
                -datetime.fromisoformat(
                    self.groups[name][0]["created_at"].replace("Z", "+00:00")
                ).timestamp(),
                name,
            ),
        )

    def __getattr__(self, name):
        return getattr(self.archive, name)

    def day_artist_keys(self, value):
        return [
            {
                "key": name,
                "username": self.groups[name][0]["username"],
                "imageCount": len(self.groups[name]),
                "rank": rank,
                "representativeId": self.covers[name],
            }
            for rank, name in enumerate(self.order)
        ]

    def effective_representative_ids(self, value, excluded_images=None):
        return dict(self.covers)

    def creator_reaction_totals(self, value, excluded_images=None):
        return dict(self.totals)

    def creator_quality_scores(self, value, excluded_images=None):
        return self.archive.creator_quality_scores(value, self.excluded)

    def artists_page(
        self,
        value,
        offset,
        limit,
        pinned_username=None,
        order=None,
        representatives=None,
        excluded_images=None,
    ):
        names = (self.order if order is None else order)[offset : offset + limit]
        result = []
        with self.archive.connect() as db:
            for name in names:
                if name not in self.groups:
                    continue
                image = db.execute(
                    "SELECT * FROM images WHERE id=?", (self.covers[name],)
                ).fetchone()
                items = self.groups[name]
                result.append(
                    {
                        "username": items[0]["username"],
                        "imageCount": len(items),
                        "representativeIndex": next(
                            i
                            for i, item in enumerate(items)
                            if item["id"] == image["id"]
                        ),
                        "representative": self.archive._row_item(image),
                        "images": [self.archive._row_item(image)],
                    }
                )
        return result

    def day_summary(self, value):
        summary = self.archive.day_summary(value)
        return {
            **summary,
            "artistCount": len(self.groups),
            "imageCount": sum(map(len, self.groups.values())),
        }

    def artist_images(self, value, username, models=None, excluded_images=None):
        wanted = {item["id"] for item in self.groups.get(username.casefold(), [])}
        # Fetch the creator's rows only; preserve the established date/rating contract.
        return [
            item
            for item in self.archive.artist_images(
                value, username, excluded_images=excluded_images
            )
            if item["id"] in wanted
        ]

    def creators_with_visible_images(self, value, excluded_images=None):
        return set(self.groups)

    def day_models(self, value):
        counts = defaultdict(int)
        for items in self.groups.values():
            for item in items:
                for name in base_labels(item["base_model"]):
                    counts[name] += 1
        return [
            {"model": name, "images": count}
            for name, count in sorted(
                counts.items(), key=lambda pair: (-pair[1], pair[0])
            )
        ]
