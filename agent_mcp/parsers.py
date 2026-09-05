from __future__ import annotations

import json
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:
    tomllib = None

MAX_NESTING = 32
MAX_CONTAINER_ITEMS = 4096


class ParseFailure(ValueError):
    pass


def _bounded_shape(value: Any, depth: int = 0) -> None:
    if depth > MAX_NESTING:
        raise ParseFailure("too-deep")
    if isinstance(value, dict):
        if len(value) > MAX_CONTAINER_ITEMS:
            raise ParseFailure("too-many-items")
        for key, child in value.items():
            if not isinstance(key, str) or len(key) > 1024:
                raise ParseFailure("invalid-key")
            _bounded_shape(child, depth + 1)
    elif isinstance(value, list):
        if len(value) > MAX_CONTAINER_ITEMS:
            raise ParseFailure("too-many-items")
        for child in value:
            _bounded_shape(child, depth + 1)
    elif isinstance(value, str) and len(value) > 1_048_576:
        raise ParseFailure("oversized-string")


def parse_json(data: bytes) -> dict[str, Any]:
    def unique_members(pairs):
        result = {}
        for key, child in pairs:
            if key in result:
                raise ParseFailure("duplicate-json-key")
            result[key] = child
        return result

    def invalid_number(value):
        raise ParseFailure("nonfinite-json-number")

    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=unique_members,
                           parse_constant=invalid_number)
    except (ValueError, RecursionError) as error:
        raise ParseFailure("invalid-json") from error
    if not isinstance(value, dict):
        raise ParseFailure("root-not-object")
    _bounded_shape(value)
    return value

def strip_jsonc(text: str) -> str:
    output: list[str] = []
    index = 0
    quoted = False
    escaped = False
    while index < len(text):
        char = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if quoted:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            index += 1
            continue
        if char == '"':
            quoted = True
            output.append(char)
            index += 1
            continue
        if char == "/" and following == "/":
            output.extend("  ")
            index += 2
            while index < len(text) and text[index] not in "\r\n":
                output.append(" ")
                index += 1
            continue
        if char == "/" and following == "*":
            output.extend("  ")
            index += 2
            closed = False
            while index < len(text):
                if index + 1 < len(text) and text[index:index + 2] == "*/":
                    output.extend("  ")
                    index += 2
                    closed = True
                    break
                output.append("\n" if text[index] == "\n" else " ")
                index += 1
            if not closed:
                raise ParseFailure("invalid-jsonc")
            continue
        output.append(char)
        index += 1

    cleaned = output
    quoted = False
    escaped = False
    index = 0
    while index < len(cleaned):
        char = cleaned[index]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char == ",":
            lookahead = index + 1
            while lookahead < len(cleaned) and cleaned[lookahead].isspace():
                lookahead += 1
            if lookahead < len(cleaned) and cleaned[lookahead] in "}]":
                cleaned[index] = " "
        index += 1
    return "".join(cleaned)


def parse_jsonc(data: bytes) -> dict[str, Any]:
    try:
        text = data.decode("utf-8")
    except UnicodeError as error:
        raise ParseFailure("invalid-utf8") from error
    return parse_json(strip_jsonc(text).encode("utf-8"))


def parse_toml(data: bytes) -> dict[str, Any]:
    if tomllib is None:
        raise ParseFailure("tomllib-unavailable")
    try:
        value = tomllib.loads(data.decode("utf-8"))
    except (ValueError, RecursionError) as error:
        raise ParseFailure("invalid-toml") from error
    if not isinstance(value, dict):
        raise ParseFailure("root-not-object")
    _bounded_shape(value)
    return value
