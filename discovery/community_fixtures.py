"""Development-only metadata and identities; excluded from frozen builds."""
from .community import Member, Artwork, CommunityError, positive_id
import sys
if getattr(sys,"frozen",False):raise RuntimeError("Fixture identities are excluded from production")

class FixtureVerifier:
    """Explicit synthetic universe. Never accepts real OAuth credentials."""
    def __init__(self, *, development=False, manifest=None):
        if not development:
            raise RuntimeError("Fixture identity verification requires explicit development mode")
        self.members = {f"fixture:{n}": Member(n, f"DemoMember{n:02d}") for n in range(1, 11)}
        self.artworks = {n: Artwork(n, 101 + (n - 1001) // 5, f"DemoArtist{1 + (n - 1001) // 5}",
                                   (1, 2, 4, 8, 16)[(n - 1001) % 5],
                                   f"/community-fixture.svg?id={n}",
                                   ("landscape",) if n % 2 else ("portrait",), (11,) if n % 2 else (12,))
                         for n in range(1001, 1061)}
        self.artworks[1099] = Artwork(1099, 1, "DemoMember1", 1, "/community-fixture.svg?id=1099")
        self.failure = None
        self.artwork_failures = {}
        self.hidden = {}
        self.removed = set()
        self.expire_during_artwork = False
        self.manifest = manifest
        if manifest:
            if manifest.get('developmentOnly') is not True or manifest.get('schemaVersion') != 1:
                raise RuntimeError('Invalid development metadata manifest')
            for row in manifest['artworks']:
                image = positive_id(row['id'])
                source = row.get('sourcePreview')
                preview = ''
                if source:
                    try:
                        from .cache import thumbnail_source
                        preview = thumbnail_source(source)
                    except ValueError:pass
                self.artworks[image] = Artwork(image, positive_id(row['creatorId']), row['username'],
                    row['browsingLevel'], preview,
                    tuple(t['name'].casefold() for t in row.get('tags', [])),
                    tuple(t['id'] for t in row.get('tags', []) if t.get('id')),
                    row.get('tagsComplete') is True or (row.get('source')=='reaction-metadata' and len(row.get('tags',[]))<30),
                    row.get('ownerEvidence')!='explicit-simulated-owner')
            for row in manifest.get('negativeCases', []):
                self.artwork_failures[row['id']] = ('UNVERIFIED_OWNER' if row['state'] in
                    ('missing_creator', 'unverified_owner') else 'INVALID_ARTWORK')
        self.initial_artworks = dict(self.artworks)
        self.initial_failures = dict(self.artwork_failures)

    def _check(self):
        if self.failure:
            raise CommunityError(self.failure, {"AUTH_EXPIRED": 401, "RATE_LIMITED": 429}.get(self.failure, 503))

    def member(self, token):
        self._check()
        if token not in self.members:
            raise CommunityError("AUTH_EXPIRED", 401)
        return self.members[token]

    def artwork(self, image_id):
        self._check()
        if hasattr(self, 'live_verifier'):
            return self.live_verifier.artwork(image_id)
        if self.expire_during_artwork:
            raise CommunityError('AUTH_EXPIRED', 401)
        if image_id in self.artwork_failures:
            raise CommunityError(self.artwork_failures[image_id], 400)
        if image_id not in self.artworks:
            raise CommunityError("INVALID_ARTWORK", 404)
        return self.artworks[image_id]
