from __future__ import annotations

import re
from typing import Any

BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")
TABLE_HEADER = re.compile(r"^\s*\[")


class TomlWriteFailure(ValueError):
    pass


def quote_string(value: str) -> str:
    escaped = []
    for character in value:
        if character == '"':
            escaped.append('\\"')
        elif character == "\\":
            escaped.append("\\\\")
        elif character == "\n":
            escaped.append("\\n")
        elif character == "\t":
            escaped.append("\\t")
        elif character == "\r":
            escaped.append("\\r")
        elif ord(character) < 0x20 or character == "\x7f":
            escaped.append(f"\\u{ord(character):04X}")
        else:
            escaped.append(character)
    return '"' + "".join(escaped) + '"'


def render_key(key: str) -> str:
    return key if BARE_KEY.match(key) else quote_string(key)


def render_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return quote_string(value)
    if isinstance(value, list):
        return "[" + ", ".join(render_value(item) for item in value) + "]"
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return "{ " + ", ".join(render_key(key) + " = " + render_value(item) for key, item in value.items()) + " }"
    raise TomlWriteFailure("unsupported-value")


def render_server_table(name: str, values: dict[str, Any]) -> str:
    header = f"[mcp_servers.{render_key(name)}]"
    lines = [header]
    nested: list[tuple[str, dict[str, Any]]] = []
    for key, value in values.items():
        if isinstance(value, dict):
            if value and all(isinstance(item, str) for item in value.values()):
                nested.append((key, value))
                continue
        lines.append(f"{render_key(key)} = {render_value(value)}")
    for key, table in nested:
        if not table:
            continue
        lines.append("")
        lines.append(f"[mcp_servers.{render_key(name)}.{render_key(key)}]")
        for inner_key, inner_value in table.items():
            lines.append(f"{render_key(inner_key)} = {render_value(inner_value)}")
    return "\n".join(lines) + "\n"


def header_pattern(name: str) -> re.Pattern[str]:
    forms = [re.escape(name)] if BARE_KEY.match(name) else []
    forms.append(re.escape(quote_string(name)))
    forms.append(re.escape("'" + name + "'"))
    joined = "|".join(forms)
    return re.compile(r"^\s*\[\s*mcp_servers\s*\.\s*(?:" + joined + r")\s*\]\s*(?:#.*)?$")


def subtable_pattern(name: str) -> re.Pattern[str]:
    forms = [re.escape(name)] if BARE_KEY.match(name) else []
    forms.append(re.escape(quote_string(name)))
    forms.append(re.escape("'" + name + "'"))
    joined = "|".join(forms)
    return re.compile(r"^\s*\[\s*mcp_servers\s*\.\s*(?:" + joined + r")\s*\.")


def locate_server_block(text: str, name: str) -> tuple[int, int] | None:
    lines = text.splitlines(keepends=True)
    header = header_pattern(name)
    subtable = subtable_pattern(name)
    starts = [index for index, line in enumerate(lines) if header.match(line)]
    if not starts:
        return None
    if len(starts) > 1:
        raise TomlWriteFailure("ambiguous-table")
    start = starts[0]
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if TABLE_HEADER.match(line) and not subtable.match(line):
            end = index
            break
    while end > start + 1 and lines[end - 1].strip() == "":
        end -= 1
    return start, end


def remove_server_block(text: str, name: str) -> str:
    located = locate_server_block(text, name)
    if located is None:
        raise TomlWriteFailure("table-not-found")
    start, end = located
    lines = text.splitlines(keepends=True)
    while start > 0 and lines[start - 1].strip() == "":
        start -= 1
    remaining = lines[:start] + lines[end:]
    result = "".join(remaining)
    if result and not result.endswith("\n"):
        result += "\n"
    return result


def append_server_block(text: str, block: str) -> str:
    if text and not text.endswith("\n"):
        text += "\n"
    if text and not text.endswith("\n\n"):
        text += "\n"
    return text + block
