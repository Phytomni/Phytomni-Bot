# AI Operational Authority

AI may inspect, design, edit scoped source, run local offline checks, and
prepare evidence. Explicit human/environment authority is required for secrets,
paid or live-provider calls, production data, destructive migrations, merges,
deployments, feature-flag activation, and releases.

Repository evidence on 2026-08-18 shows Bot workflows `Lint`, `Secret Scan`,
and scheduled `E2E nightly`. No `CODEOWNERS` file is present. The checkout does
not prove branch-protection required checks, reviewer policy, environment
approvals, or current GitHub configuration; obtain those from GitHub/operations
before merge or release.

Local `make full` is evidence only for the local gate. Nightly/live E2E needs
authorized configuration and cannot be replaced by offline fixtures.
