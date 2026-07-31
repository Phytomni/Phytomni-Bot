# Agent Routing Corpus v1

This directory contains the fixed `dev_v1` and `test_v1` routing corpora.
They are the 50-case development split and 100-case test split used to
evaluate canonical agent selection and deterministic core arguments. Treat
`test_v1` as immutable after a recorded baseline; make a new versioned file
for later corrections or expanded coverage.

## Source Provenance

The read-only curation source is:

```text
../manuscript/1.submittion/2025-11-31329A-Z_Source_Data/Supplementary Data/
```

Each workbook record stores the workbook basename, worksheet name, physical
one-based row, literal identifier in `source.source_id`, and closed
`source_id_column` and `source_text_column` fields. `transformation.source_text`
is the retained source cell used to construct the prompt. Workbook
verification reads each declared cell on that physical row exactly; it does
not search another cell in the row. The normal routing runtime does not access
this sibling checkout; it is required only for the explicit provenance check
below.

The source workbooks have different column shapes. The retained cells are:

| Corpus surface               | Source ID cell | Source text cell |
| ---------------------------- | -------------- | ---------------- |
| Knowledge, Data 3            | A              | C                |
| Knowledge, Data 4            | A              | B                |
| InSilicoResearch, Data 4     | A              | D                |
| Data, BriefGene, GeneNetwork | A              | C                |
| Analyst, Data 6              | A              | B                |
| DeepGenome and DigitalDesign | B              | D                |
| Review, Data 17              | A              | B                |

For Data 7, B is the exact `source_id` and D is the exact retained
`transformation.source_text`; D can omit the B identifier. DeepGenome and
DigitalDesign therefore require `expected_core_args.gene_id` to equal the
verified B-cell `source_id`, rather than treating B as D or weakening identity
validation. No source response, model output, credential, or endpoint field
is included in either JSONL file.

## Split Allocation

The case IDs are lexicographically sorted. Development IDs run from
`dev-<agent>-001` through `005`; test IDs run through `010` for every
canonical agent.

- **Agent:** Chat
  **Development en/zh:** 3/2
  **Test en/zh:** 5/5
  **Source allocation:** Authored non-research prompts

- **Agent:** Knowledge
  **Development en/zh:** 3/2
  **Test en/zh:** 5/5
  **Source allocation:** Dev: 1 Data 3 plus 4 Data 4; test: 2 Data 3 plus 8 Data
  4

- **Agent:** Data
  **Development en/zh:** 3/2
  **Test en/zh:** 5/5
  **Source allocation:** Five disjoint Data 5 classifications in dev; ten in
  test

- **Agent:** Analyst
  **Development en/zh:** 3/2
  **Test en/zh:** 5/5
  **Source allocation:** All ten Data 6 Task/Query intents in test; five
  source-row paraphrases in dev

- **Agent:** Review
  **Development en/zh:** 3/2
  **Test en/zh:** 5/5
  **Source allocation:** Five disjoint Data 17 topics in dev; ten in test

- **Agent:** BriefGene
  **Development en/zh:** 2/3
  **Test en/zh:** 5/5
  **Source allocation:** Five disjoint Data 5 IDs in dev; ten in test

- **Agent:** DeepGenome
  **Development en/zh:** 2/3
  **Test en/zh:** 5/5
  **Source allocation:** One Data 7 gene per species in dev; two in test

- **Agent:** InSilicoResearch
  **Development en/zh:** 2/3
  **Test en/zh:** 5/5
  **Source allocation:** Five disjoint Data 4 references in dev; ten in test

- **Agent:** DigitalDesign
  **Development en/zh:** 2/3
  **Test en/zh:** 5/5
  **Source allocation:** One Data 7 gene per species in dev; two in test

- **Agent:** GeneNetwork
  **Development en/zh:** 2/3
  **Test en/zh:** 5/5
  **Source allocation:** Five disjoint trait mappings in dev; ten in test

The five Data 7 species are Arabidopsis, soybean, rice, wheat, and maize.
DeepGenome and DigitalDesign deliberately use the same per-species source
genes for different routing intents. Analyst is the sole within-agent
development/test source-row overlap: its development prompts are explicit
paraphrases of five test intents. All other same-agent source rows are split
disjoint.

## Network Crosswalk

Each mapping below was reviewed directly against the active, bundled Trait
Ontology catalog. The source phrase is either the committed TO name or an
explicit catalog synonym: `heading date` is a synonym of days to heading, and
`effective panicle number` is covered by the panicle-number synonym for
effective tillers. Every final Network question includes `Oryza sativa` and
the exact active TO ID. No routing model or Network resolver was used to
create these labels.

- **Case:** dev-network-001
  **Workbook/sheet/row:** Data 5/Sheet1/744
  **Source trait phrase:** plant height
  **Species:** Oryza sativa
  **Active TO ID:** TO:0000207
  **Committed TO name:** plant height

- **Case:** dev-network-002
  **Workbook/sheet/row:** Data 5/Sheet1/748
  **Source trait phrase:** panicle number
  **Species:** Oryza sativa
  **Active TO ID:** TO:0000152
  **Committed TO name:** panicle number

- **Case:** dev-network-003
  **Workbook/sheet/row:** Data 5/Sheet1/750
  **Source trait phrase:** yield
  **Species:** Oryza sativa
  **Active TO ID:** TO:0000371
  **Committed TO name:** yield trait

- **Case:** dev-network-004
  **Workbook/sheet/row:** Data 5/Sheet1/752
  **Source trait phrase:** grain length
  **Species:** Oryza sativa
  **Active TO ID:** TO:0000734
  **Committed TO name:** grain length

- **Case:** dev-network-005
  **Workbook/sheet/row:** Data 5/Sheet1/753
  **Source trait phrase:** spikelet length
  **Species:** Oryza sativa
  **Active TO ID:** TO:0002768
  **Committed TO name:** spikelet length

- **Case:** test-network-001
  **Workbook/sheet/row:** Data 5/Sheet1/747
  **Source trait phrase:** heading date
  **Species:** Oryza sativa
  **Active TO ID:** TO:0000137
  **Committed TO name:** days to heading

- **Case:** test-network-002
  **Workbook/sheet/row:** Data 5/Sheet1/749
  **Source trait phrase:** effective panicle number
  **Species:** Oryza sativa
  **Active TO ID:** TO:0000152
  **Committed TO name:** panicle number

- **Case:** test-network-003
  **Workbook/sheet/row:** Data 5/Sheet1/751
  **Source trait phrase:** grain weight
  **Species:** Oryza sativa
  **Active TO ID:** TO:0000590
  **Committed TO name:** grain weight

- **Case:** test-network-004
  **Workbook/sheet/row:** Data 5/Sheet1/754
  **Source trait phrase:** plant height
  **Species:** Oryza sativa
  **Active TO ID:** TO:0000207
  **Committed TO name:** plant height

- **Case:** test-network-005
  **Workbook/sheet/row:** Data 5/Sheet1/756
  **Source trait phrase:** plant height
  **Species:** Oryza sativa
  **Active TO ID:** TO:0000207
  **Committed TO name:** plant height

- **Case:** test-network-006
  **Workbook/sheet/row:** Data 5/Sheet1/757
  **Source trait phrase:** heading date
  **Species:** Oryza sativa
  **Active TO ID:** TO:0000137
  **Committed TO name:** days to heading

- **Case:** test-network-007
  **Workbook/sheet/row:** Data 5/Sheet1/758
  **Source trait phrase:** panicle number
  **Species:** Oryza sativa
  **Active TO ID:** TO:0000152
  **Committed TO name:** panicle number

- **Case:** test-network-008
  **Workbook/sheet/row:** Data 5/Sheet1/759
  **Source trait phrase:** effective panicle number
  **Species:** Oryza sativa
  **Active TO ID:** TO:0000152
  **Committed TO name:** panicle number

- **Case:** test-network-009
  **Workbook/sheet/row:** Data 5/Sheet1/760
  **Source trait phrase:** yield
  **Species:** Oryza sativa
  **Active TO ID:** TO:0000371
  **Committed TO name:** yield trait

- **Case:** test-network-010
  **Workbook/sheet/row:** Data 5/Sheet1/761
  **Source trait phrase:** grain weight
  **Species:** Oryza sativa
  **Active TO ID:** TO:0000590
  **Committed TO name:** grain weight

Only active ontology IDs are permitted. A deprecated upstream identifier is
not a valid gold label even where it appears in external material.

## Run

Validate the repository corpus:

```bash
UV_CACHE_DIR=/tmp/phytomni-routing-uv \
  uv run --no-sync pytest \
  tests/unit/evaluation/test_agent_routing_dataset.py -q
```

Verify every workbook coordinate against the source inputs:

```bash
UV_CACHE_DIR=/tmp/phytomni-routing-uv \
  uv run --no-sync python -c \
  "from pathlib import Path; from scripts.agent_routing_eval.dataset import (\
load_dataset, verify_workbook_sources); root=Path('../manuscript/1.submittion/\
2025-11-31329A-Z_Source_Data/Supplementary Data'); cases=load_dataset(\
Path('evaluation/agent_routing/datasets/dev_v1.jsonl')) + load_dataset(\
Path('evaluation/agent_routing/datasets/test_v1.jsonl')); \
verify_workbook_sources(cases, root); print(f'verified {len(cases)} cases')"
```

## Selector Evaluation

Run the selector-only evaluation from the repository root:

```bash
uv run python scripts/evaluate_agent_routing.py --mode quick

uv run python scripts/evaluate_agent_routing.py --mode benchmark

uv run python scripts/evaluate_agent_routing.py \
  --mode benchmark \
  --dataset evaluation/agent_routing/datasets/dev_v1.jsonl
```

Quick mode evaluates the development split once. The default benchmark
evaluates the test split three times, producing 300 logical calls; each
logical call may make up to three provider attempts after a typed provider
failure. The selected agents are never executed: only the selector and its
schema/core-argument result are measured. Benchmark runs require a clean
working tree unless `--allow-dirty` is supplied; that override records a
diagnostic report rather than a stable baseline. `--enforce-thresholds` is
available only for benchmark mode and changes threshold failure to exit code

1. Exit codes are 0 for a completed run, 1 for an enforced threshold failure,
   2 for invalid configuration/data or a blocked dirty benchmark, and 3 for an
   incomplete or cancelled run. JSON and Markdown artifacts are written under
   `evaluation/agent_routing/results/`, which is intentionally ignored.
