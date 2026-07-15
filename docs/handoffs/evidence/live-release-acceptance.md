# Live release and remote-ref acceptance packet

Owners: Operations, Bot/Web/Go maintainers, and release owner  
Current state: `External Pending`  
Evidence: `Not returned`

This packet covers `RC-LIVE-001`, `RC-REL-001`, and `RC-REL-002`. It separates
three different claims: the offline Bot gate, authorized live backend E2E,
and publication of the reviewed release ref. A passing local gate cannot
substitute for either of the latter two.

## Authorized live E2E (`RC-LIVE-001`)

Run from an operator-controlled host only after the endpoint variables,
secrets, role grants, and owner approvals are present:

```bash
PHYTOMNI_RUN_INTEGRATION=1 PHYTOMNI_ALLOW_NETWORK=1 \
  uv run pytest e2e/ -v --tb=short
```

Record the exact Bot commit, environment class, operator, UTC start/end,
test count, pass/fail/skip counts, and artifact identifier. The redacted
artifact must include the agreed agent matrix and the Web/Go scenarios:

- DeepGenome submit, revision polling, partial final, and failure states;
- BriefGene, Analyst, Design, and Network report/artifact consumption;
- A2UI/AG-UI passthrough and timeout mapping;
- Expert/history cutover smoke where the release owner includes it;
- GaussDB and relay checks only when the authorized environment permits them.

Do not attach `e2e/output/` files containing customer data, keys, DSNs, SQL,
provider responses, or private URLs. A failed or unavailable test stays
pending with its classification; rerunning with broader permissions or changed
secrets is not an acceptance strategy.

## Remote push (`RC-REL-001`)

After the reviewed local commits and scoped/full gates are green, run the
repository path without merge or force-push:

```bash
make push
git ls-remote origin refs/heads/release/0.1.3
```

The returned record must include the reviewed local HEAD, the remote hash, the
command exit status, UTC timestamp, and the exact network error when the
remote is unreachable. If the environment requires the approved explicit SSH
identity, rerun the same `make push` workflow with the operator's configured
SSH command and record that fact without exposing paths or key material.

`RC-REL-001` remains `External Pending` when DNS, SSH, proxy, or remote policy
rejects the push. A local `ahead N` status is not a remote acceptance record.

## Release-owner review (`RC-REL-002`)

The release owner reviews the tracked disposition matrix and verifies that:

1. every required `RC-*` row links to a reviewed evidence record;
2. no external row is closed from a plan checkbox or offline mock;
3. approved blockers identify an owner, mitigation, and next review date;
4. the remote ref points to the reviewed commit;
5. rollback references exist for every migration, key rotation, and retirement
   action.

Return the signed or attributable review record. Until then, the overall
release remains `External Pending` even when the Bot tree is locally green.
