# ASTRA schema and viewer adapter contract

Snapshot: 2026-07-29

Question: Which released ASTRA packages and schema/API contracts should the
Neurodesktop viewer target?

## Decision

Pin both packages independently:

```text
astra-spec==0.0.12
astra-tools==0.2.11
```

These remain the latest releases, and both release tags are identical to their
respective `main` branches at this snapshot. No released ASTRA contract has
changed since the 2026-07-27 assessment. The explicit dual pin matters because
`astra-tools 0.2.11` declares only `astra-spec>=0.0.11`, which would otherwise
allow a future breaking schema release to enter the image
([astra-spec release](https://github.com/LightconeResearch/astra-spec/releases/tag/v0.0.12),
[astra-spec tag-to-main comparison](https://github.com/LightconeResearch/astra-spec/compare/v0.0.12...main),
[astra-tools release](https://github.com/LightconeResearch/astra-tools/releases/tag/v0.2.11),
[astra-tools tag-to-main comparison](https://github.com/LightconeResearch/astra-tools/compare/v0.2.11...main),
[astra-tools dependency metadata](https://github.com/LightconeResearch/astra-tools/blob/v0.2.11/pyproject.toml#L42-L52)).

New Neurodesktop examples and fixtures must use:

```yaml
version: "0.0.12"
```

The marker is the installed `astra-spec` *package* version. `astra init`
derives it from package metadata, and `check_spec_version()` compares a YAML
marker against that metadata
([scaffold implementation](https://github.com/LightconeResearch/astra-tools/blob/v0.2.11/src/astra/cli.py#L131-L140),
[version check](https://github.com/LightconeResearch/astra-tools/blob/v0.2.11/src/astra/validation/schema.py#L112-L155)).
Do not read the marker from `astra.datamodel.analysis.version` or
`astra.datamodel.astra_pydantic.version`: both generated modules in the
0.0.12 wheel still say `0.0.11`
([Pydantic model](https://github.com/LightconeResearch/astra-spec/blob/v0.0.12/src/astra/datamodel/astra_pydantic.py#L32-L45),
[LinkML dataclass model](https://github.com/LightconeResearch/astra-spec/blob/v0.0.12/src/astra/datamodel/analysis.py#L64-L71)).
Upstream examples also disagree: tagged examples use `0.0.11`, while prose
examples use `1.0`
([tagged Iris example](https://github.com/LightconeResearch/astra-spec/blob/v0.0.12/examples/iris/astra.yaml#L1-L3),
[getting-started example](https://github.com/LightconeResearch/astra-spec/blob/v0.0.12/docs/getting-started.md#L31-L38)).
The CLI treats a mismatch as a warning, not an error. The viewer should show a
validation error and no graph when its declared marker is not exactly the pin;
otherwise it cannot promise which adapter contract it applied.

## Authoritative YAML contract

The following is the contract exposed by the tagged LinkML schema and the
schema-plus-semantic validation performed by `astra-tools 0.2.11`. Unless a
field below has another range, LinkML's default range is string. Entity IDs are
lowercase snake case and exclude reserved section names; universe IDs also
allow hyphens
([identifier contract](https://github.com/LightconeResearch/astra-spec/blob/v0.0.12/src/astra/schema/analysis.yaml#L30-L39),
[universe IDs](https://github.com/LightconeResearch/astra-spec/blob/v0.0.12/src/astra/schema/universe.yaml#L62-L82)).

### Analysis and nesting

`Analysis` is recursive. In authored YAML, `inputs` and `outputs` are lists;
`decisions`, `prior_insights`, `findings`, and `analyses` are maps keyed by
their IDs
([Analysis schema](https://github.com/LightconeResearch/astra-spec/blob/v0.0.12/src/astra/schema/analysis.yaml#L511-L579)).

| Field | YAML type | Required/effective meaning |
| --- | --- | --- |
| `id` | string | Identifier. May be omitted at the document root because `astra-tools` injects `root`; a nested analysis gets its identity from its map key. |
| `version` | string matching `X.Y` or `X.Y.Z` | Semantic validation requires it at the root; pin to `0.0.12`. |
| `name` | string | Semantic validation requires it at the root. |
| `description` | string | Optional prose. |
| `tags` | list of strings | Optional. |
| `inputs` | list of `Input` | Semantic validation requires the root key (an empty list is accepted). |
| `outputs` | list of `Output` | Semantic validation requires the root key (an empty list is accepted). |
| `decisions` | map of `Decision` | Optional. |
| `prior_insights` | map of `Insight` | Optional. |
| `findings` | map of `Insight` | Optional. |
| `container` | string | Optional node-level recipe default. |
| `path` | string | For a nested analysis, directory containing another `astra.yaml`; mutually exclusive with inline content. |
| `analyses` | map of `Analysis` | Optional recursive children. |

Schema validation injects root and map-key IDs into a copy before calling the
generated Pydantic models
([ID injection and model validation](https://github.com/LightconeResearch/astra-tools/blob/v0.2.11/src/astra/validation/schema.py#L23-L75)).
Semantic validation separately requires root `version`, `name`, `inputs`, and
`outputs`, resolves external child analyses when given a base directory, and
checks the graph's cross-references
([semantic root contract](https://github.com/LightconeResearch/astra-tools/blob/v0.2.11/src/astra/validation/semantic.py#L120-L240)).

### Inputs

`Input.type` is exactly `data` or `analysis`. A normal input requires `id` and
`type`; an alias requires `id` plus `from` and forbids all content fields
([input types and fields](https://github.com/LightconeResearch/astra-spec/blob/v0.0.12/src/astra/schema/analysis.yaml#L78-L84),
[Input schema and alias rules](https://github.com/LightconeResearch/astra-spec/blob/v0.0.12/src/astra/schema/analysis.yaml#L207-L294)).

| Field | YAML type | Meaning |
| --- | --- | --- |
| `id` | string | Required list-item identifier. |
| `from` | string path | Alias to an ancestor input or an ancestor/sibling output. |
| `label` | string | Optional display label. |
| `type` | `data \| analysis` | Required unless `from` is set; forbidden with `from`. |
| `description` | string | Optional. |
| `source` | string | URI, path, loader, or other data locator. |
| `ref` | string | External ASTRA-analysis reference. |
| `ref_version` | string | Version of the referenced analysis. |
| `use_outputs` | list of strings | Selected outputs from the referenced analysis. |

### Outputs

`Output.type` is exactly `metric`, `figure`, `table`, `data`, or `report`.
There is no `dataset` enum value: the viewer must explicitly map ASTRA `data`
to its internal `dataset` sub-kind
([output enum](https://github.com/LightconeResearch/astra-spec/blob/v0.0.12/src/astra/schema/analysis.yaml#L86-L98)).

| Field | YAML type | Meaning |
| --- | --- | --- |
| `id` | string | Required list-item identifier. |
| `from` | string path | Re-export of a descendant output. With `from`, only `id`, `from`, and `when` are legal. |
| `when` | list of condition strings | Optional activation conditions. |
| `label` | string | Optional display label. |
| `type` | output enum | Required unless `from` is set; forbidden with `from`. |
| `description` | string | Optional. |
| `inputs` | list of strings | IDs of analysis inputs, sibling outputs, or qualified child outputs. |
| `decisions` | list of strings | IDs of decisions that parameterize this output. |
| `recipe` | `Recipe` object | Optional execution description. |

The output fields, alias rules, dependency semantics, and recipe relationship
are defined in the schema and semantic validator
([Output schema](https://github.com/LightconeResearch/astra-spec/blob/v0.0.12/src/astra/schema/analysis.yaml#L296-L403),
[output dependency validation](https://github.com/LightconeResearch/astra-tools/blob/v0.2.11/src/astra/validation/semantic.py#L669-L746)).

### Decisions, options, and conditions

Decisions are maps keyed by decision ID; options are maps keyed by option ID.
A local decision requires `label` and `options`. An aliased decision uses
`from: ../id` (or further ancestors), may retain `when`, and forbids local
content
([Decision schema](https://github.com/LightconeResearch/astra-spec/blob/v0.0.12/src/astra/schema/analysis.yaml#L434-L509)).

| Object | Fields |
| --- | --- |
| `Decision` | `id: string`; `from: string?`; `when: list[string]?`; `label: string?`; `rationale: string?`; `tags: list[string]?`; `default: string?`; `options: map[Option]?`. |
| `Option` | `id: string`; required `label: string`; optional `description: string`, `insights: list[string]`, `incompatible_with: list[string]`, `requires: list[string]`, `excluded: bool`, `excluded_reason: string`. |

`when` exists only on `Output` and `Decision`. Each item is
`decision_id.option_id` or its negated form `~decision_id.option_id`; multiple
items are ANDed
([condition slot](https://github.com/LightconeResearch/astra-spec/blob/v0.0.12/src/astra/schema/analysis.yaml#L65-L70),
[condition evaluator](https://github.com/LightconeResearch/astra-tools/blob/v0.2.11/src/astra/helpers.py#L18-L46)).
`requires` and `incompatible_with` use non-negated `decision_id.option_id`
references. Semantic validation checks references, prevents a decision from
conditioning itself, checks defaults, and enforces excluded-option consistency
([decision semantics](https://github.com/LightconeResearch/astra-tools/blob/v0.2.11/src/astra/validation/semantic.py#L462-L607)).

### Recipes and resources

Resources are nested at `output.recipe.resources`, not directly on `Output`.
The analysis-level `container` supplies a default; `recipe.container` is a
per-output override
([Recipe and Resources schema](https://github.com/LightconeResearch/astra-spec/blob/v0.0.12/src/astra/schema/analysis.yaml#L118-L203)).

| Object | Fields |
| --- | --- |
| `Recipe` | `command: string?`, `resources: Resources?`, `container: string?`. |
| `Resources` | `cpus: float >= 0?`, `memory: string?`, `time_limit: string?`, `disk: string?`, `gpus: integer >= 1?`. |

Despite prose describing `command` as the required part of a recipe, the
released LinkML/Pydantic contract does not mark it required, and the semantic
validator only inspects the template when a command is present. The adapter
must therefore tolerate a missing recipe or command and must not invent one.

### Findings, prior insights, and evidence

Both `findings` and `prior_insights` contain the same `Insight` type. Their map
key is the authored identity; each insight carries a required claim, timestamp,
and evidence list
([Insight schema](https://github.com/LightconeResearch/astra-spec/blob/v0.0.12/src/astra/schema/insight.yaml#L92-L129)).

| Object | Fields |
| --- | --- |
| `Insight` | `id: string`; optional `label`; required `claim: string`, `created_at: datetime`, `evidence: list[Evidence]`; optional `derived: bool`, `scope: string`, `tags: list[string]`, `notes: string`. |
| `Evidence` | required `id: string`; optional `doi: string`, `artifact: string`, `version: integer >= 1`, `snapshot: string`, `source_commit: string`, `quote: TextQuoteSelector`, `location: FragmentSelector`. |
| `TextQuoteSelector` | required `exact: string`; optional `prefix`, `suffix`. |
| `FragmentSelector` | optional `value: string`, `page: integer >= 1`. |

The schema prose says exactly one of `Evidence.doi` and `Evidence.artifact`
must be present
([Evidence schema](https://github.com/LightconeResearch/astra-spec/blob/v0.0.12/src/astra/schema/insight.yaml#L60-L90)),
but 0.0.12 encodes no rule for it, and 0.2.11's semantic pass only checks that
an `artifact` names a local output
([artifact validation](https://github.com/LightconeResearch/astra-tools/blob/v0.2.11/src/astra/validation/semantic.py#L431-L460)).
Direct tests confirmed that neither source and both sources pass the released
CLI validators. The viewer should not silently assert the prose invariant;
preserve these records and emit a stable warning until upstream enforces the
rule.

### Universes

A universe mirrors the analysis tree
([Universe schema](https://github.com/LightconeResearch/astra-spec/blob/v0.0.12/src/astra/schema/universe.yaml#L28-L82)).

| Object | Fields and YAML shape |
| --- | --- |
| `Universe` | required `id: string`; optional `description`; `decisions: map[decision_id, option_id]`; `analyses: map[analysis_id, UniverseNode]`. |
| `UniverseNode` | identity supplied by its map key; optional `universe: string`; `decisions: map[decision_id, option_id]`; recursive `analyses`. |
| `DecisionSelection` | generated-model representation with `decision_id` and required `option_id`; authored YAML uses the compact map above. |

Semantic validation requires exactly one valid, non-excluded selection for
every active local decision, rejects selections for inactive or inherited
decisions, and enforces `requires`/`incompatible_with`
([universe semantics](https://github.com/LightconeResearch/astra-tools/blob/v0.2.11/src/astra/validation/semantic.py#L1108-L1366)).
The `UniverseNode.universe` indirection is schema-valid, but the semantic code
explicitly says loading the referenced child universe is the caller's job; the
released file validator does not perform that resolution
([file validation](https://github.com/LightconeResearch/astra-tools/blob/v0.2.11/src/astra/validation/semantic.py#L1369-L1383)).

## Viewer adapter boundary

The supported validation API is `astra.validation`, whose public exports
include schema-data/file and semantic-data/file validators
([public API](https://github.com/LightconeResearch/astra-tools/blob/v0.2.11/src/astra/validation/__init__.py#L1-L35)).
The released CLI's actual path is:

1. `yaml.safe_load` into raw dictionaries;
2. inject map-key IDs into a copy and validate generated Pydantic models;
3. run dict-based semantic validation;
4. keep using the raw dictionaries.

This is visible in the official helper, schema validator, and CLI
([raw-dict loader](https://github.com/LightconeResearch/astra-tools/blob/v0.2.11/src/astra/helpers.py#L123-L145),
[Pydantic validation](https://github.com/LightconeResearch/astra-tools/blob/v0.2.11/src/astra/validation/schema.py#L63-L109),
[CLI sequence](https://github.com/LightconeResearch/astra-tools/blob/v0.2.11/src/astra/cli.py#L268-L306)).
There is no public load-to-LinkML-object SDK that also performs the semantic
pass. Accordingly, `adapter.py` should consume validated raw dictionaries; the
generated LinkML dataclasses/Pydantic classes are validator implementation
details, not the viewer's canonical data model.

The adapter contract should be:

1. Resolve `spec` and `universe` against `spec.parent`, and reject any supplied
   path outside that project root before opening it.
2. Load the spec with `astra.helpers.load_yaml`.
3. Require `version == importlib.metadata.version("astra-spec") == "0.0.12"`.
4. Run `validate_analysis_data(raw_spec)`, then preflight every recursive
   `analyses.*.path` for root confinement, then run
   `validate_analysis(raw_spec, base_path=spec.parent)`. Schema-validate every
   externally loaded child `astra.yaml` recursively as well: the top-level
   Pydantic pass sees only the `path` stub, while semantic resolution loads the
   child content later.
5. Resolve the analysis tree only after confinement. Upstream's
   `resolve_analysis_tree()` calls `.resolve()` and reads the target without a
   project-root boundary, so it must never receive an un-preflighted path
   ([resolver](https://github.com/LightconeResearch/astra-tools/blob/v0.2.11/src/astra/helpers.py#L63-L120)).
6. Load and schema-validate a supplied universe, then call
   `validate_universe(universe_data, resolved_analysis_data)`. This public
   data-level call is necessary for externally stored sub-analyses because the
   convenience `validate_universe_file()` does not resolve them.
7. If no universe is supplied, derive selections from each active local
   decision's `default`; error if a required active decision has no default.
8. Only after all checks pass, adapt to schema-independent internal records.
   Use fully qualified analysis-tree paths for internal IDs so repeated local
   IDs in different scopes cannot collide. Mapping keys, not redundant nested
   `id` properties, are canonical for decisions, options, insights, and child
   analyses.
9. Map `Output.type: data` to viewer `sub_kind: dataset`; extract resources
   from `Output.recipe.resources`; preserve missing recipe/command as `null`;
   and evaluate `when` against the effective per-scope universe selection.

Do not pass `UniverseNode.universe` indirection through as if it were resolved.
Either implement confined resolution explicitly or return a clear unsupported
contract error for the pinned release.

## Viewer-model consequences surfaced by the schema

These are sharp decisions for later Wayfinder tickets rather than reasons to
change the pin:

1. **`published` and units do not exist in ASTRA 0.0.12.** `Output` has no
   `published`, artifact-path, or metric-unit field. Spec-only gap G5 ("terminal
   output is not marked published") and unit-aware metric previews cannot be
   computed from ASTRA YAML. Decide whether to remove/reword G5, infer terminal
   publication by graph topology, or source these properties exclusively from
   a run manifest.
2. **Evidence is a three-layer chain.** The schema is
   `Option -> prior Insight -> Evidence` and
   `finding Insight -> Evidence.artifact -> Output`. The current viewer node
   vocabulary has `finding` and `evidence`, but no distinct prior-insight/source
   layer. Decide whether to flatten embedded Evidence into its owning Insight
   inspector or extend the internal graph so papers/artifacts and insights are
   distinct. An output-to-finding `claims` edge is only authoritative when a
   finding contains `Evidence.artifact` naming that output.
3. **External path indirection needs a confinement policy.** ASTRA permits
   `Analysis.path`; upstream resolution is not project-confined. The widget's
   stated file boundary therefore requires adapter-owned preflight/resolution
   or an explicit M1 restriction to inline analyses.
4. **Child-universe indirection is incomplete upstream.** The schema permits
   `UniverseNode.universe`, but released validation does not load it. Decide
   whether the viewer supports it itself or rejects it until upstream defines
   the resolver contract.
5. **Upstream validation has two known soft spots.** The YAML/package version
   mismatch is warning-only, and the exactly-one evidence-source rule is prose
   only. The viewer should hard-fail the former because it controls adapter
   compatibility, and report the latter as a warning because it does not.

## Neurodesktop compatibility evidence

The current checkout pins `quay.io/jupyter/base-notebook:2026-07-28` and
installs `ipywidgets==8.1.8` plus `jupyterlab_widgets`
([base image](https://github.com/neurodesk/neurodesktop/blob/0fc6c896599a9fe60c66c01e3568d84bea9aef80/Dockerfile#L3-L6),
[Jupyter packages](https://github.com/neurodesk/neurodesktop/blob/0fc6c896599a9fe60c66c01e3568d84bea9aef80/Dockerfile#L544-L581)).
The locally built image reported:

```text
Python             3.13.14
JupyterLab          4.6.2
ipywidgets          8.1.8
jupyterlab_widgets  3.0.16
pydantic            2.13.4
PyYAML              6.0.3
```

This is within `astra-spec`'s Python `>=3.9,<4.0` and `astra-tools`' Python
`>=3.11` requirements; `astra-tools` explicitly classifies Python 3.13 and
3.14 as supported
([astra-spec package metadata](https://github.com/LightconeResearch/astra-spec/blob/v0.0.12/pyproject.toml#L5-L20),
[astra-tools package metadata](https://github.com/LightconeResearch/astra-tools/blob/v0.2.11/pyproject.toml#L1-L36)).

In a disposable system-site-packages virtual environment inside that image,
installing the exact dual pins produced no broken requirements. Imports of
`Analysis`, `Universe`, and all public validation functions succeeded;
`astra init --no-git` generated `version: "0.0.12"`; and `astra validate`
passed for both the generated analysis and baseline universe. As an additional
tag-level regression check, the source test suites passed under the exact pins:

```text
astra-spec v0.0.12:  10 passed
astra-tools v0.2.11: 196 passed, 21 skipped (optional/network cases)
```

The pins are therefore compatible with the current Neurodesktop Python and
Jupyter environment. This proves package and validation compatibility, not yet
the future `anywidget` renderer or built-image integration tests.
