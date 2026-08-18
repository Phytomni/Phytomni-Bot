# Bot Ready versus Accepted

Bot Ready is a repository readiness result. It does not authorize
feature flags, Web or Go cutover, production rollout, or migration
cleanup. Any missing paired consumer or staging evidence remains
`External Pending`.

`Accepted` requires the Bot packet plus the owner-returned Web, Go,
staging, or backend evidence named by the [Bot contract acceptance
runbook](bot-contract-acceptance-runbook.md). A green local gate or a
copyable fixture is not `Accepted`.
