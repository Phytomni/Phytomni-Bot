# DeepGenome report contract fixtures

These four JSON files are synthetic shape goldens for the public DeepGenome
snapshot returned by `GetTaskStatus` and `GET /v1/runs/{run_id}`. They contain
demo identifiers, text, and timestamps only; they are not live backend
acceptance evidence and do not prove Web/Go or production integration.
See [Bot Ready versus Accepted](../../ops/bot-ready-versus-accepted.md).

Clients submit one DeepGenome request and retain the owner-scoped umbrella run
ID. They read the persisted snapshot by that ID and never poll concrete remote
analysis child IDs. `report_revision` is monotonic; a client must ignore stale
or equal revisions instead of replacing a visible report with older content.

## Report states

- **Fixture:** [`running.json`](running.json)
  **Meaning:** BriefGene succeeded, an `intermediate_report` is visible, and
  optional analysis work is still running.

- **Fixture:** [`partial-final.json`](partial-final.json)
  **Meaning:** At least one usable analysis completed, synthesis produced
  `final_report`, and optional failures are exposed through
  `degraded` and
  `failures`.

- **Fixture:** [`failed-with-intermediate.json`](failed-with-intermediate.json)
  **Meaning:** BriefGene succeeded, later work or final synthesis failed, and
  the last `intermediate_report` remains available while
  `final_report` is null.

- **Fixture:** [`brief-gene-failed.json`](brief-gene-failed.json)
  **Meaning:** Required BriefGene failed before optional submission; both
  reports are null and the report stage is
  `waiting_for_brief_gene`.

`intermediate_report` becomes visible after the required BriefGene profile and
after each accepted optional-analysis transition. `final_report` appears only
after at least one usable analysis summary and successful final synthesis.
Every optional child is independently degradable: one failure does not erase a
usable intermediate report or force unrelated children to fail. `degraded`,
the progress counts, and fixed sanitized failure records explain partial
outcomes; they are warning metadata and never permission to label an
intermediate report complete.

The coordinator is deliberately in-process. A service restart settles an
orphaned non-terminal umbrella at the documented restart-failure boundary and
retains its latest intermediate snapshot; this fixture set does not promise a
durable worker or background processing outside the Bot process.
