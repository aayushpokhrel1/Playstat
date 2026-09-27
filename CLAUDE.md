## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).

## Design Context

This project has [PRODUCT.md](PRODUCT.md) (register: product, platform: web — who Playstat is for, why it exists, brand personality) and [DESIGN.md](DESIGN.md) (visual system: near-black terminal surface, one signal-green accent, Geist Sans/Mono). Read both before designing or editing any UI in `web/`. Managed by the `impeccable` skill — use `/impeccable <command>` for design work.

## Where things are written down

| File | What it holds |
| --- | --- |
| `README.md` | What it is, the stack, how to run it |
| `PRODUCT.md` | Product truth: who it is for, decisions, roadmap, what is deferred and why |
| `DESIGN.md` | The visual system: tokens, type, colour, component patterns |
| `docs/superpowers/` | Per-feature specs and plans, written before building |
| `CLAUDE.md` | This file |

There is no `HANDOVER.md` here. If one is ever added it holds **current state only** - where
things stand, what is half-done, what is next. Before adding a line to any such file, ask: will
this still be true in a month? If yes it belongs in one of the docs above, or in the vault.

## When Aayush says "update"

"Update the docs", "update everything", or just "update" means **all of it, in this turn**:

1. **The knowledge vault** - `C:\Users\aayus\Documents\Knowledge-Vault\Projects\Playstat\`
   (the path is per-machine), and **no `Playstat/` folder exists yet** - create `Projects/Playstat/index.md` and link it from the vault's `Projects/index.md`. Add what this session learned that is worth keeping: a
   decision and its WHY, a non-obvious gotcha or fix, a research finding, a cross-project
   learning. This is the part that gets forgotten, and it is the part that compounds.
2. **Every doc in this repo**, not only the one already open.

**Vault writes go through WSL, and note content must never appear on the command line.** The Bash
tool re-quotes the wrapper, so backticks and apostrophes inside a note get executed or break the
command. Write the note to a file first, then pass only literal paths:

```
wsl -d Ubuntu -- bash -lc 'cat /mnt/c/<tmp>/note.md >> /mnt/c/Users/aayus/Documents/Knowledge-Vault/Projects/Playstat/index.md'
```

**Updating docs means making them TRUE, not just appending what shipped.** Correct or strike a
stale claim where it sits rather than adding a newer entry underneath it, because the next reader
may hit the old one first. Cross-check every number against reality instead of trusting the file.

## Where knowledge goes

A lesson has exactly one home, chosen by how far it reaches:

- **A rule about specific code goes in a comment AT that code.** The most reliable form there is:
  you cannot edit the function without reading the warning above it.
- **A lesson that generalises goes in the vault**, phrased so it is useful on a different
  project, with this one as the example.
- **A dated narrative of what you did today goes in `git log`.** It is already there, in detail.

Keep any one lesson in a single place. Two copies drift, and the drift causes wrong work later.
