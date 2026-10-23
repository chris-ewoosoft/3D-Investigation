# ADR 0013 — A resume is a proven continuation, the manifest records the run that happened, and liveness is a lease

- Status: Accepted
- Date: 2026-10-23
- Supersedes: none
- Amends: ADR 0008 (the "with nothing to resume, start fresh" clause), ADR 0010
  (the mtime-only liveness rule for retention), ADR 0011 (the "epochs the
  framework ran" decision, split into budget vs. completed), ADR 0012 (the
  nightly resume assertions)

## Context

A fresh review of the package against its own documentation found four places
where the code did something other than what the README, an ADR and a green
suite said it did. Each defect was reproduced before it was fixed; none was
hypothetical.

**A resumed run's provenance described the invocation, not the run.** The
manifest's hyper-parameters were read from `TrainConfig` even on `--resume`,
where Ultralytics restores its argument set from the checkpoint and ignores the
command line. The same defect that ADR 0011 fixed for `epochs` was still present
for `imgsz`, `batch`, `seed`, `device` and the dataset path. Worse, the recorded
`source_weights_sha256` hashed the *base* checkpoint named by the model spec,
while the run actually started from the interrupted run's `weights/last.pt`.

**`--resume` was fail-open.** ADR 0008 said: "With nothing to resume from, the
pipeline says so and starts a fresh run rather than failing." That sentence
sounds cautious and is the opposite: an operator who asks to continue work is
silently given a new run, which may then overwrite the artifact they were
trying to preserve. The same fail-open shape existed for a completed
checkpoint (Ultralytics asserts on `epoch == -1`), a checkpoint without saved
arguments (the framework fills them with defaults), and a checkpoint trained on
different dataset bytes (the framework restores `data`; when that path is gone
it quietly substitutes the invocation's `--data`).

**Lock and run liveness were a single 24-hour mtime.** A crashed process leaves
its advisory lock behind; the next run waits out the stale window even though
the lock names an owner that is provably gone. The reverse was also true: a
live run whose lease crossed the window could have its lock stolen, and a
session made only of long epochs could have an active run pruned between
checkpoint writes. One timestamp cannot distinguish "slow" from "dead", yet the
code used it in both directions.

**The metric predicate accepted values that are not measurements.** `bool`
subclasses `int`, so `True` cleared any threshold as `1.0`; `NaN` compares
false against every minimum, so a metric that met no bar could slip through;
`Infinity` makes the manifest invalid JSON; and a value that overflows
`float()` raised `OverflowError` halfway through the quality gate. The backend
and the gate also had two separate spellings of the predicate, so they could
disagree about the same number.

**An artifact produced by a resume was still skippable.** `--skip-unchanged`
compared the recorded fields and the base-checkpoint digest. A resumed run can
match every recorded value and still not be reproducible by a fresh invocation:
it started from another run's checkpoint and ignored the argument set. The
manifest carried no field that said "this artifact came from a resume", so the
skip rule could not know.

## Decision

- **Resolve a `TrainPlan` before anything is observed or trained.** The backend
  decides the actual starting file up front: the base checkpoint for a fresh
  run, the newest resumable `last.pt` for a resume. The provenance snapshot
  hashes `plan.source_weights`, so the recorded digest is the file that entered
  the model. The plan also carries the dataset fingerprint the resume check
  compares against.
- **`--resume` fails closed.** Missing checkpoint, completed checkpoint
  (`epoch == -1`), a checkpoint with no saved training arguments, a checkpoint
  whose recorded dataset is missing on disk, an invocation whose dataset cannot
  be fingerprinted, and a dataset whose bytes differ are all `ResumeError`
  (exit 2). "Cannot prove this is the same experiment" is a refusal, never a new
  run.
- **The checkpoint's experiment wins, and the operator is told.** A resume
  restores the checkpoint's `imgsz`/`batch`/`seed`/`device`/`epochs`; the
  pipeline logs each value the invocation asked for and did not get. Only the
  dataset is a hard refusal. Silently overwriting the operator's flags is the
  same class of dishonesty as silently starting over.
- **The manifest records the run that happened.** `epochs` is the effective
  *budget* (the checkpoint's total on resume). `epochs_completed` is the number
  of epochs actually completed, cumulative for a resumed run — early stopping
  can finish a 100-epoch budget at epoch 40, and those are different facts.
  `resume_from` names the checkpoint a resumed run continued. Hyper-parameters
  come from the trainer's post-training arguments first, then the checkpoint's
  saved arguments, then the invocation; a fact none of them can provide stays
  absent rather than being invented.
- **A resumed artifact is never skippable.** `gate.already_current` returns
  `False` as soon as `resume_from` is present. Even when every recorded value
  agrees, a fresh invocation would not reproduce a run that started elsewhere.
- **Liveness is a lease with an owner, and the owner is evidence.** The
  advisory lock records `host` and `pid` in addition to its token, and a daemon
  heartbeat refreshes its mtime every `LOCK_HEARTBEAT_SECONDS` (30 s). A
  same-host owner that is provably **alive** keeps the lock however old its
  lease is; a same-host owner that is provably **dead** makes it stale at once
  (the claim protocol still serialises the steal); an owner that cannot be
  probed — another host, an older lock, a permission — falls back to
  `age > stale_after`. Unknown never means "dead".
- **A run's lease is a separate marker, and the pruner reads it.** The backend
  writes `.cvtrain-alive` (host + PID) into the run directory at every epoch
  and removes it when training returns, so a finished run is not kept forever
  by a process that happens to still be alive. The pruner keeps a run whose
  owner is alive, and counts the marker's mtime as activity for a run on
  another host. A killed process cannot clean up: its dead PID or its ageing
  marker lets the existing rules speak.
- **One metric predicate, in a leaf module.** `cvtrain/metrics.py` owns
  `coerce_metric`: reject `bool`, reject non-finite values, reject values that
  overflow `float`, keep `numbers.Real` (numpy scalars included). The backend
  uses it when reading the framework's result; the gate uses it when reading a
  hand-editable manifest. `metrics` is a leaf, so the policy module no longer
  imports the framework adapter to share a predicate. The manifest writer uses
  `json.dumps(..., allow_nan=False)` so a non-finite value is a typed
  `ManifestIntegrityError`, not a document strict readers reject.
- **The platform PID probe is chosen at import time.** `os.kill(pid, 0)` is
  correct on POSIX and **terminates** the process on Windows. Each platform
  gets the probe whose semantics it implements (`_probe_process`), and the
  shared `_is_stale`/`_process_alive` decision stays platform-neutral and fully
  covered on both CI operating systems.

## Consequences

- The resume check is the same experiment, byte for byte. A moved dataset is
  accepted (the same bytes at another path); a different dataset is refused
  with the two paths named. A restore-the-checkpoint workflow after moving the
  project now works instead of being refused by path.
- A completed run no longer reaches the framework's assertion. The operator
  gets a one-line actionable error that says to run without `--resume`.
- `--resume` on a machine where nothing was ever trained is an error, not an
  accidental fresh run. This is a behaviour change from ADR 0008, and it is the
  intended one: continuing work is a request that cannot be satisfied silently.
- The manifest can now answer "how much of the budget actually ran?" and "was
  this artifact resumed?" without inferring either from filenames.
- Crash recovery is immediate for a same-host crash, without weakening the
  protection of a live run: a live owner's lock is never stolen, even after the
  stale window. A shared volume without a probeable owner keeps the old
  age-based rule.
- `pytest`-style snapshot semantics for metrics are gone: a boolean, `NaN` or
  `Infinity` is "not reported", not a passing measurement, and the gate says so
  in the same words on both sides.
- The nightly real-framework job now uses a dedicated runs directory
  (`ResumeRuns`), asserts that the interrupted checkpoint has `epoch >= 0` and
  a 5-epoch budget, and asserts that the manifest for the resumed run records
  `epochs == 5`, `epochs_completed == 5` and a `resume_from` ending in
  `last.pt`. The previous job could find the earlier one-epoch run's completed
  checkpoint and "resume" it, which is exactly the fail-open case above.
- The mutation set grows from 80 to **101**. The new mutants pin the resume
  validation (missing/completed/unprovable/mismatched), the effective-facts
  extraction (budget, off-by-one completed count), the plan source digest, the
  skip refusal, metric sanitisation, `allow_nan`, the lease heartbeat, the
  owner-liveness branches, the run marker and the prune rule. `MIN_MUTANTS` is
  raised to 101 in the same change, so deleting one is a red gate.
- The suite grows from 459 to **499** tests, and coverage stays at 100 % of
  statements and branches (1 902 / 486). `cvtrain/metrics.py` is a new leaf on
  the dependency graph; `backend` and `gate` both point at it, and neither
  points at the other.
