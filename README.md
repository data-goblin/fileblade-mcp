<p align="center"><img src="assets/fileblade-extension-logo.svg" alt="FileBlade extension" width="640"></p>

---

**FileBlade Agent MCP** shows the MCP servers your agents are configured to use in [FileBlade](https://github.com/data-goblin/fileblade), which gives you IDE-like sidebars for Omarchy. 

<p align="center"><img src="assets/mcp-blade.png" alt="Agent MCP blade" width="640"></p>

The purpose of this extension is to inventory the MCP server configuration of your installed agents from their documented config files. Listing is read-only and it never starts a server. It supports:
- Codex
- Claude Code
- OpenCode
- Pi (through its MCP adapter)
- Copilot CLI
- Antigravity

> [!NOTE]
> Please submit a PR if you want support for other agents. This is an unofficial compatibility project, not affiliated with any agent vendor.

Agent MCP groups servers by agent and scope in a FileBlade filetree, for the project selected in the file tree. You can use the extension to see which servers each agent knows about and to consolidate them by agent: one click on an agent mark copies a server definition into that agent's user-scope config through an explicit apply step.

<p align="center"><img src="assets/mcp-agents.gif" alt="Copying an MCP server to one agent, to all agents, and removing it again" width="640"></p>

## Installation / Quick-start

Until this extension appears in the Omarchy marketplace, install it directly
from GitHub. Requires Omarchy 4.0.2 or later, FileBlade and Python 3.11+. FileBlade includes its
bundled x86-64 Linux binary; no separate extension binary or build is needed.

1. [Install FileBlade](https://github.com/data-goblin/fileblade#installation--quick-start) first.

2. Install and enable this extension:

   ```bash
   OMARCHY_SHELL_IPC_TIMEOUT=10s omarchy plugin add https://github.com/data-goblin/fileblade-mcp.git --enable
   ```

3. Restart the shell after installation finishes:

   ```bash
   omarchy restart shell
   ```

4. Select MCP in a FileBlade module slot.

To remove this extension:

```bash
omarchy plugin remove data-goblin.fileblade-mcp
omarchy restart shell
```

Removing the extension preserves your files, previous agent configuration
changes and FileBlade's saved state and recoverable bins.

This concise README is human-written to convey the simple intent and purpose of this extension. Full (agent-written) docs are in [docs/agent-written/README.md](docs/agent-written/README.md); design notes in [ARCHITECTURE.md](ARCHITECTURE.md). 

Building your own extension is covered in FileBlade's [EXTENSIONS.md](https://github.com/data-goblin/fileblade/blob/main/EXTENSIONS.md).

### FileBlade Repos

- [FileBlade core](https://github.com/data-goblin/fileblade)
- [Memory](https://github.com/data-goblin/fileblade-memory)
- [Skills](https://github.com/data-goblin/fileblade-skills)
- [MCP](https://github.com/data-goblin/fileblade-mcp)
- [Hooks](https://github.com/data-goblin/fileblade-hooks)

## License

MIT, see [LICENSE](LICENSE).
