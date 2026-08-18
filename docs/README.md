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

- **I want to…:** Look up compute resource tiers
  **Read:**
  [reference/configuration.md](reference/configuration.md#compute-resource-tiers)

- **I want to…:** Look up outbound pool budgets
  **Read:**
  [reference/configuration.md](reference/configuration.md#outbound-logical-pool-variables)
  and the
  [runbook outbound section](ops/http-api-runbook.md#outbound-logical-pool-operations)

- **I want to…:** Look up an HTTP endpoint
  **Read:** [reference/http-api.md](reference/http-api.md)

- **I want to…:** Look up an MCP tool
  **Read:** [reference/mcp-tools.md](reference/mcp-tools.md)

- **I want to…:** Look up a CLI command
  **Read:** [reference/cli.md](reference/cli.md)

- **I want to…:** Understand Expert / intent routing
  **Read:** [explanation/architecture.md](explanation/architecture.md) and
  [HTTP API — Expert Routing](reference/http-api.md#expert-routing)

- **I want to…:** Operate conversation context V1
  **Read:** [ops/conversation-context-v1.md](ops/conversation-context-v1.md)

- **I want to…:** Read DeepGenome response shapes
  **Read:** [contracts/deep-genome/README.md](contracts/deep-genome/README.md)

- **I want to…:** Read A2UI / Research input / resumable-upload goldens
  **Read:** [contracts/a2ui/](contracts/a2ui/README.md),
  [research-input-resolution](contracts/research-input-resolution/README.md),
  [resumable-upload](contracts/resumable-upload/README.md)

- **I want to…:** Understand the architecture
  **Read:** [explanation/architecture.md](explanation/architecture.md)

- **I want to…:** Understand the agent graphs
  **Read:** [explanation/agent-graphs.md](explanation/agent-graphs.md)

- **I want to…:** Deploy, or build a customer image
  **Read:** [guides/deployment.md](guides/deployment.md)

- **I want to…:** Set up for local development
  **Read:** [guides/development.md](guides/development.md)

- **I want to…:** Run the live e2e suite
  **Read:** [../e2e/README.md](../e2e/README.md)

- **I want to…:** Operate the HTTP service
  **Read:** [ops/http-api-runbook.md](ops/http-api-runbook.md)

- **I want to…:** Build, validate, mount, or replace the citation database
  **Read:** [ops/citation-database-runbook.md](ops/citation-database-runbook.md)

- **I want to…:** Read the Bot acceptance boundary
  **Read:**
  [ops/bot-ready-versus-accepted.md](ops/bot-ready-versus-accepted.md)
  and the
  [acceptance runbook](ops/bot-contract-acceptance-runbook.md)

- **I want to…:** Upgrade 0.1.3 → current
  **Read:** [ops/upgrading.md](ops/upgrading.md)

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
  dispatch, outbound runtime, Expert routing, and agent graph
  composition.
- `guides/` — how-to: local development setup and deployment or
  customer-image distribution.
- `ops/` — operate a running deployment: HTTP runbook, citation,
  conversation-context, and upgrade notes. Evidence ledgers in the
  same directory are SHA snapshots, not runbooks; start from the
  [acceptance runbook](ops/bot-contract-acceptance-runbook.md).
- `contracts/` — copyable, sanitized wire-shape fixtures for clients.

## Evidence ledgers (snapshots, not runbooks)

These files record Bot-local dispositions. They go stale when HEAD
moves. Do not treat them as the current operating procedure.

- [Bot contract acceptance](ops/bot-contract-acceptance-runbook.md)
- [Convergence ledger](ops/bot-contract-convergence-ledger.md)
- [Compatibility register](ops/bot-compatibility-register.md)
- [DataAgent incident ledger](ops/dataagent-incident-ledger.md)
- DeepGenome Web-response evidence map
  (`ops/deep-genome-rc-web-evidence.md`)

## Static-analysis exemption approval

The generated [static-analysis exemption ledger](development/lint-exemptions.md)
is the review record for exact, approved findings. The durable workflow is
documented in `STYLE.md`: inspect the finding, write the
counterfactual, obtain explicit approval, update the standalone registry, and
run the scoped gate before committing.
