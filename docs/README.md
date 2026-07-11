# Documentation

Phytomni-Bot's repository-level documentation is organized
[Diátaxis](https://diataxis.fr/)-style: `reference/` is for looking
something up, `explanation/` is for understanding how and why the
system is built the way it is, `guides/` walks through a task end to
end, `ops/` covers operating a running deployment, and `design/` holds
proposals and ADRs for work that is planned but not yet implemented.
Start from the table below, or browse a category directly.

## I want to… → Read

| I want to…                        | Read                                                               |
| --------------------------------- | ------------------------------------------------------------------ |
| Look up an environment variable   | [reference/configuration.md](reference/configuration.md)           |
| Look up an HTTP endpoint          | [reference/http-api.md](reference/http-api.md)                     |
| Look up an MCP tool               | [reference/mcp-tools.md](reference/mcp-tools.md)                   |
| Look up a CLI command             | [reference/cli.md](reference/cli.md)                               |
| Understand the architecture       | [explanation/architecture.md](explanation/architecture.md)         |
| Understand the agent graphs       | [explanation/agent-graphs.md](explanation/agent-graphs.md)         |
| Deploy, or build a customer image | [guides/deployment.md](guides/deployment.md)                       |
| Set up for local development      | [guides/development.md](guides/development.md)                     |
| Operate the HTTP service          | [ops/http-api-runbook.md](ops/http-api-runbook.md)                 |
| Upgrade 0.1.2 → 0.1.3             | [ops/upgrading.md](ops/upgrading.md)                               |
| Upgrade 0.1.1 → 0.1.2             | [ops/upgrading.md](ops/upgrading.md)                               |
| See release history               | [../CHANGELOG.md](../CHANGELOG.md)                                 |
| Look up the lint-waiver ledger    | [development/lint-exemptions.md](development/lint-exemptions.md)   |
| Read a design proposal or ADR     | [design/etl-web-mysql-history.md](design/etl-web-mysql-history.md) |
| Check Web cutover status          | [ops/web-cutover-checklist.md](ops/web-cutover-checklist.md)       |

## Where docs live

- `reference/` — look something up: CLI flags, configuration
  variables, HTTP routes, and MCP tool schemas.
- `explanation/` — understand how and why: package layout, MCP
  dispatch, and agent graph composition.
- `guides/` — how-to: local development setup and deployment or
  customer-image distribution.
- `ops/` — operate a running deployment: the HTTP runbook, upgrade
  notes, and cutover status.
- `design/` — proposals and ADRs for work that is planned but not yet
  implemented.
