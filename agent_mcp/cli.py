from __future__ import annotations

import argparse
import json
import sys

from fileblade_inventory import SCOPES, WatchPlan

from .apply import Applier
from .inventory import Inventory, bounded_json
from .model import SCHEMA_VERSION

MAX_RESTORE_PAYLOAD_BYTES = 1024 * 1024


def stdin_payload() -> tuple[str, str]:
    payload = sys.stdin.readline(MAX_RESTORE_PAYLOAD_BYTES + 2)
    if payload.endswith("\n"):
        payload = payload[:-1]
    if not payload:
        return "", "restore payload is missing"
    if len(payload.encode("utf-8")) > MAX_RESTORE_PAYLOAD_BYTES:
        return "", "restore payload exceeds its byte limit"
    return payload, ""


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="agent-mcpctl")
    commands = result.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list", help="list configuration-only MCP definitions")
    listing.add_argument("--project", required=True)
    listing.add_argument("--json", action="store_true", required=True)
    listing.add_argument("--watch", action="store_true", help="Include native source directories for the host's private watch transport")
    listing.add_argument("--scope", choices=SCOPES, default="all")
    applying = commands.add_parser("apply", help="write or remove one definition in an agent's user config")
    applying.add_argument("--project", required=True)
    applying.add_argument("--id", required=True)
    applying.add_argument("--agent", action="append", required=True)
    applying.add_argument("--state", choices=("on", "off"), required=True)
    applying.add_argument("--json", action="store_true", required=True)
    for command in ("remove", "prepare-remove", "remove-prepared"):
        removing = commands.add_parser(command, help="prepare or perform removal of a listed definition")
        removing.add_argument("--project", required=True)
        removing.add_argument("--id", required=True)
        removing.add_argument("--json", action="store_true", required=True)
        if command == "remove-prepared":
            removing.add_argument("--payload-stdin", action="store_true", required=True)
    restoring = commands.add_parser("restore", help="write a removed definition payload back into its source config")
    restoring.add_argument("--project", default="")
    restoring.add_argument("--record-id", required=True)
    restoring.add_argument("--payload-stdin", action="store_true", required=True)
    restoring.add_argument("--json", action="store_true", required=True)
    return result


def main(arguments: list[str] | None = None) -> int:
    options = parser().parse_args(arguments)
    if options.command == "list":
        try:
            if options.watch:
                with WatchPlan() as plan:
                    document = plan.finish(Inventory(options.project, scope=options.scope).scan())
            else:
                document = Inventory(options.project, scope=options.scope).scan()
            output = bounded_json(document)
        except (OSError, ValueError, TimeoutError):
            sys.stdout.write('{"ok":false,"error":"Inventory failed, not probed","definitions":[],"healthBasis":"configuration-only","schemaVersion":1,"truncated":true,"warnings":[{"code":"inventory-failed","sourceId":"inventory"}]}\n')
            return 1
        sys.stdout.write(output + "\n")
        return 0
    if options.command in ("apply", "remove", "prepare-remove", "remove-prepared", "restore"):
        try:
            applier = Applier(Inventory(options.project))
            if options.command == "apply":
                document = applier.apply(options.id, options.agent, options.state)
            elif options.command in ("remove", "prepare-remove"):
                document = applier.remove(options.id, prepare=options.command == "prepare-remove")
            elif options.command == "remove-prepared":
                raw_payload, payload_error = stdin_payload()
                expected = json.loads(raw_payload) if not payload_error else None
                document = applier.remove(options.id, expected_payload=expected) if isinstance(expected, dict) else applier.failure("prepared recovery payload is missing or invalid")
            else:
                raw_payload, payload_error = stdin_payload()
                document = applier.failure(payload_error) if payload_error else applier.restore(options.record_id, raw_payload)
        except (OSError, ValueError, TimeoutError):
            document = {
                "ok": False,
                "schemaVersion": SCHEMA_VERSION,
                "project": "<project>",
                "changed": False,
                "message": "apply failed",
                "results": [],
            }
        sys.stdout.write(json.dumps(document, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n")
        return 0 if document["ok"] else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
