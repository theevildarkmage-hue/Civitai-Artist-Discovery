"""Decoder for the flattened payload Civitai returns from some tRPC queries.

Most procedures answer with the superjson shape `{"json": ...}`. Heavier ones such as
`image.getInfinite` answer with a string holding a flattened array instead: index 0 is
the root, integers inside containers point at other indices, small negatives are
sentinels, and an array whose first element is a string is a typed wrapper like
`["Date", "..."]`. Sharing one unwrap step keeps callers from caring which they get.
"""

from __future__ import annotations

import json
import math

UNDEFINED = -1
HOLE = -2

_SENTINELS = {UNDEFINED: None, HOLE: None, -3: math.nan, -4: math.inf, -5: -math.inf, -6: -0.0}


def parse_flat(serialized: str | list):
    values = json.loads(serialized) if isinstance(serialized, str) else serialized
    if not isinstance(values, list):
        return values
    hydrated: dict[int, object] = {}

    def hydrate(pointer):
        if not isinstance(pointer, int):
            return pointer
        if pointer < 0:
            return _SENTINELS.get(pointer)
        if pointer in hydrated:
            return hydrated[pointer]
        value = values[pointer]
        if not isinstance(value, (list, dict)):
            hydrated[pointer] = value
            return value
        if isinstance(value, list):
            if value and isinstance(value[0], str):
                kind = value[0]
                if kind == "Date":
                    hydrated[pointer] = value[1]
                elif kind == "Set":
                    hydrated[pointer] = [hydrate(item) for item in value[1:]]
                elif kind == "Map":
                    flat = [hydrate(item) for item in value[1:]]
                    hydrated[pointer] = dict(zip(flat[::2], flat[1::2]))
                elif kind == "BigInt":
                    hydrated[pointer] = int(value[1])
                else:
                    hydrated[pointer] = hydrate(value[1]) if len(value) > 1 else kind
                return hydrated[pointer]
            array: list = [None] * len(value)
            hydrated[pointer] = array
            for position, item in enumerate(value):
                if item != HOLE:
                    array[position] = hydrate(item)
            return array
        obj: dict = {}
        hydrated[pointer] = obj
        for key, item in value.items():
            obj[key] = hydrate(item)
        return obj

    return hydrate(0)


def unwrap_result(result: object):
    """Return the payload from a tRPC `result` object in either serialization."""
    if not isinstance(result, dict):
        return result
    data = result.get("data")
    if isinstance(data, str):
        return parse_flat(data)
    if isinstance(data, dict) and "json" in data:
        return data["json"]
    return data
