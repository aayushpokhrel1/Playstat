## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).

## Design Context

This project has [PRODUCT.md](PRODUCT.md) (register: product, platform: web — who Playstat is for, why it exists, brand personality) and [DESIGN.md](DESIGN.md) (visual system: near-black terminal surface, one signal-green accent, Geist Sans/Mono). Read both before designing or editing any UI in `web/`. Managed by the `impeccable` skill — use `/impeccable <command>` for design work.
