from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from pathlib import Path
import re
import unicodedata
from typing import Any

from .records import fingerprint

SCHEMA_VERSION = 1
CORE_AGENT_IDS = {
    "claude": "claude-code",
    "codex": "codex",
    "opencode": "opencode",
    "pi": "pi",
    "github-copilot-cli": "copilot-cli",
    "google-antigravity": "antigravity",
}
MAX_FIELD_CHARS = 256
MAX_LABEL_CHARS = 128
MAX_PATH_PART_CHARS = 128
_UNSAFE_UNICODE_CATEGORIES = {"Cc", "Cf", "Cs", "Co", "Cn"}
_SECRET_SHAPES = (
    re.compile(r"(?i)\bbearer\s+\S+"),
    re.compile(r"(?i)(?:bearer|token|secret|password|passwd|api[_-]?key|authorization|credential)"),
    re.compile(r"\b(?:sk|ghp|github_pat)-?[A-Za-z0-9_]{12,}\b"),
)


def digest(*parts: str) -> str:
    value = "\0".join(parts).encode("utf-8", "surrogatepass")
    return hashlib.sha256(value).hexdigest()[:24]


def _safe_unicode(value: str, maximum: int) -> bool:
    return (
        0 < len(value) <= maximum
        and not any(unicodedata.category(character) in _UNSAFE_UNICODE_CATEGORIES for character in value)
        and not any(pattern.search(value) for pattern in _SECRET_SHAPES)
    )


def safe_label(value: object, identifier: str) -> str:
    text = str(value)
    if not _safe_unicode(text, MAX_LABEL_CHARS):
        return f"server-{identifier[:8]}"
    return text


def safe_path(value: str, identifier: str) -> tuple[str, bool]:
    prefix = ""
    remainder = value
    if value in {
        "<project>", "<codex-home>", "~",
    }:
        return value, False
    if value.startswith("<project>/"):
        prefix, remainder = "<project>/", value[10:]
    elif value.startswith("<codex-home>/"):
        prefix, remainder = "<codex-home>/", value[13:]
    elif value.startswith("~/"):
        prefix, remainder = "~/", value[2:]
    elif value.startswith("/"):
        prefix, remainder = "/", value[1:]
    safe_parts: list[str] = []
    redacted = False
    for index, part in enumerate(remainder.split("/")):
        part_id = digest("path-part-v1", identifier, str(index), part)
        if part not in {".", ".."} and _safe_unicode(part, MAX_PATH_PART_CHARS):
            safe_parts.append(part)
        else:
            safe_parts.append(f"segment-{part_id[:8]}")
            redacted = True
    result = prefix + "/".join(safe_parts)
    if len(result) > MAX_FIELD_CHARS:
        return prefix + "path-" + digest("path-value-v1", identifier, value)[:8], True
    return result, redacted


@dataclass
class Definition:
    agent: str
    raw_name: str
    scope: str
    source_kind: str
    source_path: str
    support: str
    transport: str
    enabled: bool | None
    trusted: bool | None
    valid: bool
    priority: int
    precedence_known: bool = True
    trust_gates_effective: bool = True
    trust_role: str = "source-approval"
    endpoint_signature: str | None = field(default=None, repr=False)
    secret_presence: dict[str, bool] = field(default_factory=dict)
    selected: bool | None = None
    shadowed_by: str | None = None
    duplicate_group: str | None = None
    metrics: dict[str, object] = field(default_factory=dict)
    raw_config: dict[str, Any] | None = field(default=None, repr=False, compare=False)
    absolute_path: Path | None = field(default=None, repr=False, compare=False)
    applied_agents: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.source_id = digest("source-v1", self.agent, self.source_kind, self.source_path)
        self.id = digest("definition-v2", self.agent, self.source_kind, self.source_path,
                         str(self.absolute_path or ""), self.raw_name, fingerprint(self.raw_config))

    @property
    def effective(self) -> bool | None:
        if not self.valid or self.enabled is False or self.selected is False:
            return False
        if self.trust_gates_effective and self.trusted is False:
            return False
        if self.enabled is None or self.selected is None or (self.trust_gates_effective and self.trusted is None):
            return None
        return True

    @property
    def state(self) -> str:
        if not self.valid:
            return "invalid"
        if self.enabled is False:
            return "disabled"
        if self.trust_gates_effective and self.trusted is False:
            return "untrusted"
        if self.selected is False:
            return "shadowed"
        if self.effective is None:
            return "unknown"
        return "enabled"

    def public(self) -> dict[str, Any]:
        source_path, source_redacted = safe_path(self.source_path, self.source_id)
        source = {
            "id": self.source_id,
            "kind": self.source_kind,
            "path": source_path,
            "redacted": source_redacted,
        }
        if self.absolute_path is not None:
            try:
                logical = self.absolute_path.absolute()
                target = self.absolute_path.resolve(strict=True)
                target_path, target_redacted = safe_path(target.as_posix(), self.source_id)
                if target != logical and not target_redacted:
                    source["realpath"] = target_path
            except (OSError, RuntimeError):
                pass
        return {
            "id": self.id,
            "agent": self.agent,
            "agentId": CORE_AGENT_IDS.get(self.agent, self.agent),
            "name": safe_label(self.raw_name, self.id),
            "scope": self.scope,
            "source": source,
            "support": self.support,
            "transport": self.transport,
            "enabled": self.enabled,
            "trusted": self.trusted,
            "trustRole": self.trust_role,
            "selected": self.selected,
            "shadowed": self.selected is False,
            "shadowedBy": self.shadowed_by,
            "duplicateGroup": self.duplicate_group,
            "effective": self.effective,
            "state": self.state,
            "health": "not-probed",
            "secretPresence": {
                "environment": bool(self.secret_presence.get("environment")),
                "headers": bool(self.secret_presence.get("headers")),
                "authentication": bool(self.secret_presence.get("authentication")),
            },
            "group": f"{self.agent}, {self.scope}",
            "metrics": self.metrics,
            "appliedAgents": list(self.applied_agents),
        }
