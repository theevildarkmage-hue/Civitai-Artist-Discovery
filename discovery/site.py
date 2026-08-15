"""Civitai Red endpoints and content-rating policy shared by the app."""

SITE_ORIGIN = "https://civitai.red"
API_URL = f"{SITE_ORIGIN}/api/v1/images"
DEFAULT_CONTENT_RATING = "Soft"  # PG and PG-13.
CONTENT_RATINGS = ("Soft", "Mature", "X")
RATING_RANK = {"Soft": 1, "Mature": 2, "X": 3}
BROWSING_LEVELS = (1, 2, 4, 8, 16)
DEFAULT_BROWSING_LEVELS = (1, 2)
LEVEL_LABELS = {1: "PG", 2: "PG-13", 4: "R", 8: "X", 16: "XXX"}


def content_rating(value: object) -> str:
    rating = str(value or DEFAULT_CONTENT_RATING)
    if rating not in CONTENT_RATINGS:
        raise ValueError("Content rating must be Soft, Mature, or X")
    return rating


def browsing_levels(value: object) -> tuple[int, ...]:
    if not isinstance(value, (list, tuple, set)):
        raise ValueError("Browsing levels must be a non-empty list")
    try:
        selected = {int(level) for level in value}
    except (TypeError, ValueError) as error:
        raise ValueError("Browsing levels must be PG, PG-13, R, X, or XXX") from error
    if not selected or not selected.issubset(BROWSING_LEVELS):
        raise ValueError("Select at least one valid browsing level")
    return tuple(level for level in BROWSING_LEVELS if level in selected)


def levels_for_rating(rating: object) -> tuple[int, ...]:
    return {"Soft": (1, 2), "Mature": (1, 2, 4), "X": BROWSING_LEVELS}[content_rating(rating)]


def rating_for_levels(levels: object) -> str:
    selected = browsing_levels(levels)
    return "X" if any(level in selected for level in (8, 16)) else \
        ("Mature" if 4 in selected else "Soft")


def image_url(image_id: int) -> str:
    return f"{SITE_ORIGIN}/images/{int(image_id)}"


def profile_url(username: str) -> str:
    import urllib.parse
    return f"{SITE_ORIGIN}/user/{urllib.parse.quote(username)}"
