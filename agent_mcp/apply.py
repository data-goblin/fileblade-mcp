from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from fileblade_paths import parse_path, path_text
from fileblade_mutations import Snapshot

from .inventory import MAX_CLAUDE_BYTES, MAX_CONFIG_BYTES, Inventory
from . import records
from .model import CORE_AGENT_IDS, SCHEMA_VERSION, Definition, safe_label
from .parsers import ParseFailure, parse_json, parse_toml
from .recovery import RecoveryFull, RecoveryStore
from .safeio import atomic_write, bounded_read
from .tomlwrite import TomlWriteFailure, append_server_block, locate_server_block, remove_server_block, render_server_table

WRITE_AGENTS = ("claude-code", "codex", "opencode", "pi", "copilot-cli", "antigravity")
REMOTE_TRANSPORTS = {"sse", "streamable-http"}
SUPPORTED_TRANSPORTS = {
    "claude-code": {"stdio", "sse", "streamable-http"},
    "codex": {"stdio", "streamable-http"},
    "opencode": {"stdio", "sse", "streamable-http"},
    "pi": {"stdio", "sse", "streamable-http"},
    "copilot-cli": {"stdio", "sse", "streamable-http"},
    "antigravity": {"stdio", "sse", "streamable-http"},
}


class ApplyRefused(ValueError):
    pass


@dataclass(frozen=True)
class ServerSpec:
    name: str
    display: str
    transport: str
    command: str = ""
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class AgentResult:
    agent: str
    ok: bool
    changed: bool
    message: str
    touched: list[str] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "ok": self.ok,
            "changed": self.changed,
            "message": self.message,
            "touched": list(self.touched),
        }


def _string_map(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items() if isinstance(item, str)}


def server_spec(definition: Definition) -> ServerSpec:
    config = definition.raw_config
    if not isinstance(config, dict):
        raise ApplyRefused("source definition is not an object")
    transport = definition.transport
    display = safe_label(definition.raw_name, definition.id)
    if transport not in REMOTE_TRANSPORTS and transport != "stdio":
        raise ApplyRefused(f"transport {transport} cannot be copied")
    if transport == "stdio":
        command = config.get("command")
        arguments = config.get("args", [])
        if isinstance(command, list):
            if not command or not all(isinstance(item, str) for item in command):
                raise ApplyRefused("source command is not a string list")
            return ServerSpec(definition.raw_name, display, transport, command[0], tuple(command[1:]),
                              _string_map(config.get("env", config.get("environment"))))
        if not isinstance(command, str) or not command:
            raise ApplyRefused("source command is missing")
        if not isinstance(arguments, list) or not all(isinstance(item, str) for item in arguments):
            raise ApplyRefused("source args are not a string list")
        return ServerSpec(definition.raw_name, display, transport, command, tuple(arguments),
                          _string_map(config.get("env", config.get("environment"))))
    url = None
    for key in ("httpUrl", "serverUrl", "url"):
        if isinstance(config.get(key), str):
            url = config[key]
            break
    if not url:
        raise ApplyRefused("source url is missing")
    headers = _string_map(config.get("headers", config.get("http_headers")))
    return ServerSpec(definition.raw_name, display, transport, url=url, headers=headers)


def render_entry(agent: str, spec: ServerSpec) -> dict[str, Any]:
    stdio = spec.transport == "stdio"
    http = spec.transport == "streamable-http"
    if agent == "claude-code":
        if stdio:
            entry: dict[str, Any] = {"type": "stdio", "command": spec.command, "args": list(spec.args)}
            if spec.env:
                entry["env"] = dict(spec.env)
            return entry
        entry = {"type": "http" if http else "sse", "url": spec.url}
        if spec.headers:
            entry["headers"] = dict(spec.headers)
        return entry
    if agent == "codex":
        if stdio:
            entry = {"command": spec.command}
            if spec.args:
                entry["args"] = list(spec.args)
            if spec.env:
                entry["env"] = dict(spec.env)
            return entry
        entry = {"url": spec.url}
        if spec.headers:
            entry["http_headers"] = dict(spec.headers)
        return entry
    if agent == "opencode":
        if stdio:
            entry = {"type": "local", "command": [spec.command, *spec.args]}
            if spec.env:
                entry["environment"] = dict(spec.env)
            return entry
        entry = {"type": "remote", "url": spec.url}
        if spec.headers:
            entry["headers"] = dict(spec.headers)
        return entry
    if agent == "copilot-cli":
        if stdio:
            entry = {"type": "local", "command": spec.command, "args": list(spec.args)}
            if spec.env:
                entry["env"] = dict(spec.env)
            entry["tools"] = ["*"]
            return entry
        entry = {"type": "http" if http else "sse", "url": spec.url}
        if spec.headers:
            entry["headers"] = dict(spec.headers)
        entry["tools"] = ["*"]
        return entry
    if agent == "antigravity":
        if stdio:
            entry = {"command": spec.command}
            if spec.args:
                entry["args"] = list(spec.args)
            if spec.env:
                entry["env"] = dict(spec.env)
            return entry
        entry = {"serverUrl": spec.url}
        if spec.headers:
            entry["headers"] = dict(spec.headers)
        return entry
    if agent == "pi":
        if stdio:
            entry = {"command": spec.command}
            if spec.args:
                entry["args"] = list(spec.args)
            if spec.env:
                entry["env"] = dict(spec.env)
            return entry
        entry = {"url": spec.url}
        if spec.headers:
            entry["headers"] = dict(spec.headers)
        return entry
    raise ApplyRefused("unknown agent")


def conflict_message(spec: ServerSpec, label: str) -> str:
    return f"a different server named {spec.display} already exists in {label}; nothing was changed"


class Applier:
    def __init__(self, inventory: Inventory) -> None:
        self.inventory = inventory
        self.snapshots: dict[Path, Snapshot] = {}
        self.recovery = RecoveryStore(inventory.recovery_directory())

    def home(self) -> Path:
        return self.inventory.home

    def pi_target(self) -> Path:
        settings_path = self.home() / ".pi" / "agent" / "settings.json"
        result = bounded_read(settings_path, MAX_CONFIG_BYTES)
        packages: set[str] = set()
        if result.data is not None:
            try:
                packages = self.inventory.pi_packages(parse_json(result.data))
            except ParseFailure:
                packages = set()
        if "pi-mcp-adapter" in packages:
            return self.home() / ".pi" / "agent" / "mcp.json"
        if "pi-codemode-mcp" in packages:
            return self.home() / ".pi" / "agent" / ".mcp.json"
        raise ApplyRefused("Pi has no enabled MCP adapter package")

    def target_path(self, agent: str) -> Path:
        if agent == "claude-code":
            return self.home() / ".claude.json"
        if agent == "codex":
            return self.inventory.codex_home / "config.toml"
        if agent == "opencode":
            return self.inventory.config_home / "opencode" / "opencode.json"
        if agent == "copilot-cli":
            return self.home() / ".copilot" / "mcp-config.json"
        if agent == "antigravity":
            return self.home() / ".gemini" / "config" / "mcp_config.json"
        if agent == "pi":
            return self.pi_target()
        raise ApplyRefused("unknown agent")

    def read_target(self, path: Path, limit: int) -> bytes | None:
        try:
            snapshot = Snapshot.read(path, limit)
        except (OSError, RuntimeError) as error:
            raise ApplyRefused(str(error)) from error
        self.snapshots[path] = snapshot
        return snapshot.data

    def read_json_target(self, path: Path, limit: int) -> dict[str, Any]:
        data = self.read_target(path, limit)
        if data is None:
            return {}
        try:
            return parse_json(data)
        except ParseFailure:
            raise ApplyRefused("target is not strict JSON") from None

    def write_json_target(self, path: Path, document: dict[str, Any]) -> None:
        encoded = json.dumps(document, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
        error = atomic_write(path, encoded.encode("utf-8"), snapshot=self.snapshots.get(path))
        if error:
            raise ApplyRefused(f"target is {error}")

    def json_container(self, agent: str, document: dict[str, Any], create: bool) -> tuple[dict[str, Any] | None, str]:
        if agent == "opencode":
            mcp = document.get("mcp")
            if isinstance(mcp, dict) and "servers" not in mcp and mcp:
                return mcp, "mcp (v1 map)"
            if not isinstance(mcp, dict):
                if not create:
                    return None, "mcp.servers"
                mcp = {}
                document["mcp"] = mcp
            servers = mcp.get("servers")
            if not isinstance(servers, dict):
                if not create:
                    return None, "mcp.servers"
                servers = {}
                mcp["servers"] = servers
            return servers, "mcp.servers"
        servers = document.get("mcpServers")
        if not isinstance(servers, dict):
            if not create:
                return None, "mcpServers"
            servers = {}
            document["mcpServers"] = servers
        return servers, "mcpServers"

    def apply_json(self, agent: str, path: Path, spec: ServerSpec, state: str, limit: int) -> AgentResult:
        logical = self.inventory.logical_path(path)
        document = self.read_json_target(path, limit)
        if state == "off":
            container, label = self.json_container(agent, document, create=False)
            if container is None or spec.name not in container:
                return AgentResult(agent, True, False, "not present", [])
            del container[spec.name]
            self.write_json_target(path, document)
            return AgentResult(agent, True, True, f"removed from {label}", [logical])
        container, label = self.json_container(agent, document, create=True)
        entry = render_entry(agent, spec)
        existing = container.get(spec.name)
        if existing == entry:
            return AgentResult(agent, True, False, "already present", [])
        if existing is not None:
            return AgentResult(agent, False, False, conflict_message(spec, label), [])
        container[spec.name] = entry
        self.write_json_target(path, document)
        return AgentResult(agent, True, True, f"written to {label}", [logical])

    def apply_toml(self, agent: str, path: Path, spec: ServerSpec, state: str, *, raw: dict | None = None) -> AgentResult:
        logical = self.inventory.logical_path(path)
        data = self.read_target(path, MAX_CONFIG_BYTES)
        text = ""
        document: dict[str, Any] = {}
        if data is not None:
            try:
                document = parse_toml(data)
                text = data.decode("utf-8")
            except (ParseFailure, UnicodeError):
                raise ApplyRefused("target is not strict TOML") from None
        servers = document.get("mcp_servers")
        existing = servers.get(spec.name) if isinstance(servers, dict) else None
        entry = render_entry(agent, spec) if raw is None else raw
        try:
            if state == "off":
                if existing is None:
                    return AgentResult(agent, True, False, "not present", [])
                updated = remove_server_block(text, spec.name)
            else:
                if existing == entry:
                    return AgentResult(agent, True, False, "already present", [])
                if existing is not None:
                    return AgentResult(agent, False, False, conflict_message(spec, "mcp_servers"), [])
                updated = append_server_block(text, render_server_table(spec.name, entry))
        except TomlWriteFailure as error:
            raise ApplyRefused(f"cannot locate table: {error}") from None
        try:
            verified = parse_toml(updated.encode("utf-8"))
        except ParseFailure:
            raise ApplyRefused("edited TOML would not parse") from None
        verified_servers = verified.get("mcp_servers")
        present = isinstance(verified_servers, dict) and spec.name in verified_servers
        if state == "off" and present:
            raise ApplyRefused("table is also defined elsewhere in the file")
        if state == "on" and (not present or records.fingerprint(verified_servers[spec.name]) != records.fingerprint(entry)):
            raise ApplyRefused("edited TOML does not contain the intended table")
        expected = deepcopy(document)
        expected_servers = expected.setdefault("mcp_servers", {})
        if not isinstance(expected_servers, dict):
            raise ApplyRefused("target has an invalid mcp_servers table")
        if state == "off":
            expected_servers.pop(spec.name, None)
        else:
            expected_servers[spec.name] = entry
        if records.fingerprint(records.normalized_toml(verified)) != records.fingerprint(records.normalized_toml(expected)):
            raise ApplyRefused("edited TOML would change other settings")
        error = atomic_write(path, updated.encode("utf-8"), snapshot=self.snapshots.get(path))
        if error:
            raise ApplyRefused(f"target is {error}")
        if state == "off":
            return AgentResult(agent, True, True, "removed table", [logical])
        return AgentResult(agent, True, True, "written table", [logical])

    def apply_agent(self, definition: Definition, spec: ServerSpec, agent: str, state: str) -> AgentResult:
        if agent not in WRITE_AGENTS:
            return AgentResult(agent, False, False, "unknown agent", [])
        if spec.transport not in SUPPORTED_TRANSPORTS[agent]:
            return AgentResult(agent, False, False, f"transport {spec.transport} is not supported by {agent}", [])
        path = self.target_path(agent)
        if definition.absolute_path is not None and path.absolute() == definition.absolute_path:
            if state == "off":
                return AgentResult(agent, False, False, "refusing to remove the definition's own source entry", [])
            return AgentResult(agent, True, False, "already present", [])
        if agent == "codex":
            return self.apply_toml(agent, path, spec, state)
        limit = MAX_CLAUDE_BYTES if agent == "claude-code" else MAX_CONFIG_BYTES
        return self.apply_json(agent, path, spec, state, limit)

    def apply(self, identifier: str, agents: list[str], state: str) -> dict[str, Any]:
        if state not in {"on", "off"}:
            return self.failure("state must be on or off")
        self.inventory.scan()
        definition = self.inventory.definition_by_id(identifier)
        if definition is None:
            return self.failure("unknown definition id")
        try:
            spec = server_spec(definition)
        except ApplyRefused as error:
            return self.failure(str(error))
        requested: list[str] = []
        for agent in agents:
            core = CORE_AGENT_IDS.get(agent, agent)
            if agent == "all":
                requested.extend(item for item in WRITE_AGENTS if item not in requested)
            elif core not in requested:
                requested.append(core)
        results: list[AgentResult] = []
        for agent in requested:
            try:
                results.append(self.apply_agent(definition, spec, agent, state))
            except ApplyRefused as error:
                results.append(AgentResult(agent, False, False, str(error), []))
        return {
            "ok": all(result.ok for result in results) and bool(results),
            "schemaVersion": SCHEMA_VERSION,
            "project": "<project>",
            "changed": any(result.changed for result in results),
            "message": "" if all(result.ok for result in results) else "; ".join(
                f"{result.agent}: {result.message}" for result in results if not result.ok
            ),
            "results": [result.public() for result in results],
        }

    def recovery_context(self) -> dict[str, str]:
        return {
            "project": path_text(str(self.inventory.project)),
            "home": path_text(str(self.inventory.home)),
            "configHome": path_text(str(self.inventory.config_home)),
            "codexHome": path_text(str(self.inventory.codex_home)),
            "etcRoot": path_text(str(self.inventory.etc_root)),
        }

    def recorded_inventory(self, context: dict[str, str]) -> Inventory:
        return Inventory(
            context.get("project") or str(self.inventory.project),
            home=context.get("home", str(self.inventory.home)),
            config_home=context.get("configHome", str(self.inventory.config_home)),
            etc_root=context.get("etcRoot", str(self.inventory.etc_root)),
            codex_home=context.get("codexHome", str(self.inventory.codex_home)),
            system_owner_uid=self.inventory.system_owner_uid,
            environment=self.inventory.environment,
        )

    def outcome(self, agent_result: AgentResult, payload: dict[str, Any] | None = None,
                record_id: str = "") -> dict[str, Any]:
        outcome = {
            "ok": agent_result.ok,
            "schemaVersion": SCHEMA_VERSION,
            "project": "<project>",
            "changed": agent_result.changed,
            "message": "" if agent_result.ok else f"{agent_result.agent}: {agent_result.message}",
            "results": [agent_result.public()],
        }
        if payload is not None:
            outcome["payload"] = payload
        if record_id:
            outcome["recordId"] = record_id
        return outcome

    def remove(self, identifier: str, *, prepare: bool = False, expected_payload: dict | None = None) -> dict[str, Any]:
        self.inventory.scan()
        definition = self.inventory.definition_by_id(identifier)
        if definition is None:
            return self.failure("unknown definition id")
        agent = CORE_AGENT_IDS.get(definition.agent, definition.agent)
        if agent not in WRITE_AGENTS or definition.absolute_path is None or definition.scope not in ("user", "project"):
            return self.failure("only user and project definitions in a writable config can be removed")
        try:
            server_spec(definition)
            path = definition.absolute_path
            target = path.resolve(strict=True)
            limit = MAX_CLAUDE_BYTES if agent == "claude-code" else MAX_CONFIG_BYTES
            if agent == "codex":
                data = self.read_target(path, limit)
                if data is None:
                    raise ApplyRefused("the source definition is missing")
                updated, payload = records.detach_toml(data.decode("utf-8"), definition.raw_name, definition.raw_config)
                encoded_source = updated.encode("utf-8")
            else:
                document = self.read_json_target(path, limit)
                payload = records.detach_json(definition, document)
                encoded_source = (json.dumps(document, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")
            payload.update({"format": 2, "agent": agent, "path": path_text(str(path)), "target": path_text(str(target))})
            encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
            if len(encoded) > 1024 * 1024:
                return self.failure("the definition exceeds the undo record size limit; edit the source directly")
            record_id = self.recovery.write(payload, identifier, self.recovery_context())
            if prepare:
                return self.outcome(AgentResult(agent, True, False, "prepared recovery"), payload, record_id)
            if expected_payload is not None and payload != expected_payload:
                return self.failure("the source definition changed after recovery was prepared; nothing was changed")
            if path.resolve(strict=True) != target:
                raise ApplyRefused("the source path changed; refresh and retry")
            error = atomic_write(path, encoded_source, snapshot=self.snapshots.get(path))
            if error:
                raise ApplyRefused(f"target is {error}")
            agent_result = AgentResult(agent, True, True, "removed source definition", [self.inventory.logical_path(path)])
        except UnicodeError:
            return self.failure("the definition cannot be preserved as a Unicode undo record; edit the source directly")
        except RecoveryFull as error:
            return self.failure(str(error))
        except (OSError, RuntimeError, ValueError) as error:
            return self.failure(str(error))
        return self.outcome(agent_result, payload, record_id)

    def restore_record(self, payload: dict, context: dict[str, str] | None = None) -> dict:
        agent = payload.get("agent")
        if agent not in WRITE_AGENTS or not isinstance(payload.get("path"), str) or not isinstance(payload.get("target"), str):
            return self.failure("restore payload is incomplete")
        try:
            path = Path(parse_path(payload["path"]))
            target = Path(parse_path(payload["target"]))
            if not path.is_absolute() or not target.is_absolute():
                raise ApplyRefused("restore payload needs absolute source paths")
            recorded = self.recorded_inventory(context) if context else self.inventory
            recorded.scan()
            if agent not in recorded.config_paths.get(path.absolute(), set()):
                raise ApplyRefused("the recorded source is not a known configuration file for that agent")
            if path.resolve(strict=False) != target:
                raise ApplyRefused("the source path changed; restore was refused")
            if payload.get("kind") == "toml" and agent == "codex":
                data = self.read_target(path, MAX_CONFIG_BYTES)
                updated = records.attach_toml("" if data is None else data.decode("utf-8"), payload)
                changed = updated is not None
                encoded = updated.encode("utf-8") if changed else b""
            elif payload.get("kind") == "json" and agent != "codex":
                limit = MAX_CLAUDE_BYTES if agent == "claude-code" else MAX_CONFIG_BYTES
                document = self.read_json_target(path, limit)
                changed = records.attach_json(document, payload)
                encoded = (json.dumps(document, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8") if changed else b""
            else:
                raise ApplyRefused("restore payload has an invalid source format")
            if changed:
                if path.resolve(strict=False) != target:
                    raise ApplyRefused("the source path changed; restore was refused")
                error = atomic_write(path, encoded, snapshot=self.snapshots.get(path))
                if error:
                    raise ApplyRefused(f"target is {error}")
            result = AgentResult(agent, True, changed, "restored source definition" if changed else "already present",
                                 [self.inventory.logical_path(path)] if changed else [])
            return self.outcome(result)
        except (OSError, RuntimeError, ValueError) as error:
            return self.failure(str(error))

    def restore(self, record_id: str, raw_payload: str) -> dict[str, Any]:
        try:
            payload = parse_json(raw_payload.encode("utf-8"))
        except (UnicodeError, ValueError):
            return self.failure("restore payload is not JSON")
        if not isinstance(payload, dict):
            return self.failure("restore payload is not a record")
        record = self.recovery.read(record_id)
        if record is not None and record["payload"] != payload:
            record = None
        if record is None:
            record = self.recovery.find(payload)
        if record is None:
            return self.failure("no prepared recovery record matches this payload")
        prepared = record["payload"]
        if type(prepared.get("format")) is not int or prepared["format"] != 2:
            return self.failure("restore payload has an unsupported format")
        document = self.restore_record(prepared, record.get("context"))
        if document.get("ok"):
            self.recovery.mark_restored(str(record["recordId"]))
        return document

    def failure(self, message: str) -> dict[str, Any]:
        return {
            "ok": False,
            "schemaVersion": SCHEMA_VERSION,
            "project": "<project>",
            "changed": False,
            "message": message,
            "results": [],
        }
