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
an undo that has not been used. A confirmed restore marks its record used, so it
stops holding a place while a repeat restore still answers; used records are
dropped after a week. Unused records are kept for ten years, which is the widest
bin retention the core allows, so the helper never expires an undo the bin still
lists. Sixty-four unused removals is the limit. The earlier free-form payload format, which carried its own
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

This file was written by an agent.

## Provider ownership and host availability

The blade contribution declares `provider: "Provider.qml"`. FileBlade creates
one nonvisual provider per enabled extension identity and shares it across its
views. It supplies `providerId`, the canonical absolute `providerRoot`, `files`,
and `inventoryComponentUrl` at construction. The provider exposes `inventory`,
`error`, `observers`, `viewCount`, `attach(context)`, `detach(context)`, and
`shutdown()`. Construction stays idle. Duplicate attachment is harmless, the
last detach suspends the shared inventory, and shutdown unloads it and refuses
late attachments. Existing inventory options and module contract 2 are preserved.

`Service.qml` resolves its directory from its own QML URL, even when the shell
strips the manifest's source directory. It lazily delegates to the same provider
only when an older FileBlade calls attach. The new host owns its provider directly,
so its companion Service never creates a second inventory. A legacy companion
without provider metadata still requires its actual old-shell service; a new
host on a restricted shell must request an extension update instead of loading
that old Service itself. Updating companions alone cannot repair an old core on
the restricted shell.

The missing-host card no longer inspects foreign registry entries.
`bin/fileblade-host-status` reads `omarchy plugin list --json`, validates unique
IDs and Boolean enabled states, then checks the enabled host with
`omarchy-shell data-goblin.fileblade status`. Each command has a two-second
deadline, 128 KiB stdout and 4 KiB stderr limits, and process-group cleanup.
Listings are limited to 512 rows; the helper returns only the four known companion
names and states. It reads no agent configuration and downloads nothing.

Missing, disabled, starting, ready, and unknown states stay distinct. Only a
confirmed disabled host offers the existing explicit Enable action. Starting
or failed checks never offer installation or enablement. The first enabled
companion in a successful listing owns the card. Polling backs off to 30 seconds,
and dismissal stops checks until the next shell start. This is presentation;
FileBlade's catalog separately controls permission to load providers and helpers.

The QML lifecycle and guard tests plus the standalone host-check tests are part
of `tests/run`. They cover cold creation, shared observers, last detach, terminal
shutdown, stripped manifests, both provider ownership paths, unavailable commands,
malformed authority, output limits and timeouts. User-visible expectation: an
enabled responding FileBlade produces no missing-host card on either shell API.
