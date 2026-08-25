# AI Development Guidance

Read the applicable OpenSpec change before editing. Prompts, schemas, routing,
graphs, handlers, run/task state, and artifacts are owned here. Authentication,
permissions, visible history, gateway projection, and UI belong to
`Phytomni-Web`; do not duplicate them.

Use `src/mcp_server_phytomni/public_agent_catalog.py` as the canonical public
agent source. Regenerate `docs/contracts/agents/public-agent-catalog.v1.json`
after changes. Graph JSON under `graphs/manifests/` is generated; use
`visualize_agent_graphs.py --manifest` and `--check-manifest` rather than
hand-editing it.

Use `python scripts/ai_development.py inventory` for a read-only architecture
map and `verify-plan <changed paths...>` for scoped checks. The complete local
gate is `make full`. Never infer production activation, external CI, secrets,
live-provider behavior, deployment approval, or release approval from local
tests.

Read `ai-feature-templates.md` and `operational-authority.md` before adding
public behavior.
