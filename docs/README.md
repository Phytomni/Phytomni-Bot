# Documentation

Phytomni-Bot's repository-level documentation is organized
[Diátaxis](https://diataxis.fr/)-style: `reference/` is for looking
something up, `explanation/` is for understanding how and why the
system is built the way it is, `guides/` walks through a task end to
end, and `ops/` covers operating a running deployment.
Start from the table below, or browse a category directly.

## I want to… → Read

- **I want to…:** Look up an environment variable
  **Read:** [reference/configuration.md](reference/configuration.md)

- **I want to…:** Look up an HTTP endpoint
  **Read:** [reference/http-api.md](reference/http-api.md)

- **I want to…:** Look up an MCP tool
  **Read:** [reference/mcp-tools.md](reference/mcp-tools.md)

- **I want to…:** Look up a CLI command
  **Read:** [reference/cli.md](reference/cli.md)

- **I want to…:** Read DeepGenome response shapes
  **Read:** [contracts/deep-genome/README.md](contracts/deep-genome/README.md)

- **I want to…:** Understand the architecture
  **Read:** [explanation/architecture.md](explanation/architecture.md)

- **I want to…:** Understand the agent graphs
  **Read:** [explanation/agent-graphs.md](explanation/agent-graphs.md)

- **I want to…:** Deploy, or build a customer image
  **Read:** [guides/deployment.md](guides/deployment.md)

- **I want to…:** Set up for local development
  **Read:** [guides/development.md](guides/development.md)

- **I want to…:** Operate the HTTP service
  **Read:** [ops/http-api-runbook.md](ops/http-api-runbook.md)

- **I want to…:** Upgrade 0.1.2 → 0.1.3
  **Read:** [ops/upgrading.md](ops/upgrading.md)

- **I want to…:** Upgrade 0.1.1 → 0.1.2
  **Read:** [ops/upgrading.md](ops/upgrading.md)

- **I want to…:** See release history
  **Read:** [../CHANGELOG.md](../CHANGELOG.md)

- **I want to…:** Read the static-analysis exemption approval ledger
  **Read:** [development/lint-exemptions.md](development/lint-exemptions.md)

## Where docs live

- `reference/` — look something up: CLI flags, configuration
  variables, HTTP routes, and MCP tool schemas.
- `explanation/` — understand how and why: package layout, MCP
  dispatch, and agent graph composition.
- `guides/` — how-to: local development setup and deployment or
  customer-image distribution.
- `ops/` — operate a running deployment: the HTTP runbook and upgrade
  notes.
- `contracts/` — copyable, sanitized wire-shape fixtures for clients.

## Static-analysis exemption approval

The generated [static-analysis exemption ledger](development/lint-exemptions.md)
is the review record for exact, approved findings. The durable workflow is
documented in `AGENTS.md` and `STYLE.md`: inspect the finding, write the
counterfactual, obtain explicit approval, update the standalone registry, and
run the scoped gate before committing.
