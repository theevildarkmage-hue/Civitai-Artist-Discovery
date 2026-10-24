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
    if isinstance(serialized, str) and len(serialized.encode("utf-8")) > 1_048_576:
        raise ValueError("Flattened payload exceeds size limit")
    values = json.loads(serialized) if isinstance(serialized, str) else serialized
    if not isinstance(values, list):
        return values
    if not values or len(values) > 100_000:
        raise ValueError("Invalid flattened payload size")
    hydrated: dict[int, object] = {}
    active = set()
    visits = 0

    def hydrate(pointer, depth=0):
        nonlocal visits
        visits += 1
        if depth > 64 or visits > 200_000:
            raise ValueError("Flattened payload exceeds traversal limit")
        if type(pointer) is not int:
            return pointer
        if pointer < 0:
            if pointer not in _SENTINELS:raise ValueError("Invalid flattened sentinel")
            return _SENTINELS[pointer]
        if pointer >= len(values) or pointer in active:
            raise ValueError("Invalid or cyclic flattened pointer")
        if pointer in hydrated:
            return hydrated[pointer]
        active.add(pointer)
        try:
            return hydrate_value(pointer, depth)
        finally:
            active.remove(pointer)

    def hydrate_value(pointer, depth):
        value = values[pointer]
        if not isinstance(value, (list, dict)):
            hydrated[pointer] = value
            return value
        if isinstance(value, list):
            if value and isinstance(value[0], str):
                kind = value[0]
                if kind in ("Date", "BigInt") and len(value)!=2:
                    raise ValueError("Invalid flattened tagged value")
                if kind=="Map" and len(value)%2!=1:
                    raise ValueError("Invalid flattened Map")
                if kind == "Date":
                    hydrated[pointer] = value[1]
                elif kind == "Set":
                    hydrated[pointer] = [hydrate(item, depth+1) for item in value[1:]]
                elif kind == "Map":
                    flat = [hydrate(item, depth+1) for item in value[1:]]
                    hydrated[pointer] = dict(zip(flat[::2], flat[1::2]))
                elif kind == "BigInt":
                    hydrated[pointer] = int(value[1])
                else:
                    hydrated[pointer] = hydrate(value[1], depth+1) if len(value) > 1 else kind
                return hydrated[pointer]
            array: list = [None] * len(value)
            hydrated[pointer] = array
            for position, item in enumerate(value):
                if item != HOLE:
                    array[position] = hydrate(item, depth+1)
            return array
        obj: dict = {}
        hydrated[pointer] = obj
        for key, item in value.items():
            obj[key] = hydrate(item, depth+1)
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
