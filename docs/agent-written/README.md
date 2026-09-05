# Agent MCP for Omarchy Fileblade

`data-goblin.fileblade-mcp` is an independent Omarchy Quattro plugin that contributes an
MCP configuration inventory to the `data-goblin.fileblade/blade` extension socket, with
an agents strip that copies one server definition into the user-scope config of
other installed agents. Listing is read-only; writes require explicit apply or remove/restore actions. It is an unofficial compatibility project and
is not affiliated with Anthropic, Google, GitHub, OpenAI, OpenCode, or Pi.

## Required dependency and Install order

**Omarchy Fileblade (`data-goblin.fileblade`) must be installed and enabled first.**

Requires Omarchy 4.0.2 or later, core FileBlade and Python 3.11+. Follow the direct GitHub installation
and removal instructions in the [root README](../../README.md), then select
MCP in a FileBlade module slot. No marketplace listing is required.

The contribution uses `extensions["data-goblin.fileblade/blade"]` with
`hostContract: 2`. Its service supplies the missing-host prompt and provider
lifecycle. With FileBlade unavailable, the shared guard offers to install or
enable the host. It does not install anything without an explicit action.
Inventories reuse FileBlade's shared services; see the
[host contract](https://github.com/data-goblin/fileblade/blob/main/EXTENSIONS.md).
Removing the extension preserves host layout/view state, recoverable bins and
previous changes to the user's files or agent configuration.

## Supported source contract

Only the following documented, on-disk sources are read. “Later wins” applies
to a same-name definition only where the owning project documents it.

| Agent | Sources and precedence (low → high) | Disable/trust treatment |
| --- | --- | --- |
| Claude Code | plugin config; user `~/.claude.json`; project `.mcp.json`; local project record in `~/.claude.json`; exclusive `/etc/claude-code/managed-mcp.json` | Project approval lists are retained. Plugin enable maps and root-owned managed name policy are applied. URL/command policy requiring substitution is unknown. |
| Codex | user `~/.codex/config.toml`; trusted ancestor `.codex/config.toml`; `$CODEX_HOME/*.config.toml` profile files; cached installed plugin manifests under `~/.codex/plugins/cache/` | `enabled` and per-plugin MCP controls are retained. Profile rows have unknown enabled/selection state and can never be reported effective because `--profile` activation is not observed. The active cached plugin version and other CLI overrides are unknown. |
| OpenCode | V1/V2 global `~/.config/opencode/opencode.json[c]`; ancestor project files; V2 ancestor `.opencode/opencode.json[c]` | `enabled`/`disabled` is retained. V2 loads all direct ancestors before all `.opencode` ancestors; V1 stops at the Git root. No system scope is inferred. |
| Pi | No native MCP. With an explicitly enabled `pi-mcp-adapter`: shared globals, Pi global override, project `.mcp.json`, then `.pi/mcp.json`. With `pi-codemode-mcp`: its four documented user/project files. | Without one of those adapters Pi is reported `unsupported`; adapter `disabled` state is retained. |
| GitHub Copilot CLI | user `~/.copilot/mcp-config.json`; ancestor `.github/mcp.json` then `.mcp.json` (closer wins); installed plugin MCP (higher); session `--additional-mcp-config` is unobserved | Folder trust is `null`; `disabledMcpServers`, plugin enable maps, and secure `/etc/github-copilot/managed-settings.json` name-only policy are applied. Plugin ties remain unknown because load order is not safely derivable. |
| Google Antigravity | global `~/.gemini/config/mcp_config.json`; workspace `.agents/mcp_config.json`; CLI plugin definitions at `~/.gemini/antigravity-cli/plugins/<plugin_name>/mcp_config.json` | `disabled` is retained. Global/workspace/plugin precedence and plugin enabled state are undocumented, so conflicts and plugin effectiveness remain `null`. `serverUrl` transport is `unknown` unless an explicit supported type disambiguates it. |

Primary references: [Claude MCP](https://code.claude.com/docs/en/mcp),
[Claude managed MCP](https://code.claude.com/docs/en/managed-mcp), and
[Claude plugins](https://code.claude.com/docs/en/plugins-reference);
[Codex MCP](https://developers.openai.com/codex/mcp/),
[Codex configuration](https://developers.openai.com/codex/config-reference/), and
[Codex plugins](https://developers.openai.com/plugins/build/plugins);
[OpenCode configuration](https://opencode.ai/docs/config/),
[OpenCode MCP](https://opencode.ai/docs/mcp-servers/), and
[OpenCode V2 configuration](https://opencode.ai/v2/docs/config/);
[Pi core](https://github.com/badlogic/pi-mono/tree/main/packages/coding-agent),
[Pi MCP Adapter](https://github.com/nicobailon/pi-mcp-adapter), and
[Pi Code Mode MCP](https://github.com/mitsuhiko/pi-codemode-mcp);
[Copilot CLI MCP](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-mcp-servers),
[Copilot plugin reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-plugin-reference), and
[Copilot config reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-config-dir-reference);
[Antigravity MCP](https://antigravity.google/docs/mcp/) and
[Antigravity CLI plugins](https://antigravity.google/docs/cli/plugins/).

Every schema-version-1 definition carries stable `id`, `agent`, `source`,
`scope`, `support`, `transport`, `enabled`, `trusted`, `trustRole`, `effective`,
`selected`, `shadowed`, duplicate state, and `health`. Health is always
`not-probed`; the plugin never turns configuration into a connectivity claim.
`source.redacted` records whether a path segment required security redaction.
Reversible root aliases, `~`, `<project>` and `<codex-home>`, do not set it. Redacted sources cannot be
opened or revealed by the UI.

## Column control, sorting, and filtering

The header's gray column label is Fileblade's shared column control, driven by
one `PaneView` that this module loads through `context.ui.url("PaneView")` and
exposes as `view` for the host tab bar. Metric options declare a `kind`:

```yaml
agents:                       kind agents (default; shows the agents strip instead of a value)
status, transport, summary:   kind text (sorts ascending first)
updated, created:             kind date (sorts descending first; filter takes since/until)
tokens, characters, words,
bytes:                        kind number (sorts descending first; filter takes min/max; data bar)
```

- Left click on the label cycles the sort on the current metric: descending,
  ascending, off (text starts ascending). The label shows the short label plus
  an arrow while sorted
- Right click, Enter, Space, or Menu opens the popup: metric radio list, Sort
  ascending, Sort descending, Clear sort, Filter…, Clear filter
- Filter… opens the filter popup. Date options take since/until (ISO prefix,
  `7d`/`3w`/`2m`/`1y`, `today`, `yesterday`, presets 7d/30d/90d/1y); number
  options take min/max with `k`/`m` suffixes. `f` in the tree opens it, `s`
  toggles the sort. A filter glyph appears on the label while a filter is active
- Metric, sort, and filter persist in the blade's Fileblade state

Leaves sort inside their agent/scope group; the filter runs before grouping.
Status is the derived definition state and Transport the detected transport.
Summary is existing display context (the source path and transport), not a
description inferred from configuration.

File-backed metrics describe the bounded source configuration that produced a
definition: Updated is its modification time, Created is its Linux birth time
when the filesystem supplies one, and Size is the exact bytes read. Characters
counts decoded Unicode code points, Words uses Unicode-aware word matching,
and Tokens is deliberately labelled as an estimate (`ceil(UTF-8 bytes / 4)`).
Definitions from the same source therefore share these source metrics. Missing
or unsupported values are shown as an em dash by Fileblade.

## Agents strip

With the Agents metric selected every leaf shows Fileblade's `AgentStrip`: one
brand mark per installed agent (`files.installedAgents`, detected by the core
host from a binary on PATH or the agent home) plus a robot glyph for "all". A
mark is lit when that agent is applied. Each definition carries `agentId` (the
core id of its own agent) and `appliedAgents`: every core agent id that has a
definition with the same server name and the same endpoint signature (same
command and args, or same URL) anywhere in the inventory.

```yaml
inventory agent -> core id:
  claude: claude-code
  codex: codex
  opencode: opencode
  pi: pi
  github-copilot-cli: copilot-cli
  google-antigravity: antigravity
```

- Click a mark: `apply --agent <id> --state on|off` for that one agent
- Click the robot: turns on every installed agent that is not yet applied.
  When all of them are applied it turns every installed agent off except the
  definition's own agent
- While an apply runs the header status reads `Applying…`. On success the
  inventory rescans. When any agent is refused the header shows the backend
  message until the next rescan (Shift+R or the refresh button); changed
  agents are still rescanned

## Apply command

```
agent-mcpctl apply --project <dir> --id <definition id> --agent <agent id> [--agent <id> ...] --state on|off --json
```

`--agent` repeats; `all` expands to every write-capable agent; inventory names
are accepted as aliases for core ids. Output is one JSON line:

```yaml
ok: true when every requested agent succeeded
schemaVersion: 1
project: "<project>"
changed: true when any file changed
message: "" or "<agent>: <reason>; ..." for refused agents
results:
  - agent: core id
    ok: bool
    changed: bool
    message: already present | written to <container> | removed from <container> | written table | removed table | not present | <refusal>
    touched: [logical paths such as "~/.codex/config.toml"]
```

Exit code is 0 when `ok`, otherwise 1. `apply` rescans the inventory, finds the
definition by `id`, and re-reads the source entry from its source file at that
moment. The redacted `list` row is never an input. Env and header values are
copied verbatim into the target file and never appear on stdout or in messages.

Targets, one user-scope file per agent (`CODEX_HOME` is honored the same
way as in `list`):

```yaml
claude-code:  ~/.claude.json                       mcpServers.<name>
              stdio:  { type: "stdio", command, args, env? }
              sse:    { type: "sse", url, headers? }
              http:   { type: "http", url, headers? }
codex:        ~/.codex/config.toml                 [mcp_servers.<name>] table
              stdio:  command, args?, [mcp_servers.<name>.env]
              http:   url, [mcp_servers.<name>.http_headers]
              sse:    refused (Codex documents stdio and streamable HTTP only)
opencode:     ~/.config/opencode/opencode.json     mcp.servers.<name> (v2)
              stdio:  { type: "local", command: [command, ...args], environment? }
              remote: { type: "remote", url, headers? }
              a file whose mcp is a non-empty v1 map without servers is edited in place as mcp.<name>
pi:           ~/.pi/agent/mcp.json when pi-mcp-adapter is enabled in ~/.pi/agent/settings.json,
              ~/.pi/agent/.mcp.json when only pi-codemode-mcp is; refused when neither is
              stdio:  { command, args?, env? }      remote: { url, headers? }
copilot-cli:  ~/.copilot/mcp-config.json           mcpServers.<name>
              stdio:  { type: "local", command, args, env?, tools: ["*"] }
              remote: { type: "http" | "sse", url, headers?, tools: ["*"] }
antigravity:  ~/.gemini/config/mcp_config.json     mcpServers.<name>
              stdio:  { command, args?, env? }      remote: { serverUrl, headers? }
```

What `on` does: writes the rendered entry under `<name>` when that name is
absent, and reports `already present` when the target already holds exactly
that entry or when the target file is the definition's own source. It never
overwrites: a same-name entry with different content is a refusal. What `off`
does: deletes the `<name>` entry and reports `not present` when there is none.

Refusals (no write happens, `ok: false` for that agent):

- the target already holds a different server under the same name
  (`a different server named <name> already exists in <container>; nothing
  was changed`); remove or rename it by hand first
- the source transport is `websocket`, `unix`, or `unknown`, or the source
  entry has no usable command/url
- the target agent does not support the transport (Codex with SSE)
- the target exists but is not a regular file (symlinks are not followed) or
  is unreadable or oversized
- a JSON target is not strictly parseable (JSONC comments or trailing commas)
- the Codex file is not valid TOML, the server exists but its
  `[mcp_servers.<name>]` header cannot be located exactly once (inline tables,
  dotted keys, duplicate headers), or the edited text would not parse back to
  the intended table
- `off` for the definition's own source entry
- an unknown agent id, an unknown definition id, or Pi without an MCP adapter

Writes are atomic: the new content goes to a temporary file beside the target,
is fsynced, keeps the target's mode (0600 for a new file), and replaces the
target with `rename`. JSON is written with 2-space indentation and a trailing
newline; key order is preserved. TOML edits touch only the lines of that one
server table (plus its `env`/`http_headers` subtables) and append the new table
at the end of the file.

## Search syntax

The filter field is Fileblade's shared `PaneSearchField` with the FILES search
syntax: bare words match anywhere (case-insensitive), `"quoted text"` matches
exactly, `-word` excludes, `name:x` matches the row label only, and the
field keys shown in the placeholder (`agent, scope, transport, status`) take exact comma-separated
values, negatable with a leading `-` (`transport:stdio -status:disabled`). Unknown keys are searched as
plain text.

## Security boundary

At runtime the plugin starts only its bundled Python helper, as `list` for the
inventory and for explicit apply or prepare/remove/restore operations. `list`
only reads. `apply` writes exactly one user-scope configuration file per
requested agent, re-reads secret env and header values from the source file
instead of the redacted row, copies them verbatim into the target, and never
prints them. The helper:

- never connects to a network endpoint or opens a browser;
- never starts an MCP server, agent CLI, shell, configured command, or secret
  provider;
- never expands environment, file, command, or template substitutions;
- accepts documented environment paths only as non-empty, NUL-free strings of
  at most 4096 characters, preserving accepted Unicode and whitespace exactly;
- never reads OAuth/token stores;
- never emits URLs, commands, arguments, environment names or values, headers,
  bearer strings, tokens, OAuth values, or raw parser errors;
- opens configuration through bounded, nonblocking, no-follow descriptors and
  accepts regular files only; default managed/system settings additionally
  require the trusted owner and reject group- or world-writable modes; and
- caps sources, definitions, warnings, field sizes, nesting, runtime, and final
  JSON output before QML retains it; and
- imports no subprocess, socket, or urllib module in either command.

The UI loads Fileblade's shared `PaneHeader`, `PaneSearchField`, and
`ArtifactTree`, plus `PaneView` for column state; its own dynamic text uses
`Text.PlainText`. The inventory runs only while its Fileblade slot is open and
expanded. Close, collapse, timeout, project changes, overlapping refreshes, and
destruction cancel or invalidate the current generation. Only one apply runs
at a time and it is started solely by a click in the agents strip.

Keyboard behavior is delegated to Fileblade's shared `ArtifactTree` and search
field: `j`/`k` and arrows move; `h`/`l` and Left/Right navigate groups; `g` or
`gg` or Home goes first; `G` or End goes last; Ctrl+D/Ctrl+U and
PageDown/PageUp page; `/` searches; `f` opens the filter popup; `s` toggles the
sort; Enter/`o` opens the source file through
Fileblade; `r` reveals its containing directory; Escape backs out; and plain
Tab/Shift+Tab request focus between slots. The module itself accepts only exact
Shift+R to rescan. Ctrl+Tab, Ctrl+Shift+Tab, Ctrl+PageUp, Ctrl+PageDown,
Ctrl+brackets, and Alt+Z are not accepted here, so Fileblade and the shell keep
their host-level behavior. Refresh and row mouse actions have keyboard
equivalents through the same routes.

## Dependencies and effects

- Runtime: Python 3.11 or later, Qt/Quickshell supplied by Omarchy, and an
  installed/enabled `data-goblin.fileblade`.
- Network: none.
- Writes outside the checkout: explicit apply/remove/restore actions update
  agent configuration atomically. The host retains durable recovery records.
- Credentials handled: `list` detects secret-bearing fields only as booleans
  and discards them from output; `apply` copies env and header values from the
  source file into the target file without printing them.
- Privileged operations, installers, package managers, services, and system
  configuration changes: none.
- Removal retains FileBlade view state and recoverable bins. Entries written
  by `apply` stay in agent configurations until explicitly removed.

## Limitations

- Configuration supplied only through command-line flags, process environment,
  fetched organization endpoints, runtime plugin transforms, live sessions, or
  hosted policy is deliberately unobserved. The documented `CODEX_HOME` root
  is followed without expanding substitutions inside configuration values.
- Project trust/approval is `null` when its documented persisted state cannot
  be established without running the owning agent.
- OpenCode V2 is labelled beta. Pi support is adapter-backed, not native.
- Codex profile activation and cached plugin active-version choice, Copilot
  plugin tie order and `--additional-mcp-config` remain unknown rather than
  guessed.
- Same-name Antigravity global/workspace/plugin precedence and CLI plugin
  enabled state are undocumented and remain unresolved rather than guessed.
- Source paths and display names preserve safe Unicode exactly, without
  normalization. Unsafe Unicode categories, secret-shaped values, dot path
  segments, and overlong segments are replaced by stable hash placeholders;
  any affected source is intentionally non-actionable.
- Leaf symlinks, FIFOs, devices, oversized files, invalid encodings, and
  malformed documents are skipped with bounded error codes.
- The inventory does not prove that a server exists, authenticates, starts, or
  is healthy.
- `apply` copies command/args/env or url/headers only. Codex
  `bearer_token_env_var`, `env_http_headers`, and `env_vars`, OpenCode
  `timeout` and `oauth`, and Antigravity
  `oauth`/`authProviderType` are not translated; agents whose default
  transport differs may need those set by hand.
- `appliedAgents` is a same-name plus same-endpoint match over the inventory,
  which includes project and plugin scopes; a mark can be lit by a project
  entry that `apply --state off` (user scope only) does not remove.

## Development checks

```bash
LC_ALL=C omarchy plugin validate .
qmllint Service.qml components/PlainText.qml components/RescanKeyContract.qml blades/Module.qml
./tests/run
find . -path './.git' -prune -o -type l -print
git diff --check
```
