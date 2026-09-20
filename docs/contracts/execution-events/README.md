# Execution Events V1

`v1/fixtures.json` is the Bot-owned public execution-event contract mirrored by
Web. It contains the complete required event vocabulary, safe targets, Todo and
projection examples, ignorable extensions, negative cases, and the production
defaults below.

- **Limit:** Event bytes
  - **Default:** 16,384
  - **Purpose:** Bounds one persisted/public event envelope
- **Limit:** Summary characters
  - **Default:** 512
  - **Purpose:** Bounds user-visible fallback copy
- **Limit:** Todo items
  - **Default:** 100
  - **Purpose:** Bounds atomic Todo snapshots
- **Limit:** History page
  - **Default:** 50 default / 200 maximum
  - **Purpose:** Bounds replay queries
- **Limit:** Events per run
  - **Default:** 10,000
  - **Purpose:** Bounds retained ledger volume
- **Limit:** Live backlog
  - **Default:** 1,000
  - **Purpose:** Bounds one resumable stream catch-up
- **Limit:** Progress interval
  - **Default:** 500 ms
  - **Purpose:** Coalesces redundant informational progress

Lifecycle, Todo, artifact, input-required, failure, and terminal facts are not
discarded as progress. Changes to any vocabulary or limit require updating the
Bot fixture, Web mirror, and both fixture-driven decoder tests together.
