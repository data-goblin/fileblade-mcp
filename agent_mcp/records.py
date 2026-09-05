from copy import deepcopy
import datetime
import hashlib
import json
from typing import Any

from .parsers import parse_toml
from .tomlwrite import TomlWriteFailure, append_server_block, locate_server_block


def fingerprint(value: Any) -> str:
    def typed(item):
        if isinstance(item, dict):
            return ["object", [[key, typed(child)] for key, child in sorted(item.items())]]
        if isinstance(item, list):
            return ["array", [typed(child) for child in item]]
        if isinstance(item, (datetime.datetime, datetime.date, datetime.time)):
            return [type(item).__name__, item.isoformat()]
        if isinstance(item, float):
            return ["float", item.hex()]
        return [type(item).__name__, item]
    encoded = json.dumps(typed(value), ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def text_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def container_path(definition, document: dict) -> list[str]:
    if definition.agent == "opencode":
        return ["mcp", "servers"] if definition.source_kind.startswith("opencode-v2-") else ["mcp"]
    if definition.agent == "pi" or definition.source_kind == "copilot-project":
        return ["mcpServers"] if isinstance(document.get("mcpServers"), dict) else []
    return ["mcpServers"]


def container(document: dict, path: list[str], create: bool = False) -> dict:
    current = document
    for key in path:
        if create and key not in current:
            current[key] = {}
        current = current.get(key)
        if not isinstance(current, dict):
            raise ValueError("the source container changed; refresh and retry")
    return current


def detach_json(definition, document: dict) -> dict:
    path = container_path(definition, document)
    mapping = container(document, path)
    name = definition.raw_name
    if name not in mapping or fingerprint(mapping[name]) != fingerprint(definition.raw_config):
        raise ValueError("the source definition changed; refresh and retry")
    position = list(mapping).index(name)
    raw = mapping.pop(name)
    return {"kind": "json", "container": path, "name": name, "raw": raw,
            "position": position, "definition": fingerprint(raw)}


def attach_json(document: dict, record: dict) -> bool:
    path, name, raw = record.get("container"), record.get("name"), record.get("raw")
    position = record.get("position")
    if path not in ([], ["mcpServers"], ["mcp"], ["mcp", "servers"]) or not isinstance(name, str) or not name:
        raise ValueError("restore record has an invalid source container")
    if not isinstance(raw, dict) or type(position) is not int or position < 0 or position > 4096:
        raise ValueError("restore record has an invalid definition")
    if fingerprint(raw) != record.get("definition"):
        raise ValueError("restore definition does not match its fingerprint")
    mapping = container(document, path, create=True)
    if name in mapping:
        if fingerprint(mapping[name]) == fingerprint(raw):
            return False
        raise ValueError("the source already has a different definition; nothing was changed")
    items = list(mapping.items())
    items.insert(min(position, len(items)), (name, raw))
    mapping.clear()
    mapping.update(items)
    return True


def normalized_toml(document: dict) -> dict:
    result = deepcopy(document)
    if result.get("mcp_servers") == {}:
        del result["mcp_servers"]
    return result


def detach_toml(text: str, name: str, raw: dict) -> tuple[str, dict]:
    document = parse_toml(text.encode("utf-8"))
    mapping = document.get("mcp_servers")
    if not isinstance(mapping, dict) or name not in mapping or fingerprint(mapping[name]) != fingerprint(raw):
        raise ValueError("the source definition changed; refresh and retry")
    located = locate_server_block(text, name)
    if located is None:
        raise TomlWriteFailure("table-not-found")
    start, end = located
    lines = text.splitlines(keepends=True)
    while start > 0 and not lines[start - 1].strip():
        start -= 1
    prefix, fragment, suffix = "".join(lines[:start]), "".join(lines[start:end]), "".join(lines[end:])
    fragment_value = parse_toml(fragment.encode("utf-8"))
    if fingerprint(fragment_value) != fingerprint({"mcp_servers": {name: raw}}):
        raise ValueError("the source table cannot be isolated without changing other settings")
    updated = prefix + suffix
    del mapping[name]
    if fingerprint(normalized_toml(parse_toml(updated.encode("utf-8")))) != fingerprint(normalized_toml(document)):
        raise ValueError("removing the source table would change other settings")
    return updated, {"kind": "toml", "name": name, "text": fragment, "offset": len(prefix),
                     "after": text_digest(updated), "definition": fingerprint(raw)}


def attach_toml(text: str, record: dict) -> str | None:
    name, fragment, offset = record.get("name"), record.get("text"), record.get("offset")
    after = record.get("after")
    if not isinstance(name, str) or not name or not isinstance(fragment, str) or type(offset) is not int or offset < 0:
        raise ValueError("restore record has an invalid source table")
    if not isinstance(after, str) or len(after) != 64 or any(char not in "0123456789abcdef" for char in after):
        raise ValueError("restore record has an invalid source fingerprint")
    saved = parse_toml(fragment.encode("utf-8"))
    mapping = saved.get("mcp_servers")
    if set(saved) != {"mcp_servers"} or not isinstance(mapping, dict) or set(mapping) != {name}:
        raise ValueError("restore record contains more than its source table")
    raw = mapping[name]
    if not isinstance(raw, dict) or fingerprint(raw) != record.get("definition"):
        raise ValueError("restore definition does not match its fingerprint")
    document = parse_toml(text.encode("utf-8"))
    servers = document.setdefault("mcp_servers", {})
    if not isinstance(servers, dict):
        raise ValueError("the source container changed; refresh and retry")
    if name in servers:
        if fingerprint(servers[name]) == fingerprint(raw):
            return None
        raise ValueError("the source already has a different definition; nothing was changed")
    if text_digest(text) == record.get("after"):
        if offset > len(text):
            raise ValueError("restore record has an invalid source position")
        updated = text[:offset] + fragment + text[offset:]
    else:
        updated = append_server_block(text, fragment)
    servers[name] = raw
    if fingerprint(parse_toml(updated.encode("utf-8"))) != fingerprint(document):
        raise ValueError("restoring the table would change other settings")
    return updated
