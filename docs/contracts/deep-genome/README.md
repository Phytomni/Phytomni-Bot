# DeepGenome report contract fixtures

These four JSON files are synthetic shape goldens for the public DeepGenome
snapshot returned by `GetTaskStatus` and `GET /v1/runs/{run_id}`. They contain
demo identifiers, text, and timestamps only; they are not live backend
acceptance evidence and do not prove Web/Go or production integration.

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

## RC-WEB local evidence map

The five Web response cases below have reproducible Bot-local evidence. The
rightmost column remains external because synthetic fixtures and offline tests
cannot prove a live remote completion, OBS listing, or paired Web/Go result.

### `RC-WEB-001`

- Bot-local fixture/tests: `running.json`,
  `test_arun_returns_immediately_with_submit_envelope`, and
  `test_fake_backend_persists_acceptance_before_poll_and_snapshots`.
- Bot-local result: umbrella submit identity, accepted task, and owner-scoped
  polling snapshot.
- External Pending: live submit/poll and paired Web/Go response.

### `RC-WEB-002`

- Bot-local fixture/tests: `running.json`,
  `test_fake_backend_persists_acceptance_before_poll_and_snapshots`, and
  `test_partial_fake_backend_publishes_final_report_after_cas`.
- Bot-local result: monotonic report revision and compare-and-set final
  publication.
- External Pending: two live ordered revisions and consumer reducer evidence.

### `RC-WEB-003`

- Bot-local fixture/tests: `failed-with-intermediate.json`,
  `test_post_profile_failure_keeps_intermediate_report`, and
  `test_all_optional_failures_preserve_profile_and_fail_owner`.
- Bot-local result: failure preserves the last useful report and bounded
  failure state.
- External Pending: live failure/partial records and provider error
  correlation.

### `RC-WEB-004`

- Bot-local fixture/tests: `partial-final.json`,
  `test_deep_genome_snapshot_uses_canonical_split`, and
  `test_terminal_report_metadata_survives_run_projection`.
- Bot-local result: final report metadata and public artifact projection remain
  bounded.
- External Pending: real OBS object resolution and paired artifact rendering.

### `RC-WEB-005`

- Bot-local tests: `test_transition_sink_persists_every_local_status`,
  `test_poll_contract_emits_ordered_transitions_and_summary`, and
  `test_dispatch_coordinator_receives_effective_poll_id`.
- Bot-local result: timeout/status transitions use the configured request
  timeout and safe status vocabulary.
- External Pending: live provider timeout behavior and browser/staging
  observation.
