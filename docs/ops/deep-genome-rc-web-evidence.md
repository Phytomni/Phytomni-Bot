# DeepGenome RC-WEB evidence map

This is an internal Bot acceptance map. It links each Web response case to
reproducible Bot-local evidence while keeping live remote, OBS, paired Web/Go,
browser, and staging acceptance external.

## `RC-WEB-001`

- Bot-local fixture/tests: `running.json`,
  `test_arun_returns_immediately_with_submit_envelope`, and
  `test_fake_backend_persists_acceptance_before_poll_and_snapshots`.
- Bot-local result: umbrella submit identity, accepted task, and owner-scoped
  polling snapshot.
- External Pending: live submit/poll and paired Web/Go response.

## `RC-WEB-002`

- Bot-local fixture/tests: `running.json`,
  `test_fake_backend_persists_acceptance_before_poll_and_snapshots`, and
  `test_partial_fake_backend_publishes_final_report_after_cas`.
- Bot-local result: monotonic report revision and compare-and-set final
  publication.
- External Pending: two live ordered revisions and consumer reducer evidence.

## `RC-WEB-003`

- Bot-local fixture/tests: `failed-with-intermediate.json`,
  `test_post_profile_failure_keeps_intermediate_report`, and
  `test_all_optional_failures_preserve_profile_and_fail_owner`.
- Bot-local result: failure preserves the last useful report and bounded
  failure state.
- External Pending: live failure/partial records and provider error
  correlation.

## `RC-WEB-004`

- Bot-local fixture/tests: `partial-final.json`,
  `test_deep_genome_snapshot_uses_canonical_split`, and
  `test_terminal_report_metadata_survives_run_projection`.
- Bot-local result: final report metadata and public artifact projection remain
  bounded.
- External Pending: real OBS object resolution and paired artifact rendering.

## `RC-WEB-005`

- Bot-local tests: `test_transition_sink_persists_every_local_status`,
  `test_poll_contract_emits_ordered_transitions_and_summary`, and
  `test_dispatch_coordinator_receives_effective_poll_id`.
- Bot-local result: timeout/status transitions use the configured request
  timeout and safe status vocabulary.
- External Pending: live provider timeout behavior and browser/staging
  observation.
