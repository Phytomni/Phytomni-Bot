# Web/Go Expert and history cutover acceptance packet

Owner: Phytomni-Web, Go gateway, and release Operations  
Bot contract: `POST /v1/query/route`, `GET /v1/runs`, and the Web cutover
checklist  
Current state: `External Pending`  
Evidence: `Not returned`

This packet covers `RC-WEB-007`. It is an additive, reversible deployment
procedure. The Bot repository supplies the route and payload contract; Web,
Go, and Operations own schema changes, flags, traffic observation, and
retirement.

## Preflight

- Record the Bot commit, Web/Go release, migration identifier, and rollback
  owner.
- Back up the Web history database and verify the backup before changing the
  schema.
- Confirm that the Expert flag is off and that the previous Python route and
  history reader remain available.
- Use synthetic dialogue IDs for smoke requests and redact all customer rows.

## Expert dark launch and cutover

1. Add the Web history `mode` column through the deployment tool using an
   additive, idempotent migration. Verify existing rows read as `instant` and
   record row counts before and after.
2. Deploy the gateway with `bot.expert_enabled=false`. Prove that no
   production request is routed to Expert while dark and that the flag-off
   path still serves the old route.
3. In a non-production environment, call `POST /v1/query/route` with a
   synthetic request. Assert the resolved canonical slug, the `formatted`
   envelope, and normal `202` polling for remote slugs.
4. Run Web history and Go gateway tests, including the mode-column read,
   route selection, and fallback cases.
5. Enable the flag only after the migration and smoke artifacts are attached.
   Keep an immediate flag-off rollback and record the first live request ID.

Return the migration record, dark-launch result, resolved-slug fixture, and
flag transition evidence under `RC-WEB-007`.

## History ETL and dual-read

If ETL is selected, map each source row idempotently to the Bot run projection
using `dialogue_id`, `query`, `answer`, `tool_name`, `status`, and timestamps.
Before writing, record source count, destination count, null count, duplicate
count, and a redacted per-dialogue sample. During the observation window:

- read the Bot projection first;
- fall back to the old history row when the Bot run is not available;
- compare counts and selected fields without attaching customer content;
- keep the source snapshot and rollback point immutable.

History acceptance requires a no-loss count comparison, a duplicate/null
disposition, dual-read test output, and an explicit rollback record.

## Old-route retirement

Retire the old Python request path only after the agreed observation window
shows zero traffic and Web/Go history reads pass. Remove route/import/env
references in the Web release, retain the prior release artifact, and verify
the flag-off rollback. If any cutover check fails, restore the prior Web
release and flag before deleting the old path.

Per-real-user rate limiting and multi-key client work remain deferred product
items; they are not hidden acceptance requirements for `RC-WEB-007`.
