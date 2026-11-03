# Repair replay: what `repair.py` recovers from recorded agent errors

2026-11-04. This report measures `src/agentcad/repair.py` against the tool errors recorded in `examples/benchcad` and `examples/trials`: how many real failures the layer turns into the call the agent eventually made, and how many round-trips that would have saved. The measuring tool is `scripts/replay_repairs.py`; the numbers below come from one run of it over the whole corpus.

## Headline numbers

- The corpus holds **1,081 AgentCAD tool errors** in 1,381 task traces (1,517 trace files scanned; 136 produced no AgentCAD calls: 100 BenchCAD and 33 trials runs where the agent wrote code instead of using MCP tools, plus 3 empty logs).
- **96 errors (8.9%)** sit in the four namespaces the repair layer owns: operation name, parameter name, enum value, selector key. The other 985 are client-side refusals, semantic failures, missing files, or server bugs.
- The layer converts **8 of those 96 (8.3%)** into the call the agent eventually made: 7 operation-name suggestions and 1 enum fold. That is 15 agent round-trips saved, 1.23 per converted error.
- If every error with a usable ground truth were converted, the same corpus would have cost **48 fewer round-trips** instead of 15.
- The generic matcher is accurate but not authoritative: on the 18 candidates it offered, 8 were right (precision@1 44.4%); its single automatic rewrite (the enum fold) was right.
- The synthetic sweep is the precision figure that does not depend on which mistakes a model happened to make: **97.5% top-1 over 2,294 misspellings of the 71 registry operations** and **99.5% over 1,311 misspellings of required parameter names**.

## Corpora

| Corpus | Tasks | Tool calls | AgentCAD calls | Errors | Addressable | Converted |
|---|---:|---:|---:|---:|---:|---:|
| `examples/benchcad/runs/*/v2c` | 1,200 | 13,802 | 11,927 | 1,001 | 94 | 7 |
| `examples/trials` | 181 | 2,416 | 1,974 | 80 | 2 | 1 |
| **total** | **1,381** | **16,218** | **13,901** | **1,081** | **96** | **8** |

Both agent formats are parsed: 832 traces are Claude Code JSONL (`assistant.tool_use` / `user.tool_result`), 682 are Codex event streams (`item.started` / `item.completed` with `mcp_tool_call`), and 3 files contain neither. The BenchCAD arms are the 13 benchmark configurations (pilot, code, MCP, hybrid variants); the trials are the classic-task and hard-task runs.

## Method

- **Errors are read from the envelope, not from `is_error`.** The runtime reports business failures inside a successful MCP transport as `{"ok": false, "error": {...}}`. A tool result that is not an envelope at all (a bare tool name rejected by the client, a `Read` on a missing file) is counted as `TRANSPORT_ERROR` and treated as out of scope.
- **Only AgentCAD calls are counted.** A call is in scope when its tool name ever produced an AgentCAD envelope in the corpus (58 tool names). Client tools (`Read`, `Bash`, `Edit`, ...) are dropped before any counting.
- **The dedupe key is the `tool_use` block id, not `message.id`.** An assistant message is logged as several events that share `message.id` but carry different content blocks (`thinking`, `text`, `tool_use`). Keying on the message id drops the `tool_use` events that follow a thinking event: a first pass written that way saw 7,500 calls in the BenchCAD files; keying on the block id sees 13,802. Deduplicating by block id removes only genuine repeats, of which there are none.
- **Ground truth is the agent's own fix.** For each failed call, the next successful call in the same task with the same tool is the reference; for `apply_operations` the operation at `error.op_index` is compared with the operation at that index in the successful call. 39 of the 96 addressable errors have such a reference (`with_ground_truth`); the rest are scored as unjudged rather than as failures.
- **A parameter ground truth must be a value-preserving rename.** The rejected key's value must appear under exactly one new key in the successful operation (for example `pocket: length=13` → `depth=13`). If the agent also changed the value, or rebuilt the operation, there is no rename to compare against.
- **Auto versus suggestion.** `auto` means `normalize_op` / `normalize_params` / `normalize_enum` rewrites the call in place and the rewrite equals the ground truth (`converted`). `suggest` means the top-1 candidate of `suggest_ops` / `suggest_params` names the ground truth; the caller still has to resend the call. Both count as converted; only the automatic path is safe to apply silently.
- **`suggest_ops` is ranked against the operation catalogue the traces were produced with**, this checkout's 71 `REGISTRY` operations plus the 15 part operations the traces use (`add_sketch`, `pad`, `pocket`, `modify_feature`, `primitive`, ...). That catalogue is still generous to the layer: it does not have the part build's parameter schema, only the parameter names successful calls used. With this checkout's `REGISTRY` alone, none of the 16 rejected operation names resolves to the right operation.
- **One round-trip is one assistant message.** A conversion saves the messages between the failed call and the successful retry (median 1, mean 1.23, maximum 3).

## Replay results

| Class | Errors | With ground truth | Auto rewrites | Suggestions | Converted | Precision@1 |
|---|---:|---:|---:|---:|---:|---:|
| operation name | 16 | 16 | 0 | 15 | 7 | 0.47 |
| parameter name | 76 | 22 | 0 | 2 | 0 | 0.00 |
| enum value | 1 | 1 | 1 | 0 | 1 | 1.00 |
| selector key | 3 | 0 | 0 | 0 | 0 | — |
| **total** | **96** | **39** | **1** | **17** | **8** | **0.44** |

Coverage is 8/39 of the errors with a ground truth (20.5%) and 8/96 of all addressable errors (8.3%). Turns: 15 saved by those 8 conversions, against a ceiling of 48 if every judged error had been converted in place. No automatic rewrite targeted the wrong operation or parameter (`wrong_auto` is 0), because no alias in the table matches a 3D name; the one automatic rewrite is the `add_text` enum fold.

| Round-trips | Value |
|---|---:|
| errors with a ground truth | 39 |
| errors converted | 8 |
| median round-trips from the failed call to the successful retry | 1 |
| mean | 1.23 |
| maximum | 3 |
| saved by the 8 conversions | 15 |
| saved if all 39 judged errors were converted | 48 |

The three selector errors are value errors (`chamfer.edges: 'bottom' is not an edge selector`), not misspelled filter keys; `normalize_filter` has nothing to rename. The corpus contains no filter-dict key typos.

## Where the errors are

| Error code | Count | Share | Handled by `repair.py` |
|---|---:|---:|---|
| `TRANSPORT_ERROR` (no AgentCAD envelope) | 474 | 43.8% | no — rejected by the client, never reaches the runtime |
| `EXPORT_MISMATCH` | 170 | 15.7% | no — the written STEP reads back differently |
| `FILE_EXISTS` | 105 | 9.7% | no — needs `overwrite`, not a rename |
| `UNKNOWN_PARAM` | 76 | 7.0% | yes, parameter namespace |
| `UNKNOWN_OPERATION` | 66 | 6.1% | no — `list_operations(ops=[...])` did not list part operations (server catalog bug) |
| `FEATURE_FAILED` | 38 | 3.5% | no — geometry |
| `FILE_NOT_FOUND` | 23 | 2.1% | no — missing prerequisite |
| `SCRIPTS_DISABLED` | 19 | 1.8% | no — policy |
| `UNKNOWN_OP` | 16 | 1.5% | yes, operation namespace |
| `SELECTOR_EMPTY` | 14 | 1.3% | no — the filter matched nothing |
| all others (20 codes) | 80 | 7.4% | mostly no |
| **total** | **1,081** | **100%** | 96 addressable (8.9%) |

The dominant error is not a name error at all. 441 calls used a bare tool name (`apply_operations` instead of `mcp__agentcad__apply_operations`), which the client refuses before the server sees the call; 30 more returned a result larger than the client's output limit and 3 were other client-side failures. The repair layer cannot help with any of them, because there is no envelope to repair. `EXPORT_MISMATCH` and `FILE_EXISTS` are the next two, and both are argument-value or precondition problems rather than renames.

## The gaps the traces show

Rename pairs extracted from the 96 addressable errors — what a table that knew this build would need:

| Operation name | Ground truth | Count |
|---|---|---:|
| `edit_feature` | `modify_feature` | 7 |
| `edit_sketch` | `add_sketch` | 5 |
| `edit_feature` | `add_sketch` | 1 |
| `edit_feature` | `primitive` | 1 |
| `set_feature` | `modify_feature` | 1 |
| `set_dimension` | `add_sketch` | 1 |

| Operation | Rejected parameter | Ground truth | Count |
|---|---|---|---:|
| `pocket` | `length` | `depth` | 11 |
| `modify_feature` | `params` | `set` | 8 |
| `primitive` | `shape` | `kind` | 4 |
| `pocket` | `midplane` | `symmetric` | 2 |
| `modify_feature` | `shapes` / `length` / `params` / `plane` | no rename in the retry | 15 / 13 / 9 / 4 |

Of the 16 operation errors, eight were fixed by renaming to `modify_feature` (7 `edit_feature`, 1 `set_feature`); `suggest_ops` names it first for all seven `edit_feature` cases, and puts `delete_feature` (0.80) ahead of it for `set_feature`. Five were fixed by `add_sketch`, where `edit_hatch` — a 2D operation this checkout does have — ties `add_sketch` at 0.762 and wins the ordering, so the right answer is second. The remaining three were rebuilt as a different operation (`edit_feature` → `add_sketch` / `primitive`, `set_dimension` → `add_sketch`), and `set_dimension` produced no candidate at all. The 7-of-15 precision is therefore five second-place ties, one name outside the top three, two intent changes, and one silent error.

On the parameter side the layer is mostly silent rather than wrong: `suggest_params` offered a candidate for 2 of the 76 errors (both wrong) because `pocket.length` → `depth` and `modify_feature.params` → `set` are far apart in edit distance, below the 0.6 cutoff. The 22 value-preserving renames are exactly the cases an alias table would fix, and the top four pairs cover 25 of the 78 rejected keys.

## Synthetic typo sweep

Every variant below is generated deterministically from the 71 `REGISTRY` operations; variants that are themselves registered operations or aliases are skipped, so the intended operation is unambiguous.

| Bucket | Variants | Top-1 correct | Rate |
|---|---:|---:|---:|
| one character deleted | 737 | 737 | 100% |
| adjacent characters transposed | 684 | 679 | 99.3% |
| one character doubled | 613 | 613 | 100% |
| verb swapped (`add_` ↔ `draw_` ↔ `create_` ↔ `set_` ↔ `edit_` ↔ `make_`) | 260 | 208 | 80.0% |
| **operation names** | **2,294** | **2,237** | **97.5%** |
| required parameter names | 1,311 | 1,304 | 99.5% |

The verb bucket carries nearly all the misses: `create_leader` ranks `create_layer`, `create_polyline` ranks `create_layer`, `edit_ray` ranks `edit_hatch`. A verb is a strong lexical signal that `difflib` follows, even when the noun is the part that identifies the operation. The alias table covers the common verb swaps (`draw_line`, `create_circle`, ...), and the sweep skips variants that are already aliases, so 80% is the rate for verb variants the table does not know.

## Why coverage is low

1. **The client refuses 44% of the failures before the runtime exists.** A bare tool name is not an MCP tool, so there is no envelope and no `error.details["repair"]` to enrich. Fixing that is a tool-registration or prompt problem, not a repair-table problem.
2. **This checkout has no 3D part runtime.** The BenchCAD traces were produced by a build with `add_sketch`, `pad`, `pocket`, `modify_feature` and 11 other part operations; `REGISTRY` here has 71 2D operations and none of those. The alias table's targets are pinned to `REGISTRY` by `tests/test_r_repair_core.py`, so a 3D alias cannot exist in this tree even though the traces show it would be the single highest-value entry. 94 of the 96 addressable errors are BenchCAD part operations.
3. **The remaining errors are not name errors.** `EXPORT_MISMATCH` (170), `FILE_EXISTS` (105), `FEATURE_FAILED` (38) and `FILE_NOT_FOUND` (23) need value changes, preconditions or geometry, which the four repair primitives do not model. `UNKNOWN_OPERATION` (66) is a server-side catalog bug: `list_operations(ops=[...])` only knew 2D operations.

The measured conversion is therefore what this checkout can do, not what the layer could do against the build the traces came from: the names that would convert are in the traces, but the table that would hold them cannot land here while every alias target must exist in `REGISTRY`.

## Envelope-level repairs the replay does not count

Two repairs happen above the four primitives measured here:

- **`FILE_EXISTS` (105 in the corpus)** now carries `error.repair.suggested_call` with the rejected call and `overwrite: true`, for `create_drawing`, `draw_from_spec`, `export_drawing` and `import_drawing` (`service.py:_overwrite_repair`). Re-rendering a preview over an earlier `preview.png` is the case the BenchCAD notes single out. The replay scores only name, enum and selector rewrites, so this class is not in the 96 addressable errors, but it is a single resend from success and the caller keeps the decision (the repair is offered, never applied).
- **Selector key aliases** (`layr` → `layer`, `lyr`, `layers`, `types`, `tags`, …) are applied in `model.select`; a key that is neither accepted nor a known misspelling now raises `BAD_SELECTOR` instead of widening the selection to the whole layout. The corpus happens to contain no filter-dict typos — its three selector errors are value errors — so the measured contribution is zero, while the data-loss case is pinned by `tests/test_r_repair_selectors.py` and `tests/test_r_repair_integration.py`.

## Limitations

- **No 3D part runtime in this checkout.** BenchCAD 3D errors can only be judged by the generic rules (`op.alias`, `difflib`) against a catalogue reconstructed from successful calls; their real parameter schema, enums and selectors are unavailable, so parameter suggestions for those operations are necessarily weak.
- **Ground truth is the agent's retry, not an oracle.** 57 of the 96 addressable errors have no unambiguous ground truth and are excluded from coverage rather than counted as failures; the agent sometimes fixed the intent rather than the call.
- **The gap tables are in-sample.** They are the renames the same traces contain. A table built from them and re-measured on the same traces would be self-fulfilling; the 48-round-trip ceiling is a counterfactual, not a held-out result.
- **Round-trips are assistant messages, not tokens or seconds.** A call batched into a message with other calls counts one turn, and a suggestion requires a resend that the auto path does not.
- **Rollout scope.** The corpus is 13 BenchCAD arms plus the trials, one agent configuration and one run per task, so the error mix is a property of these runs rather than of AgentCAD in general.

## Reproduce

```sh
./.venv/Scripts/python.exe scripts/replay_repairs.py --json C:/tmp/repairs.json
./.venv/Scripts/python.exe scripts/replay_repairs.py --corpus trials --limit 20 --json C:/tmp/trials.json
```

The full run takes under 3 seconds and reads 1,517 trace files. The JSON document carries the per-class counters, the rename tables, the synthetic sweep with per-bucket counts, ten sample errors and the limitations above; `tests/test_r_repair_replay.py` pins its structure and the semantics of the three converted examples on a fixture trace.
