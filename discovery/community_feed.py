"""Lightweight accepted recommendation contract; no artwork processing."""
from datetime import datetime,timedelta,timezone
from .community import CommunityError,positive_id
def decode(row,now=None):
 try:
  image,creator=positive_id(row['imageId']),positive_id(row['creatorId'])
  stamp=datetime.fromisoformat(row['submittedAt'].replace('Z','+00:00'))
  now=now or datetime.now(timezone.utc)
  # The authenticated server owns acceptance time and feed eligibility. Its clock
  # can be ahead of this PC: a newly accepted row must not invalidate the page.
  # Keep timestamp validation and the defensive local expiration bound, without
  # treating the PC clock as an authority on when server acceptance may occur.
  if stamp.tzinfo is None or now>=stamp+timedelta(hours=168):raise ValueError()
  if not isinstance(row['recommender'],str) or not 1<=len(row['recommender'])<=200:raise ValueError()
  return image,creator,(stamp+timedelta(hours=168)).timestamp()
 except (KeyError,ValueError,TypeError,AttributeError,OverflowError):raise CommunityError('UNAVAILABLE',503) from None
