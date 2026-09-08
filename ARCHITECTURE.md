# MCP architecture

MCP inventories configuration without starting servers or probing endpoints.
Public rows omit commands, arguments, environment values and authentication
headers. FileBlade's shared tree owns presentation, navigation, filtering and
metrics. Semantic rows can open their source but cannot rename or trash the
whole configuration file. Bin dialogs inherit the blade's focus and key state.

The provider service owns FileBlade's shared `ArtifactInventory`. Panes attach
as observers; the runtime handles discovery, bounds, errors, ordinary apply
actions and external-change subscriptions. Reads and watches stop with the last
pane. Accepted apply actions retain their original project after pane closure.
The declared native helper's `list --watch` adds bounded native directory paths
for private host transport. Normal listing stays redacted. Configuration-only
health labels and source aliases remain unchanged; actions use the captured
native project anchor. No servers are started or endpoints probed.

Copying between agents uses a portable server description. Undo keeps the
original definition instead. New recovery records preserve the JSON map path,
entry fields and position, including flat adapter maps and OpenCode v1 maps.
TOML records retain the original table text, comments, unknown fields and typed
values. With an unchanged remainder, restore reproduces the original bytes.
After unrelated edits, it appends the retained block and verifies that only the
intended definition was added. Ambiguous or dispersed tables are refused.

Row IDs bind the complete typed definition and native source identity, including
the project behind a display alias. A stale selection cannot remove or copy
its replacement. Removal rechecks the source definition after discovery;
restore refuses a conflicting entry or retargeted source symlink and is
idempotent after success. JSON writes require unique members and finite numbers.
Retained raw Codex fields are restored rather than reduced to the portable
description. Missing historical fields cannot be reconstructed.

A restore writes only to a configuration file this helper itself recorded.
Removal mints a recovery record under the state directory, private to the user,
and reports its identifier; `restore` accepts a payload only when it matches one
of those records, takes the destination from the record rather than from the
payload, and refuses any path that the current scan does not know as a
configuration file for that agent. A payload naming an arbitrary file is refused
without writing. The record also carries the project and home it was taken in,
so a restore reaches the same sources even when the caller passes no project.
Preparing and then removing reuse one record, the record is durable before the
source is touched, and a full store refuses a new removal rather than evicting
an undo that has not been used. The earlier free-form payload format, which carried its own
destination, is gone, so recovery records prepared before this change cannot be
replayed; the removal itself is still listed in the core bin.

`tests/run` covers discovery, redaction, copying, exact recovery, conflicts,
malformed records, native source paths through the core bin, keyboard contracts
and read-only helper imports in development and installed layouts.

Core owns logical removal and restore independently of the pane. `prepare-remove`
returns exact recovery data without writing; `remove-prepared` rejects a changed
definition. The complete record is bounded and durably stored before removal,
including the helper route needed for restore without an open pane. Uncertain
completion retains it; only confirmed idempotent restore removes it.

Configuration writers capture file identity and original bytes, then use the
core's descriptor-relative mutation boundary. Private staging, durable recovery
intents and no-replace publication preserve intervening entries. Existing
permission bits and source symlinks are retained; new files are private `0600`.

## Missing host

`Service.qml` loads `HostGuard.qml` once the shell injects `pluginRegistry`.
`HostGuard.js` decides from the registry alone: nothing shows while
`data-goblin.fileblade` is installed and enabled; otherwise the alphabetically
first enabled plugin that declares a `data-goblin.fileblade/*` extension owns
one overlay listing every waiting extension. Install runs detached through
`sh -c` because the clone landing in the plugins directory hot-reloads every
third-party plugin, guard included: `omarchy plugin add --enable --yes` (or
`omarchy plugin enable` when the host is installed but disabled), a wait for
the entry in `shell.json` and the host IPC target, then
`omarchy restart shell`. Failure raises a critical notification with the last
error line and rescans plugins so a fresh guard reappears. The close glyph or
Escape hides it until the next shell start. The card reuses the host's look:
`assets/fileblade-logo.png` tinted with the accent colour, and the welcome
tab's accent Install button. `tests/tst_host_guard.qml` covers the
decision table offscreen.
