"""Civitai Red endpoints and content-rating policy shared by the app."""

SITE_ORIGIN = "https://civitai.red"
API_URL = f"{SITE_ORIGIN}/api/v1/images"
DEFAULT_CONTENT_RATING = "Soft"  # PG and PG-13.
CONTENT_RATINGS = ("Soft", "Mature", "X")
RATING_RANK = {"Soft": 1, "Mature": 2, "X": 3}


def content_rating(value: object) -> str:
    rating = str(value or DEFAULT_CONTENT_RATING)
    if rating not in CONTENT_RATINGS:
        raise ValueError("Content rating must be Soft, Mature, or X")
    return rating


def image_url(image_id: int) -> str:
    return f"{SITE_ORIGIN}/images/{int(image_id)}"


def profile_url(username: str) -> str:
    import urllib.parse
    return f"{SITE_ORIGIN}/user/{urllib.parse.quote(username)}"
