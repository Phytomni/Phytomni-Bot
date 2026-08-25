# Execution Events V1

`v1/fixtures.json` is the Bot-owned public execution-event contract mirrored
by Web. It contains the complete required event vocabulary, safe targets,
Todo and projection examples, ignorable extensions, negative cases, and the
production defaults below.

| Limit | Default | Purpose |
| --- | ---: | --- |
| Event bytes | 16,384 | Bounds one persisted/public event envelope |
| Summary characters | 512 | Bounds user-visible fallback copy |
| Todo items | 100 | Bounds atomic Todo snapshots |
| History page | 50 default / 200 maximum | Bounds replay queries |
| Events per run | 10,000 | Bounds retained ledger volume |
| Live backlog | 1,000 | Bounds one resumable stream catch-up |
| Progress interval | 500 ms | Coalesces redundant informational progress |

Lifecycle, Todo, artifact, input-required, failure, and terminal facts are not
discarded as progress. Changes to any vocabulary or limit require updating the
Bot fixture, Web mirror, and both fixture-driven decoder tests together.
