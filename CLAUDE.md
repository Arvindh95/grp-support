# grp-support

## Code context lookup

Always consult `graphify-out/` first when locating code, tracing call paths, or building mental model of this repo. Files:
- `graphify-out/GRAPH_REPORT.md` — communities, god nodes, surprising edges
- `graphify-out/graph.json` — full nodes/edges, queryable
- `graphify-out/manifest.json` — file inventory

Use these before grep/glob — they encode call graph, ownership, and clusters that raw search misses. Re-run `/graphify` if the codebase changed materially since last snapshot.
