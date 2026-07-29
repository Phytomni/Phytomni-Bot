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

| Agent            | Development en/zh | Test en/zh | Source allocation                                                             |
| ---------------- | ----------------- | ---------- | ----------------------------------------------------------------------------- |
| Chat             | 3/2               | 5/5        | Authored non-research prompts                                                 |
| Knowledge        | 3/2               | 5/5        | Dev: 1 Data 3 plus 4 Data 4; test: 2 Data 3 plus 8 Data 4                     |
| Data             | 3/2               | 5/5        | Five disjoint Data 5 classifications in dev; ten in test                      |
| Analyst          | 3/2               | 5/5        | All ten Data 6 Task/Query intents in test; five source-row paraphrases in dev |
| Review           | 3/2               | 5/5        | Five disjoint Data 17 topics in dev; ten in test                              |
| BriefGene        | 2/3               | 5/5        | Five disjoint Data 5 IDs in dev; ten in test                                  |
| DeepGenome       | 2/3               | 5/5        | One Data 7 gene per species in dev; two in test                               |
| InSilicoResearch | 2/3               | 5/5        | Five disjoint Data 4 references in dev; ten in test                           |
| DigitalDesign    | 2/3               | 5/5        | One Data 7 gene per species in dev; two in test                               |
| GeneNetwork      | 2/3               | 5/5        | Five disjoint trait mappings in dev; ten in test                              |

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

| Case             | Workbook/sheet/row | Source trait phrase      | Species      | Active TO ID | Committed TO name |
| ---------------- | ------------------ | ------------------------ | ------------ | ------------ | ----------------- |
| dev-network-001  | Data 5/Sheet1/744  | plant height             | Oryza sativa | TO:0000207   | plant height      |
| dev-network-002  | Data 5/Sheet1/748  | panicle number           | Oryza sativa | TO:0000152   | panicle number    |
| dev-network-003  | Data 5/Sheet1/750  | yield                    | Oryza sativa | TO:0000371   | yield trait       |
| dev-network-004  | Data 5/Sheet1/752  | grain length             | Oryza sativa | TO:0000734   | grain length      |
| dev-network-005  | Data 5/Sheet1/753  | spikelet length          | Oryza sativa | TO:0002768   | spikelet length   |
| test-network-001 | Data 5/Sheet1/747  | heading date             | Oryza sativa | TO:0000137   | days to heading   |
| test-network-002 | Data 5/Sheet1/749  | effective panicle number | Oryza sativa | TO:0000152   | panicle number    |
| test-network-003 | Data 5/Sheet1/751  | grain weight             | Oryza sativa | TO:0000590   | grain weight      |
| test-network-004 | Data 5/Sheet1/754  | plant height             | Oryza sativa | TO:0000207   | plant height      |
| test-network-005 | Data 5/Sheet1/756  | plant height             | Oryza sativa | TO:0000207   | plant height      |
| test-network-006 | Data 5/Sheet1/757  | heading date             | Oryza sativa | TO:0000137   | days to heading   |
| test-network-007 | Data 5/Sheet1/758  | panicle number           | Oryza sativa | TO:0000152   | panicle number    |
| test-network-008 | Data 5/Sheet1/759  | effective panicle number | Oryza sativa | TO:0000152   | panicle number    |
| test-network-009 | Data 5/Sheet1/760  | yield                    | Oryza sativa | TO:0000371   | yield trait       |
| test-network-010 | Data 5/Sheet1/761  | grain weight             | Oryza sativa | TO:0000590   | grain weight      |

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
  "from pathlib import Path; from scripts.agent_routing_eval.dataset import load_dataset, verify_workbook_sources; root=Path('../manuscript/1.submittion/2025-11-31329A-Z_Source_Data/Supplementary Data'); cases=load_dataset(Path('evaluation/agent_routing/datasets/dev_v1.jsonl'))+load_dataset(Path('evaluation/agent_routing/datasets/test_v1.jsonl')); verify_workbook_sources(cases, root); print(f'verified {len(cases)} cases')"
```
