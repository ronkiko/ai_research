"""Shared controller contract: actions contain buttons, never world coordinates."""
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class Action:
    right: bool = False
    jump: bool = False

    def __post_init__(self):
        if type(self.right) is not bool or type(self.jump) is not bool:
            raise ValueError('right and jump must be booleans')


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f'Duplicate field: {key}')
            result[key] = value
        return result

    def invalid(value):
        raise ValueError(f'Invalid JSON number: {value}')

    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid)


def fields(value, required, optional=()):
    if not isinstance(value, dict):
        raise ValueError('Expected a JSON object')
    missing = set(required) - value.keys()
    extra = value.keys() - set(required) - set(optional)
    if missing or extra:
        raise ValueError(f'Missing fields: {sorted(missing)}; unknown fields: {sorted(extra)}')
