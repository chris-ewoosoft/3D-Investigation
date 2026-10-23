# ADR 0010 — Retention is a policy, not an accident

- Status: Accepted
- Date: 2026-10-21
- Supersedes: none
- Applies to: `AIComputerVision/cvtrain/fsio.py`, `AIComputerVision/cvtrain/cli.py`,
  `AIComputerVision/cvtrain/config.py`, `AIComputerVision/cvtrain/backend.py`

## Context

`runs/<model>/` grows by one directory per training run, and a run is not
small: the tracking model writes a `yolo11x` checkpoint of a few hundred
megabytes into every one of them. Nothing in the pipeline ever removed a run,
so the directory's size was a function of how many times somebody had trained
— a fact that was true, discoverable, and not owned by anybody.

`Models/*.onnx.bak` is the other accumulation, but a bounded one: the
publication policy keeps exactly one generation of rollback copy, so there is
at most one `.bak` per model. That half needed no decision.

The `SCORECARD.md` debt table was the right place to record the gap, and it
records debts as "measured and written down" rather than as "fixed". But a
retention policy is not a debt to be *measured* — it is a capability the
package does not have, and the table's own rule ("closing a debt = a decision
recorded as an ADR + a mechanism that turns it red") points at writing the
capability, not at a note.

The tempting shortcut was to prune automatically at the end of a training run.
It was rejected: the pipeline would then delete user data without being asked
to, on the basis of a heuristic, as a side effect of a command that was asked
to do something else. The operator's decision, expressed as a command, is the
safer shape — and it composes with the package's existing "exactly one mode"
discipline, where `--write-manifest` and `--verify-manifest` are commands
rather than side effects.

## Decision

- **`--prune-runs` is a mode of its own.** It never trains, never exports and
  never touches `Models/`: the published artifacts and their provenance are
  not its business. It joins `--check`, `--dry-run`, `--write-manifest` and
  `--verify-manifest` in the mutually exclusive group, so it cannot be
  combined with a training run by accident.
- **The count of kept runs is the flag's argument.** `--prune-runs` keeps one
  run per model; `--prune-runs 3` keeps three. There is deliberately no
  separate `--keep-runs` modifier: a flag that is silently ignored unless
  another flag is also present is a trap, and this package has already
  removed one of those.
- **The newest runs of every model survive.** The rule is per model, not per
  directory, so pruning the tracking runs cannot cost the detection runs the
  checkpoint `--resume` would continue from.
- **A run that is still alive is never deleted.** The framework rewrites
  `weights/last.pt` after every epoch, so a fresh checkpoint is the proof
  that a run is still going. A run whose last write is younger than
  `RUN_ALIVE_SECONDS` (24 h — the same order as `LOCK_STALE_SECONDS`) is kept
  whatever its position in the history. For a destructive operation the safe
  direction is *keep*. Amended by [ADR 0013](0013-resume-is-provenance-effective-facts-and-a-lease-is-evidence-of-life.md):
  a run also carries a `.cvtrain-alive` lease naming its host and PID, and an
  owner that is provably alive is kept even when the 24 h window has passed;
  the marker's mtime counts as activity for a run on another host.
- **Only directories this pipeline created are candidates.** A run is
  recognised by the `RUN_DIR_PREFIX` prefix, which is the same constant the
  backend names a run with — one spelling, two readers.
- **A refused deletion is a typed error, and the runs already deleted stay
  deleted.** `remove_tree` raises `StorageError` (exit `2`), and the prune is
  not a rollback: a cleanup that cannot finish is still a cleanup.

## Consequences

- `runs/` has a bounded size that the operator chooses, and the command that
  enforces it is explicit, reversible in the sense that it only ever removes
  directories the pipeline wrote.
- The retention rules are pinned: four mutants invert them (the "keep the
  newest" rule, the "never delete a live run" rule and the "only our own
  directories" rule in `fsio.py`, plus the mode dispatch in `cli.py`), and
  `tests/test_fsio.py` covers the rules directly, including the two
  typed-error paths.
- The `--prune-runs` mode is documented in the README, and the
  README ↔ CLI two-way test in `tests/test_architecture.py` fails if the
  flag and the documentation drift apart.
- The package still has no *automatic* retention. That remains an operator
  decision, deliberately: a training run must never delete anything the user
  did not ask it to delete.
