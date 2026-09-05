from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping

from fileblade_paths import parse_path, wire
from fileblade_inventory import lane_rows, watch_path

from .model import CORE_AGENT_IDS, Definition, SCHEMA_VERSION, digest
from .parsers import ParseFailure, parse_json, parse_jsonc, parse_toml
from .safeio import Deadline, artifact_metrics, bounded_directories, bounded_files, bounded_read, safe_relative_file

MAX_CLAUDE_BYTES = 4 * 1024 * 1024
MAX_CONFIG_BYTES = 512 * 1024
MAX_REGISTRY_BYTES = 1024 * 1024
MAX_SOURCES = 256
MAX_DEFINITIONS = 1024
MAX_WARNINGS = 128
MAX_PLUGIN_DIRS = 128
MAX_PROFILE_FILES = 128
MAX_OUTPUT_BYTES = 512 * 1024
MAX_ENVIRONMENT_PATH_CHARS = 4096


PROJECT_SCOPES = frozenset({"project", "local"})


USER_SOURCE_KINDS = frozenset({
    "codex-profile", "codex-plugin-manifest", "claude-plugin-registry", "claude-plugin-manifest",
    "claude-plugin", "copilot-plugin-manifest", "copilot-plugin", "copilot-user",
    "copilot-managed-settings", "antigravity-cli-plugin", "antigravity-user", "opencode-user",
    "pi-shared-global", "pi-agents-global",
})


def project_source_kind(source_kind: str) -> bool:
    return source_kind == "project" or source_kind.endswith(("-project", "-project-override"))


def user_source_kind(source_kind: str) -> bool:
    return source_kind in USER_SOURCE_KINDS


def read_environment_path(environment: Mapping[str, object], name: str) -> str | None:
    value = environment.get(name)
    if not isinstance(value, str) or not value or "\0" in value or len(value) > MAX_ENVIRONMENT_PATH_CHARS:
        return None
    return value


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)][:1024]


def _enabled(config: object) -> tuple[bool | None, bool]:
    if not isinstance(config, dict):
        return None, False
    if "disabled" in config:
        return (not config["disabled"], True) if isinstance(config["disabled"], bool) else (None, False)
    if "enabled" in config:
        return (config["enabled"], True) if isinstance(config["enabled"], bool) else (None, False)
    return True, True


def _transport(agent: str, config: object) -> tuple[str, bool]:
    if not isinstance(config, dict):
        return "unknown", False
    kind = str(config.get("type", "")).lower()
    if "socket" in config:
        return "unix", True
    if "httpUrl" in config:
        return "streamable-http", True
    if "serverUrl" in config:
        if kind in {"sse", "websocket", "ws", "streamable-http", "http"}:
            if kind in {"websocket", "ws"}:
                return "websocket", True
            return ("sse" if kind == "sse" else "streamable-http"), True
        return "unknown", True
    if "url" in config:
        if kind == "sse":
            return "sse", True
        return "streamable-http", True
    if "command" in config:
        return "stdio", True
    if kind in {"local", "stdio"}:
        return "stdio", False
    if kind in {"remote", "http"}:
        return "streamable-http", False
    if kind == "sse":
        return "sse", False
    return "unknown", False


def _endpoint_signature(config: object) -> str | None:
    if not isinstance(config, dict):
        return None
    for key in ("httpUrl", "serverUrl", "url"):
        value = config.get(key)
        if isinstance(value, str):
            return digest("endpoint-v1", "remote", value)
    command = config.get("command")
    arguments = config.get("args", [])
    if isinstance(command, list) and all(isinstance(item, str) for item in command):
        return digest("endpoint-v1", "stdio", *command)
    if isinstance(command, str) and isinstance(arguments, list) and all(isinstance(item, str) for item in arguments):
        return digest("endpoint-v1", "stdio", command, *arguments)
    return None


def _secret_presence(config: object) -> dict[str, bool]:
    if not isinstance(config, dict):
        return {"environment": False, "headers": False, "authentication": False}
    keys = {str(key).lower() for key in config}
    environment = bool(keys & {"env", "environment", "env_vars", "envvars"})
    headers = bool(keys & {"headers", "http_headers", "env_http_headers", "headershelper"})
    authentication = bool(keys & {
        "oauth", "auth", "bearertoken", "bearer_token", "bearertokenenv", "bearer_token_env_var",
        "clientsecret", "client_secret", "authprovidertype",
    })
    return {"environment": environment, "headers": headers, "authentication": authentication}


class Inventory:
    def __init__(
        self,
        project: str | Path,
        *,
        home: str | Path | None = None,
        config_home: str | Path | None = None,
        etc_root: str | Path = "/etc",
        deadline_seconds: float = 2.0,
        system_owner_uid: int = 0,
        codex_home: str | Path | None = None,
        environment: Mapping[str, object] | None = None,
        scope: str = "all",
    ) -> None:
        self.scope = scope
        self.project = Path(parse_path(str(project))).absolute()
        self.home = Path(parse_path(str(home))).absolute() if home is not None else Path.home().absolute()
        self.config_home = Path(parse_path(str(config_home))).absolute() if config_home is not None else self.home / ".config"
        self.etc_root = Path(parse_path(str(etc_root))).absolute()
        self.system_owner_uid = system_owner_uid
        self.environment = os.environ if environment is None else environment
        configured_codex_home = (
            codex_home
            if codex_home is not None
            else read_environment_path(self.environment, "CODEX_HOME")
        )
        self.codex_home = (
            Path(parse_path(str(configured_codex_home))).absolute()
            if configured_codex_home
            else self.home / ".codex"
        )
        self.deadline = Deadline(deadline_seconds)
        self.definitions: list[Definition] = []
        self.warnings: list[dict[str, str]] = []
        self.agent_status: dict[str, dict[str, str]] = {}
        self.sources = 0
        self.source_metrics: dict[str, dict[str, object]] = {}
        self.truncated = False

    def logical_path(self, path: Path) -> str:
        absolute = path.absolute()
        try:
            relative = absolute.relative_to(self.project)
            return "<project>" if not relative.parts else "<project>/" + relative.as_posix()
        except ValueError:
            pass
        try:
            relative = absolute.relative_to(self.home)
            return "~" if not relative.parts else "~/" + relative.as_posix()
        except ValueError:
            pass
        try:
            relative = absolute.relative_to(self.codex_home)
            return "<codex-home>" if not relative.parts else "<codex-home>/" + relative.as_posix()
        except ValueError:
            return absolute.as_posix()

    def warn(self, agent: str, source_kind: str, path: Path, code: str) -> None:
        if len(self.warnings) >= MAX_WARNINGS:
            self.truncated = True
            return
        logical = self.logical_path(path)
        self.warnings.append({
            "code": code[:64],
            "sourceId": digest("source-v1", agent, source_kind, logical),
        })

    def read_document(
        self,
        agent: str,
        source_kind: str,
        path: Path,
        parser: Callable[[bytes], dict[str, Any]],
        *,
        limit: int = MAX_CONFIG_BYTES,
        secure_managed: bool = False,
    ) -> dict[str, Any] | None:
        self.deadline.check()
        if self.scope == "user" and project_source_kind(source_kind):
            return None
        if self.scope == "project" and user_source_kind(source_kind):
            return None
        if self.sources >= MAX_SOURCES:
            self.truncated = True
            return None
        result = bounded_read(
            path,
            limit,
            required_owner_uid=self.system_owner_uid if secure_managed else None,
            reject_group_or_world_writable=secure_managed,
        )
        if result.error == "missing":
            return None
        self.sources += 1
        logical = self.logical_path(path)
        if result.data is not None:
            self.source_metrics[logical] = artifact_metrics(path, result.data)
        if result.data is None:
            self.warn(agent, source_kind, path, result.error or "unreadable")
            return None
        try:
            return parser(result.data)
        except ParseFailure as error:
            self.warn(agent, source_kind, path, str(error))
            return None

    def add_servers(
        self,
        *,
        agent: str,
        mapping: object,
        scope: str,
        source_kind: str,
        path: Path,
        support: str,
        priority: int,
        trusted: bool | None = True,
        enabled_override: bool | None = None,
        enabled_known: bool = True,
        precedence_known: bool = True,
        trust_from_config: bool = False,
        trust_gates_effective: bool = True,
        trust_role: str = "source-approval",
    ) -> None:
        if not isinstance(mapping, dict) or not lane_rows([{"scope": scope}], self.scope, PROJECT_SCOPES):
            return
        logical = self.logical_path(path)
        for raw_name in sorted(mapping, key=lambda value: str(value)):
            self.deadline.check()
            if len(self.definitions) >= MAX_DEFINITIONS:
                self.truncated = True
                return
            name = str(raw_name)
            config = mapping[raw_name]
            enabled, enabled_valid = _enabled(config)
            if not enabled_known:
                enabled = None
            if enabled_override is not None:
                enabled = enabled and enabled_override if enabled is not None else enabled_override
            definition_trusted = trusted
            if trust_from_config and isinstance(config, dict):
                definition_trusted = config.get("trust", False) if isinstance(config.get("trust", False), bool) else None
            transport, transport_valid = _transport(agent, config)
            self.definitions.append(Definition(
                agent=agent,
                raw_name=name,
                scope=scope,
                source_kind=source_kind,
                source_path=logical,
                support=support,
                transport=transport,
                enabled=enabled,
                trusted=definition_trusted,
                valid=isinstance(config, dict) and enabled_valid and transport_valid,
                priority=priority,
                precedence_known=precedence_known,
                trust_gates_effective=trust_gates_effective,
                trust_role=trust_role,
                endpoint_signature=_endpoint_signature(config),
                secret_presence=_secret_presence(config),
                metrics=dict(self.source_metrics.get(logical, {})),
                raw_config=config if isinstance(config, dict) else None,
                absolute_path=path.absolute(),
            ))

    def ancestor_directories(self) -> list[Path]:
        current = self.project
        chain = [current]
        while current.parent != current and len(chain) < 64:
            if self.scope != "user":
                watch_path(current / ".git")
            if (current / ".git").exists():
                break
            current = current.parent
            chain.append(current)
        return list(reversed(chain))

    def filesystem_ancestors(self) -> list[Path]:
        current = self.project
        chain = [current]
        while current.parent != current and len(chain) < 64:
            current = current.parent
            chain.append(current)
        return list(reversed(chain))

    def nested_directories(self, root: Path, depth: int) -> list[Path]:
        current = [root]
        for _ in range(depth):
            following: list[Path] = []
            for parent in current:
                remaining = MAX_PLUGIN_DIRS - len(following)
                if remaining <= 0:
                    self.truncated = True
                    break
                following.extend(bounded_directories(parent, remaining, self.deadline))
            current = following
            if not current:
                break
        return current

    def plugin_source(
        self,
        agent: str,
        plugin: Path,
        manifest: dict[str, Any],
        manifest_path: Path,
        *,
        source_kind: str,
        support: str,
        priority: int,
        enabled: bool | None,
        precedence_known: bool,
    ) -> None:
        declared = manifest.get("mcpServers")
        if isinstance(declared, dict):
            self.add_servers(agent=agent, mapping=declared, scope="plugin", source_kind=source_kind + "-inline",
                             path=manifest_path, support=support, priority=priority, enabled_override=enabled,
                             precedence_known=precedence_known)
            return
        candidates: list[Path] = []
        if isinstance(declared, str):
            candidate = safe_relative_file(plugin, declared)
            if candidate is None:
                self.warn(agent, source_kind, manifest_path, "unsafe-plugin-path")
                return
            candidates.append(candidate)
        elif isinstance(declared, list):
            for value in declared[:16]:
                candidate = safe_relative_file(plugin, value)
                if candidate is None:
                    self.warn(agent, source_kind, manifest_path, "unsafe-plugin-path")
                    continue
                candidates.append(candidate)
        else:
            candidates.extend((plugin / ".mcp.json", plugin / ".github" / "mcp.json"))
        for path in candidates:
            document = self.read_document(agent, source_kind, path, parse_json)
            if document:
                mapping = document.get("mcpServers") if isinstance(document.get("mcpServers"), dict) else document.get("mcp_servers")
                if not isinstance(mapping, dict):
                    mapping = document
                self.add_servers(agent=agent, mapping=mapping, scope="plugin", source_kind=source_kind,
                                 path=path, support=support, priority=priority, enabled_override=enabled,
                                 precedence_known=precedence_known)
                return

    def settings_plugin_states(self, paths: list[Path], agent: str) -> dict[str, bool]:
        states: dict[str, bool] = {}
        for path in paths:
            document = self.read_document(agent, f"{agent}-settings", path, parse_json)
            if not document:
                continue
            enabled = document.get("enabledPlugins")
            if isinstance(enabled, dict):
                for key, value in enabled.items():
                    if isinstance(key, str) and isinstance(value, bool):
                        states[key] = value
            disabled = document.get("disabledPlugins")
            for key in _string_list(disabled):
                states[key] = False
        return states

    def apply_name_policy(self, agent: str, document: dict[str, Any] | None) -> None:
        if not document:
            return
        allowed_value = document.get("allowedMcpServers")
        denied_value = document.get("deniedMcpServers")

        def names(value: object) -> tuple[set[str], bool]:
            result: set[str] = set()
            ambiguous = False
            if not isinstance(value, list):
                return result, False
            for entry in value[:1024]:
                if not isinstance(entry, dict):
                    ambiguous = True
                    continue
                name = entry.get("serverName")
                other = set(entry) - {"serverName"}
                if isinstance(name, str) and not other:
                    result.add(name)
                else:
                    ambiguous = True
            return result, ambiguous

        allowed, allowed_ambiguous = names(allowed_value)
        denied, denied_ambiguous = names(denied_value)
        for definition in self.definitions:
            if definition.agent != agent:
                continue
            if definition.raw_name in denied and not denied_ambiguous:
                definition.enabled = False
                continue
            if isinstance(allowed_value, list):
                if not allowed_value or (not allowed_ambiguous and definition.raw_name not in allowed):
                    definition.enabled = False
                elif allowed_ambiguous and definition.enabled is not False:
                    definition.enabled = None
            if denied_ambiguous and definition.enabled is not False:
                definition.enabled = None

    def installed_plugin_paths(self, document: dict[str, Any], root: Path) -> list[tuple[str, Path]]:
        found: list[tuple[str, Path]] = []

        def walk(value: object, label: str = "") -> None:
            if len(found) >= MAX_PLUGIN_DIRS:
                return
            if isinstance(value, dict):
                install = value.get("installPath") or value.get("path")
                if isinstance(install, str):
                    candidate = Path(install)
                    if not candidate.is_absolute():
                        candidate = root / candidate
                    candidate = candidate.absolute()
                    try:
                        candidate.relative_to(root.absolute())
                    except ValueError:
                        return
                    name = str(value.get("id") or value.get("name") or label or candidate.name)
                    found.append((name, candidate))
                    return
                for key, child in list(value.items())[:MAX_PLUGIN_DIRS]:
                    walk(child, str(key))
            elif isinstance(value, list):
                for child in value[:MAX_PLUGIN_DIRS]:
                    walk(child, label)

        walk(document)
        unique: dict[str, tuple[str, Path]] = {}
        for name, path in found:
            unique[path.as_posix()] = (name, path)
        return [unique[key] for key in sorted(unique)]

    def discover_claude(self) -> None:
        agent = "claude"
        user_path = self.home / ".claude.json"
        user = self.read_document(agent, "claude-user-local", user_path, parse_json, limit=MAX_CLAUDE_BYTES)
        local_mapping: object = None
        project_trust: dict[str, Any] = {}
        if user:
            self.add_servers(agent=agent, mapping=user.get("mcpServers"), scope="user", source_kind="claude-user", path=user_path,
                             support="documented", priority=30)
            projects = _dict(user.get("projects"))
            for key in (self.project.as_posix(), str(self.project)):
                project_record = projects.get(key)
                if isinstance(project_record, dict):
                    local_mapping = project_record.get("mcpServers")
                    project_trust = project_record
                    break
            self.add_servers(agent=agent, mapping=local_mapping, scope="local", source_kind="claude-local", path=user_path,
                             support="documented", priority=50)

        project_path = self.project / ".mcp.json"
        project = self.read_document(agent, "claude-project", project_path, parse_json)
        approvals: list[dict[str, Any]] = [project_trust]
        for kind, settings_path in (
            ("claude-user-settings", self.home / ".claude" / "settings.json"),
            ("claude-settings-project", self.project / ".claude" / "settings.json"),
            ("claude-settings-local-project", self.project / ".claude" / "settings.local.json"),
        ):
            settings_document = self.read_document(agent, kind, settings_path, parse_json)
            if settings_document:
                approvals.append(settings_document)
        approved = {name for record in approvals for name in _string_list(record.get("enabledMcpjsonServers"))}
        denied = {name for record in approvals for name in _string_list(record.get("disabledMcpjsonServers"))}
        approve_all = any(record.get("enableAllProjectMcpServers") is True for record in approvals)
        if project:
            mapping = project.get("mcpServers")
            if isinstance(mapping, dict):
                for name, config in mapping.items():
                    trust = False if name in denied else True if approve_all or name in approved else None
                    self.add_servers(agent=agent, mapping={name: config}, scope="project", source_kind="claude-project", path=project_path,
                                     support="documented", priority=40, trusted=trust)

        plugin_root = self.home / ".claude" / "plugins"
        registry_path = plugin_root / "installed_plugins.json"
        registry = self.read_document(agent, "claude-plugin-registry", registry_path, parse_json, limit=MAX_REGISTRY_BYTES)
        plugin_states = self.settings_plugin_states([
            self.home / ".claude" / "settings.json",
            self.project / ".claude" / "settings.json",
            self.project / ".claude" / "settings.local.json",
        ], agent) if registry else {}
        managed_settings_path = self.etc_root / "claude-code" / "managed-settings.json"
        managed_settings = self.read_document(agent, "claude-managed-settings", managed_settings_path, parse_json,
                                              secure_managed=True)
        if managed_settings:
            managed_plugins = managed_settings.get("enabledPlugins")
            if isinstance(managed_plugins, dict):
                for key, value in managed_plugins.items():
                    if isinstance(key, str) and isinstance(value, bool):
                        plugin_states[key] = value
        if registry:
            for plugin_id, plugin_path in self.installed_plugin_paths(registry, plugin_root):
                enabled = plugin_states.get(plugin_id)
                manifest_path = plugin_path / ".claude-plugin" / "plugin.json"
                manifest = self.read_document(agent, "claude-plugin-manifest", manifest_path, parse_json)
                if manifest and "mcpServers" in manifest:
                    self.plugin_source(agent, plugin_path, manifest, manifest_path, source_kind="claude-plugin",
                                       support="documented", priority=20, enabled=enabled, precedence_known=True)
                else:
                    mcp_path = plugin_path / ".mcp.json"
                    mcp = self.read_document(agent, "claude-plugin", mcp_path, parse_json)
                    if mcp:
                        self.add_servers(agent=agent, mapping=mcp.get("mcpServers"), scope="plugin",
                                         source_kind="claude-plugin", path=mcp_path, support="documented",
                                         priority=20, enabled_override=enabled)

        managed_path = self.etc_root / "claude-code" / "managed-mcp.json"
        managed = self.read_document(agent, "claude-managed", managed_path, parse_json, secure_managed=True)
        if managed:
            self.add_servers(agent=agent, mapping=managed.get("mcpServers"), scope="managed", source_kind="claude-managed",
                             path=managed_path, support="documented", priority=100)
            for definition in self.definitions:
                if definition.agent == agent and definition.scope != "managed":
                    definition.selected = False
        self.apply_name_policy(agent, managed_settings)

    def discover_codex(self) -> None:
        agent = "codex"
        user_path = self.home / ".codex" / "config.toml"
        user = self.read_document(agent, "codex-user", user_path, parse_toml)
        if user:
            self.add_servers(agent=agent, mapping=user.get("mcp_servers"), scope="user", source_kind="codex-user", path=user_path,
                             support="documented", priority=20)
        trust: bool | None = None
        if user:
            project_records = _dict(user.get("projects"))
            record = project_records.get(self.project.as_posix())
            if isinstance(record, dict):
                level = record.get("trust_level")
                trust = True if level == "trusted" else False if level == "untrusted" else None
        for index, directory in enumerate(self.ancestor_directories()):
            path = directory / ".codex" / "config.toml"
            document = self.read_document(agent, "codex-project", path, parse_toml)
            if document:
                self.add_servers(agent=agent, mapping=document.get("mcp_servers"), scope="project", source_kind="codex-project",
                                 path=path, support="documented", priority=40 + index, trusted=trust)

        profile_suffix = ".config.toml"
        for path in (bounded_files(self.codex_home, MAX_PROFILE_FILES, self.deadline) if self.scope != "project" else []):
            if len(path.name) <= len(profile_suffix) or not path.name.endswith(profile_suffix):
                continue
            document = self.read_document(agent, "codex-profile", path, parse_toml)
            if document:
                self.add_servers(
                    agent=agent,
                    mapping=document.get("mcp_servers"),
                    scope="profile",
                    source_kind="codex-profile",
                    path=path,
                    support="documented",
                    priority=0,
                    enabled_known=False,
                    trusted=None,
                    precedence_known=False,
                )

        plugin_controls = _dict(user.get("plugins")) if user else {}
        cache_root = self.home / ".codex" / "plugins" / "cache"
        for version in (self.nested_directories(cache_root, 3) if self.scope != "project" else []):
            plugin = version.parent
            marketplace = plugin.parent
            manifest_path = version / ".codex-plugin" / "plugin.json"
            manifest = self.read_document(agent, "codex-plugin-manifest", manifest_path, parse_json)
            if not manifest:
                continue
            plugin_name = str(manifest.get("name") or plugin.name)
            plugin_id = f"{plugin_name}@{marketplace.name}"
            control = _dict(plugin_controls.get(plugin_id))
            plugin_enabled = control.get("enabled") if isinstance(control.get("enabled"), bool) else None
            before = len(self.definitions)
            self.plugin_source(agent, version, manifest, manifest_path, source_kind="codex-plugin",
                               support="documented", priority=10, enabled=plugin_enabled,
                               precedence_known=False)
            server_controls = _dict(control.get("mcp_servers"))
            for definition in self.definitions[before:]:
                server = _dict(server_controls.get(definition.raw_name))
                if isinstance(server.get("enabled"), bool):
                    definition.enabled = bool(server["enabled"]) and definition.enabled is not False
        self.agent_status[agent] = {
            "id": agent,
            "support": "partial",
            "reason": "plugin-version-profile-selection-and-cli-overrides-unobserved",
        }

    def config_candidates(self, directory: Path, name: str) -> list[Path]:
        return [directory / f"{name}.json", directory / f"{name}.jsonc"]

    def discover_opencode(self) -> None:
        agent = "opencode"
        ancestors = self.filesystem_ancestors()
        candidates: list[tuple[Path, str, int, bool]] = []
        candidates.extend((path, "user", 100, False) for path in self.config_candidates(self.config_home / "opencode", "opencode"))
        for index, directory in enumerate(ancestors):
            candidates.extend((path, "project", 200 + index, False) for path in self.config_candidates(directory, "opencode"))
        for index, directory in enumerate(ancestors):
            candidates.extend((path, "project", 300 + index, True) for path in self.config_candidates(directory / ".opencode", "opencode"))
        v1_paths = {path for directory in self.ancestor_directories() for path in self.config_candidates(directory, "opencode")}
        for path, scope, priority, dot_directory in candidates:
            document = self.read_document(agent, f"opencode-{scope}", path, parse_jsonc)
            if not document:
                continue
            mcp = document.get("mcp")
            if not isinstance(mcp, dict):
                continue
            if isinstance(mcp.get("servers"), dict):
                mapping = mcp["servers"]
                support = "beta"
                source_kind = f"opencode-v2-{scope}"
            else:
                if dot_directory or (scope == "project" and path not in v1_paths):
                    continue
                mapping = mcp
                support = "documented"
                source_kind = f"opencode-v1-{scope}"
            self.add_servers(agent=agent, mapping=mapping, scope=scope, source_kind=source_kind, path=path,
                             support=support, priority=priority)
        self.agent_status[agent] = {"id": agent, "support": "partial", "reason": "remote-inline-plugin-state-unobserved"}

    def pi_packages(self, document: dict[str, Any]) -> set[str]:
        result: set[str] = set()
        packages = document.get("packages")
        if not isinstance(packages, list):
            return result
        for entry in packages[:MAX_PLUGIN_DIRS]:
            if isinstance(entry, str):
                text = entry
                if text.startswith("npm:"):
                    text = text[4:]
                if text.startswith("@"):
                    slash = text.find("/")
                    version = text.find("@", slash + 1) if slash >= 0 else -1
                    result.add(text[:version] if version >= 0 else text)
                else:
                    result.add(text.split("@", 1)[0])
            elif isinstance(entry, dict) and entry.get("enabled") is not False:
                value = entry.get("name") or entry.get("source")
                if isinstance(value, str):
                    result.add(value.removeprefix("npm:").split("@", 1)[0])
        return result

    def pi_server_map(self, document: dict[str, Any]) -> object:
        if isinstance(document.get("mcpServers"), dict):
            return document["mcpServers"]
        return {key: value for key, value in document.items() if key not in {"settings", "imports", "$schema"}}

    def discover_pi(self) -> None:
        agent = "pi"
        agent_dir = self.home / ".pi" / "agent"
        settings_path = agent_dir / "settings.json"
        settings = self.read_document(agent, "pi-settings", settings_path, parse_json)
        packages = self.pi_packages(settings or {})
        project_settings = self.read_document(agent, "pi-settings-project", self.project / ".pi" / "settings.json", parse_json)
        packages |= self.pi_packages(project_settings or {})
        adapter = "pi-mcp-adapter" in packages
        codemode = "pi-codemode-mcp" in packages
        if not adapter and not codemode:
            self.agent_status[agent] = {"id": agent, "support": "unsupported", "reason": "pi-core-has-no-native-mcp"}
            return
        self.agent_status[agent] = {"id": agent, "support": "extension", "reason": "explicit-supported-adapter"}
        if codemode and not adapter:
            sources = [
                (agent_dir / "mcp.json", "user", 10, True),
                (agent_dir / ".mcp.json", "user", 20, True),
                (self.project / ".pi" / "mcp.json", "project", 30, None),
                (self.project / ".mcp.json", "project", 40, None),
            ]
            for path, scope, priority, trusted in sources:
                document = self.read_document(agent, "pi-codemode-mcp", path, parse_json)
                if document:
                    self.add_servers(agent=agent, mapping=self.pi_server_map(document), scope=scope,
                                     source_kind="pi-codemode-mcp", path=path, support="extension",
                                     priority=priority, trusted=trusted)
            return
        sources = [
            (self.config_home / "mcp" / "mcp.json", "user", "pi-shared-global", 10, True),
            (self.home / ".agents" / "mcp.json", "user", "pi-agents-global", 20, True),
            (self.home / ".agents" / "mcp" / "mcp.json", "user", "pi-agents-global", 30, True),
            (agent_dir / "mcp.json", "user", "pi-user-override", 40, True),
            (self.project / ".mcp.json", "project", "pi-shared-project", 50, None),
            (self.project / ".pi" / "mcp.json", "project", "pi-project-override", 60, None),
        ]
        for path, scope, kind, priority, trusted in sources:
            document = self.read_document(agent, kind, path, parse_json)
            if document:
                self.add_servers(agent=agent, mapping=self.pi_server_map(document), scope=scope, source_kind=kind, path=path,
                                 support="extension", priority=priority, trusted=trusted)

    def discover_copilot(self) -> None:
        agent = "github-copilot-cli"
        user_path = self.home / ".copilot" / "mcp-config.json"
        user = self.read_document(agent, "copilot-user", user_path, parse_json)
        if user:
            self.add_servers(agent=agent, mapping=user.get("mcpServers"), scope="user", source_kind="copilot-user",
                             path=user_path, support="documented", priority=10)
        for index, directory in enumerate(self.ancestor_directories()):
            for offset, path in enumerate((directory / ".github" / "mcp.json", directory / ".mcp.json")):
                document = self.read_document(agent, "copilot-project", path, parse_json)
                if document:
                    mapping = document.get("mcpServers") if isinstance(document.get("mcpServers"), dict) else document
                    self.add_servers(agent=agent, mapping=mapping, scope="project", source_kind="copilot-project", path=path,
                                     support="documented", priority=30 + index * 2 + offset, trusted=None)

        settings_paths = ([self.home / ".copilot" / "settings.json"] if self.scope != "project" else []) + [
            self.project / ".github" / "copilot" / "settings.json",
            self.project / ".github" / "copilot" / "settings.local.json",
        ]
        plugin_states: dict[str, bool] = {}
        disabled_servers: set[str] = set()
        for settings_path in settings_paths:
            settings = self.read_document(agent, "copilot-settings", settings_path, parse_jsonc)
            if not settings:
                continue
            enabled_plugins = settings.get("enabledPlugins")
            if isinstance(enabled_plugins, dict):
                for key, value in enabled_plugins.items():
                    if isinstance(key, str) and isinstance(value, bool):
                        plugin_states[key] = value
            disabled_servers.update(_string_list(settings.get("disabledMcpServers")))
        managed_settings_path = self.etc_root / "github-copilot" / "managed-settings.json"
        managed_settings = self.read_document(agent, "copilot-managed-settings", managed_settings_path, parse_json,
                                              secure_managed=True)
        if managed_settings:
            managed_plugins = managed_settings.get("enabledPlugins")
            if isinstance(managed_plugins, dict):
                for key, value in managed_plugins.items():
                    if isinstance(key, str) and isinstance(value, bool):
                        plugin_states[key] = value
        installed_root = self.home / ".copilot" / "installed-plugins"
        for plugin in (self.nested_directories(installed_root, 2) if self.scope != "project" else []):
            marketplace = plugin.parent
            manifest = None
            manifest_path = plugin / "plugin.json"
            for candidate in (
                plugin / ".plugin" / "plugin.json", plugin / "plugin.json",
                plugin / ".github" / "plugin" / "plugin.json", plugin / ".claude-plugin" / "plugin.json",
            ):
                document = self.read_document(agent, "copilot-plugin-manifest", candidate, parse_json)
                if document:
                    manifest = document
                    manifest_path = candidate
                    break
            if not manifest:
                continue
            plugin_name = str(manifest.get("name") or plugin.name)
            plugin_id = plugin_name if marketplace.name == "_direct" else f"{plugin_name}@{marketplace.name}"
            enabled = plugin_states.get(plugin_id, plugin_states.get(plugin_name, True))
            self.plugin_source(agent, plugin, manifest, manifest_path, source_kind="copilot-plugin",
                               support="documented", priority=100, enabled=enabled,
                               precedence_known=True)
        for definition in self.definitions:
            if definition.agent == agent and definition.raw_name in disabled_servers:
                definition.enabled = False
        self.apply_name_policy(agent, managed_settings)
        self.agent_status[agent] = {"id": agent, "support": "partial", "reason": "session-and-organization-state-unobserved"}

    def discover_antigravity(self) -> None:
        agent = "google-antigravity"
        for path, scope in (
            (self.home / ".gemini" / "config" / "mcp_config.json", "user"),
            (self.project / ".agents" / "mcp_config.json", "project"),
        ):
            document = self.read_document(agent, f"antigravity-{scope}", path, parse_json)
            if document:
                self.add_servers(agent=agent, mapping=document.get("mcpServers"), scope=scope,
                                 source_kind=f"antigravity-{scope}", path=path, support="documented", priority=10,
                                 trusted=True if scope == "user" else None, precedence_known=False)
        plugin_root = self.home / ".gemini" / "antigravity-cli" / "plugins"
        for plugin in (bounded_directories(plugin_root, MAX_PLUGIN_DIRS, self.deadline) if self.scope != "project" else []):
            path = plugin / "mcp_config.json"
            document = self.read_document(agent, "antigravity-cli-plugin", path, parse_json)
            if document:
                self.add_servers(
                    agent=agent,
                    mapping=document.get("mcpServers"),
                    scope="plugin",
                    source_kind="antigravity-cli-plugin",
                    path=path,
                    support="documented",
                    priority=10,
                    enabled_known=False,
                    trusted=None,
                    precedence_known=False,
                )
        self.agent_status[agent] = {
            "id": agent,
            "support": "partial",
            "reason": "plugin-enable-and-source-precedence-unobserved",
        }

    def finalize(self) -> None:
        duplicate_groups: dict[tuple[str, str], list[Definition]] = {}
        for definition in self.definitions:
            if definition.selected is False:
                continue
            collision_name = definition.raw_name
            if definition.agent == "claude" and definition.scope == "plugin":
                collision_name = f"plugin:{definition.id}"
            duplicate_groups.setdefault((definition.agent, collision_name), []).append(definition)
        for group in duplicate_groups.values():
            if len(group) > 1:
                duplicate = digest("duplicate-v1", group[0].agent, *(sorted(item.id for item in group)))
                for item in group:
                    item.duplicate_group = duplicate

        groups: dict[tuple[str, str], list[Definition]] = {}
        for key, definitions in duplicate_groups.items():
            active = [item for item in definitions if item.source_kind != "codex-profile"]
            groups[key] = active
        for group in groups.values():
            if not group:
                continue
            if len(group) == 1:
                group[0].selected = True if group[0].selected is None else group[0].selected
                continue
            if not all(item.precedence_known for item in group):
                for item in group:
                    item.selected = None
                continue
            highest = max(item.priority for item in group)
            winners = [item for item in group if item.priority == highest]
            if len(winners) != 1:
                for item in group:
                    item.selected = None if item.priority == highest else False
                continue
            winner = winners[0]
            winner.selected = True
            for item in group:
                if item is not winner:
                    item.selected = False
                    item.shadowed_by = winner.id

        claude_manual = [
            item for item in self.definitions
            if item.agent == "claude" and item.scope != "plugin" and item.selected is True and item.enabled is not False
        ]
        claude_plugins = [
            item for item in self.definitions
            if item.agent == "claude" and item.scope == "plugin" and item.enabled is not False
        ]
        endpoint_winners: dict[str, Definition] = {
            item.endpoint_signature: item for item in claude_manual if item.endpoint_signature is not None
        }
        for plugin in sorted(claude_plugins, key=lambda item: (item.source_path, item.raw_name)):
            signature = plugin.endpoint_signature
            if signature is None:
                continue
            winner = endpoint_winners.get(signature)
            if winner:
                duplicate = digest("duplicate-v1", *(sorted((winner.id, plugin.id))))
                winner.duplicate_group = winner.duplicate_group or duplicate
                plugin.duplicate_group = duplicate
                plugin.selected = False
                plugin.shadowed_by = winner.id
            elif plugin.selected is True:
                endpoint_winners[signature] = plugin

        applied: dict[tuple[str, str], set[str]] = {}
        for definition in self.definitions:
            if definition.endpoint_signature is None:
                continue
            key = (definition.raw_name, definition.endpoint_signature)
            applied.setdefault(key, set()).add(CORE_AGENT_IDS.get(definition.agent, definition.agent))
        for definition in self.definitions:
            if definition.endpoint_signature is None:
                definition.applied_agents = [CORE_AGENT_IDS.get(definition.agent, definition.agent)]
                continue
            definition.applied_agents = sorted(applied[(definition.raw_name, definition.endpoint_signature)])

    def definition_by_id(self, identifier: str) -> Definition | None:
        for definition in self.definitions:
            if definition.id == identifier:
                return definition
        return None

    def scan(self) -> dict[str, Any]:
        discoverers = (
            self.discover_claude, self.discover_codex, self.discover_opencode,
            self.discover_pi, self.discover_copilot,
            self.discover_antigravity,
        )
        try:
            for discover in discoverers:
                self.deadline.check()
                discover()
        except TimeoutError:
            self.truncated = True
            self.warnings.append({"code": "deadline", "sourceId": "inventory"})
        self.finalize()
        for agent in ("claude", "codex"):
            self.agent_status.setdefault(agent, {"id": agent, "support": "documented", "reason": "filesystem-configuration"})
        public = [item.public() for item in sorted(
            self.definitions,
            key=lambda item: (item.agent, item.scope, item.raw_name, item.source_path, item.id),
        )]
        return {
            "ok": True,
            "schemaVersion": SCHEMA_VERSION,
            "healthBasis": "configuration-only",
            "project": "<project>",
            "agents": [self.agent_status[key] for key in sorted(self.agent_status)],
            "definitions": public,
            "warnings": self.warnings,
            "truncated": self.truncated or self.deadline.truncated,
            "limits": {
                "sources": MAX_SOURCES,
                "definitions": MAX_DEFINITIONS,
                "warnings": MAX_WARNINGS,
                "profileFiles": MAX_PROFILE_FILES,
                "outputBytes": MAX_OUTPUT_BYTES,
                "deadlineMilliseconds": 2000,
            },
        }


def bounded_json(document: dict[str, Any]) -> str:
    document = dict(document)
    definitions = list(document.get("definitions", []))
    while True:
        document["definitions"] = definitions
        encoded = json.dumps(wire(document), ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        if len(encoded.encode("utf-8")) <= MAX_OUTPUT_BYTES:
            return encoded
        document["truncated"] = True
        if definitions:
            definitions.pop()
            continue
        fallback = {
            "ok": True,
            "schemaVersion": SCHEMA_VERSION,
            "healthBasis": "configuration-only",
            "definitions": [],
            "warnings": [{"code": "output-limit", "sourceId": "inventory"}],
            "truncated": True,
        }
        return json.dumps(fallback, separators=(",", ":"), sort_keys=True)
