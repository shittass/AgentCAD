# Local benchmark

A quick, self-contained measurement of the runtime on your own machine: how long its calls take,
how much context it returns to an agent, and whether the drawings it writes are still correct.
No LLM, no AutoCAD, no network — the default run takes about five seconds, the full one about twenty.

This is the development counterpart to [docs/benchmarks.md](../docs/benchmarks.md). That document
records the agent trials and the upstream capability matrix, which need models, AutoCAD and hours.
This one answers the question a change raises: *did it get slower, or did it break a drawing?*

```bash
# from the repository root, with the development venv (Windows: .venv\Scripts\python)
.venv/bin/python -m benchmarks
```

A run prints one row per workload, the delta
against the recorded baseline, the six quality checks, and the step breakdown of a drafting session:

```
AgentCAD quick benchmark | Windows 11 | Python 3.13.15 | ezdxf 1.4.5 | 24 CPUs | 59b8ba6
group       workload                         n      median         p90   payload   vs base
ops         op_single_call                   8    50.37 ms    62.20 ms    0.9 KB     1.01x
ops         op_batch_50                      5    63.54 ms    64.84 ms    4.2 KB     1.00x
...
quality: 6/6 checks passed
elapsed: 4.2 s
```

`median` is the usual number to read; `p90` shows the tail. `payload` is the JSON the call returns,
which an agent then pays context for (roughly a token per 4 characters).

## Profiles and workload groups

`--profile quick` (default) stays near five seconds; `--profile full` adds the process-level and
slow paths and takes about twenty. `--only ops,read` runs a subset; `--list` names the groups.

| Group | Profiles | What it measures |
|---|---|---|
| `startup` | quick, full | Cold `python -m agentcad --help`; in `full` also importing `agentcad.service` |
| `drawing` | quick, full | `create_drawing`, `draw_from_spec` (a 100×60 plate, 27 planned operations) |
| `ops` | quick, full | The transaction layer: one op per call, a 50-op batch, a dry run, `strict`, and a 500-op batch in `full` |
| `read` | quick, full | `read_drawing`, `inspect_entities`, `validate_drawing`, `get_diff` on a dimensioned plate |
| `revision` | quick, full | `history`, `list_checkpoints`, `rollback`, `recover` |
| `discovery` | quick, full | `list_operations` (the catalog an agent reads first), one-op schemas, `search_tools` |
| `export` | full | SVG, PDF and JSON export |
| `scale` | full | A 2,000-entity drawing: read, inspect and validate |
| `mcp` | full | The real agent path: MCP process start, `tools/list` and a `tools/call` round trip over stdio |
| `session` | quick, full | One drafting session end to end: draw, dimension, note, validate, export, revise, read, undo. Runs twice for a less noisy single number (three times in `full`); the faster run is reported |

Every `ops` call includes the whole transaction: workspace lock, file load, execute, save, checkpoint,
journal, and the independent re-read validation. That is why one op costs about 50 ms while 500
operations in one batch cost about 380 ms — the fixed per-call cost dominates, and batching is what
an agent should do.

## Baseline: one per machine

Numbers are only comparable on the same machine and setup, so the comparison is gated on a
fingerprint: OS, CPU count, Python and ezdxf versions, plus a calibration loop (`calibration_ms`)
that measures raw Python speed. If any of those moved, the run says so and does not compare.

```bash
.venv/bin/python -m benchmarks --save      # record this machine as benchmarks/baseline.json
.venv/bin/python -m benchmarks             # later runs show "vs base" and any SLOW rows
.venv/bin/python -m benchmarks --gate      # exit 1 when a workload exceeds --tolerance (default 1.5x)
```

Record a new baseline after you upgrade Python, ezdxf or the CPU, and on every new machine.
The committed `baseline.json` is the maintainer's Windows machine; a CI runner will report
"not compared" instead of a false regression.

To compare two revisions, record the old one and point the new run at it; the machine gate still
applies, so both runs must be on this computer:

```bash
git switch <old-revision> && .venv/bin/python -m benchmarks --save /tmp/before.json
git switch <new-revision> && .venv/bin/python -m benchmarks --baseline /tmp/before.json --gate
```

Workloads under 2 ms are not gated: process scheduling moves them by multiples, and a ratio there is
noise rather than a regression.

## The quality half

Six deterministic checks run on drawings written to disk and are read back with ezdxf, not taken
from the runtime's own report. They fail the process exit code:

| Check | Guarantee |
|---|---|
| `plate_from_spec` | Four R4.0 corner holes 10 mm in, 7+ dimensions, 100.0×60.0 outline, template layers |
| `dimensions_attached` | Three hand-added dimensions have attached geometry blocks and raise no attachment warning |
| `undo_restores` | `rollback` restores the exact prior file bytes (sha match) and the previous circle |
| `batch_family` | `apply_batch` writes three independent drawings with the requested diameters |
| `revision_guard` | A stale `expected_revision` is refused with `STALE_REVISION` and does not touch the file |
| `validation_catches` | Geometry outside `bounds` is reported as `OUT_OF_BOUNDS`, not silently committed |

## Adding a workload

A workload is a function registered against a group; it returns `Sample` rows and must leave the
workspace in a state the next run can reuse (`if_tag_exists="replace"`, unique file names).

```python
@workload("mytools", description="what this group covers")
def mytools(ctx: Context) -> list[Sample]:
    plate = ctx.plate("mytools")
    return [Sample("my_call", "mytools",
                   measure(lambda: ctx.runtime.measure_entities(plate, "all"), repeat=ctx.repeat(5),
                           payload=True))]
```

`measure` runs warmup calls first, raises when the measured call answers `ok: false` (so a broken
workload cannot record a fast error path), and keeps serialisation out of the timer. Keep the
`quick` profile near five seconds: put anything slower behind `profiles=("full",)`.

## Caveats

- **Not a CI target.** Laptops, antivirus and thermal state move these numbers; the fingerprint and
  `--tolerance` keep the gate honest, but a red gate usually means "measure again", not "revert".
  CI owns correctness through the pytest suite; the quality half here also runs there as
  `tests/test_t_quick_benchmark.py`.
- **The first call after boot is slower.** Warmup covers interpreter and file-cache effects, not a
  cold disk.
- **Absolute milliseconds belong to the machine.** Compare deltas, not values, with anyone else.
- `--keep` leaves the workspace behind (its path is printed) so you can open the drawings.
