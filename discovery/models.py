"""Conservative interpretation of archived base-model labels, without remote lookups."""

from functools import lru_cache
import json

# Only complete, identifiable labels may partition a legacy concatenated value.
# Unknown names remain intact; matching is never an arbitrary substring search.
KNOWN_BASE_MODELS = frozenset(
    (
        "Unknown",
        "SD 1.4",
        "SD 1.5",
        "SD 1.5 Hyper",
        "SD 2.0",
        "SD 2.1",
        "SDXL 0.9",
        "SDXL 1.0",
        "SDXL Turbo",
        "SDXL Lightning",
        "Pony",
        "Illustrious",
        "NoobAI",
        "Flux.1 D",
        "Flux.1 S",
        "Flux.1 Krea",
        "Flux.1 Kontext",
        "Anima",
        "ZImageTurbo",
        "MiniMax H3",
        "Krea 2",
        "YuE2",
        "YuE2-3B",
        "LTXV",
        "LTXV 2.3",
        "LTXV 2.5",
        "Seedance",
        "Grok",
        "Nano Banana",
        "Veo 3",
        "Hunyuan Video",
        "Wan Video 14B i2v 480p",
        "Wan Video 14B i2v 720p",
        "Wan Video 14B t2v",
        "Wan Video 2.2 I2V-A14B",
        "Wan Video 2.2 T2V-A14B",
        "Wan Video 2.2 TI2V-5B",
        "Wan Video 2.5 I2V",
        "Wan Video 2.5 T2V",
        "Wan Video 2.7",
        "Wan Video 3.0",
    )
)


@lru_cache(maxsize=4096)
def base_labels(value: str) -> tuple[str, ...]:
    value = str(value or "Unknown").strip() or "Unknown"
    if " | " in value:
        return tuple(dict.fromkeys(value.split(" | ")))
    if value in KNOWN_BASE_MODELS or len(value) > 500:
        return (value,)

    @lru_cache(maxsize=None)
    def partitions(offset):
        if offset == len(value):
            return [()]
        result = []
        for name in KNOWN_BASE_MODELS:
            if value.startswith(name, offset):
                result.extend((name, *tail) for tail in partitions(offset + len(name)))
                if len(result) > 1:
                    return result[:2]
        return result

    choices = partitions(0)
    return tuple(dict.fromkeys(choices[0])) if len(choices) == 1 else (value,)


def normalize_base_model(value) -> str:
    if isinstance(value, (list, tuple)):
        return (
            " | ".join(dict.fromkeys(str(name) for name in value if name)) or "Unknown"
        )
    return " | ".join(base_labels(str(value or "Unknown")))


def matches_models(raw, requested_json):
    requested = set(json.loads(requested_json))
    return int(raw in requested or bool(set(base_labels(raw)) & requested))
