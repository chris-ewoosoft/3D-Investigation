# ADR 0012 — The gate toolchain is hash-pinned, the gates run on the shipping platform, and `--resume` is a two-layer contract

- Status: Accepted
- Date: 2026-10-22
- Supersedes: none
- Amends: ADR 0007 (its "every claim must be pinned" rule, by naming the
  *file-level* tripwire as a third kind of pinning), and the CI layout of the
  package (the cvtrain gates leave the AIAssistant workflow for their own)
- Amended by: [ADR 0013](0013-resume-is-provenance-effective-facts-and-a-lease-is-evidence-of-life.md)
  (the real-framework resume assertions and the mutation-set counts)

## Context

The 2026-10-22 re-measurement confirmed the package's five measurable criteria
(C1–C5 in `Docs/SCORECARD.md`). It also found three gaps that the criteria did
not measure, so the debt table did not record them. All three have the same
shape: **a guarantee that lived in prose or in a configuration file, where
nothing made it false**.

**The tools that decide green/red were not pinned.** The cvtrain CI job
installed its gate toolchain like this:

```yaml
pip install ruff mypy coverage PyYAML==6.0.2 onnx==1.21.0 "onnxruntime>=1.20.1"
```

`ruff` decides whether the code is clean, `mypy` decides whether the types
hold, `coverage` decides whether the floor is met. A new release of any of them
can change a verdict — in either direction — for reasons that have nothing to
do with the code under test, and nobody reviews that change because it never
touched the repository. The Python service on the other side of the repo had
already solved this: its gates come from a hash-pinned lock installed with
`--require-hashes` (ADR 0023 there), so a republished or tampered wheel cannot
enter a gate run at all. The cvtrain side had simply not been brought along.
`requirements.txt` had the same shape: `onnxruntime>=1.20.1` — a range — for
the engine the export contract is validated against, which is the engine the
Qt application ships.

**The gates ran on a platform the product does not ship on.** The cvtrain job
ran on `ubuntu-latest`. The product ships on Windows, and this package touches
Windows-specific filesystem semantics: the advisory lock is created with
`O_CREAT | O_EXCL`, the publish is a staging file plus `os.replace`, and a
file that is being replaced can fail with a sharing violation that does not
exist on POSIX. A Linux-only CI reports green while the platform the users run
was never checked. The package's own documentation claimed the lock, the
atomic publish and the retention rules were pinned — a claim that a POSIX
runner cannot fully back.

**The one behaviour a fake backend cannot vouch for was verified on a schedule
only.** `--resume` needs the *real* framework to have written a checkpoint that
carries its epoch counter and optimizer state, and to accept `resume=True`.
The offline suite drives the whole pipeline with a fake backend, so it cannot
show any of that. The real check lived in the nightly workflow — which no pull
request triggered, so a change that broke the resume seam could reach `main`
and be discovered the next morning.

Two structural problems came with the third one. The cvtrain gates lived
*inside* the AIAssistant workflow, so an AIAssistant-only pull request paid
for the whole cvtrain matrix, and the path filters of the two independent
packages were interleaved in one list.

## Decision

- **The gate toolchain is hash-pinned.** `AIComputerVision/requirements-test.txt`
  declares it (ruff, mypy, coverage, plus the packages the export contract
  needs); `AIComputerVision/requirements-test.lock` is its hash-pinned
  resolution, generated with `uv pip compile … --generate-hashes` and
  installed in CI with `pip install --require-hashes`. Every requirement in
  both files is an *exact* pin, with one documented exception: `torch`, whose
  CUDA and CPU builds share a version number and are selected through the
  index the package is installed from — so it is pinned as a bounded range
  (`>=2.7,<3`), not as an exact version. `onnxruntime` is pinned to exactly
  the `ONNXRUNTIME_VERSION` in `CMakeLists.txt`: the engine that validates an
  export must be the engine that loads it. The nightly's training stack is
  pinned the same way (`torch==2.7.0`, `torchvision==0.22.0` from the CPU
  index, `pillow==11.0.0`), because a floating install makes that job's
  verdict a property of the day it ran.
- **The gates run on the platform the product ships on.** The cvtrain gates
  move into their own workflow, `.github/workflows/Build-cvtrain-ci.yml`, and
  run on `ubuntu-latest` *and* `windows-latest`. The mutation sweep runs in
  full on Linux and re-runs the platform-sensitive subset on Windows
  (`cvtrain/fsio.py` — the advisory lock, the atomic publish, the retention
  rules: 19 of the 101 mutants, ~5 minutes). The script gained `--files` for
  that; a subset run **cannot lower the floor**, which is still checked
  against the whole declared set, and the summary always states how many of
  the declared mutants the run covered.
- **`--resume` is a two-layer contract, and both layers have a mechanism.**
  Layer one is the pipeline's seam — pick the newest checkpoint, hand
  `resume=True` to the framework, do not create a new run directory — pinned
  offline by `test_backend.TrainTests` and the resume mutants. Layer two is
  the real framework: the nightly workflow interrupts a run, resumes it, and
  asserts that the run directory count did not grow. Layer two now also runs
  for pull requests that touch the package, so it is not a nightly-only event.
  [ADR 0013](0013-resume-is-provenance-effective-facts-and-a-lease-is-evidence-of-life.md)
  strengthens both layers: the pipeline refuses a missing, completed,
  unprovable or dataset-mismatched checkpoint instead of starting over, and
  the nightly job uses an isolated runs directory and asserts the effective
  budget, the completed count and `resume_from` in the manifest.
- **The new guards are file-level tripwires, and they are pinned by inversion.**
  They make no decision inside `cvtrain/`; they make a decision *about* the
  repository's files (requirements, workflows). The mechanism is the same shape
  as the existing opset-ceiling tripwire in `tests/test_contract.py`: invert
  the decision in the file, and the owning test must go red. Each of the seven
  inversions was executed and recorded in `Docs/SCORECARD.md` §3.

## Consequences

- The verdicts of the gates are reproducible: the same lock produces the same
  lint, the same type check and the same coverage numbers, and a wheel whose
  digest does not match cannot enter a gate run.
- A Windows-only regression in the filesystem semantics — the advisory lock,
  the atomic publish, the retention rules — is now visible in CI instead of
  on a user's machine, and the platform-sensitive decisions are mutation-checked
  on the platform that ships.
- A change that breaks the resume seam against the real framework is caught
  by the pull request that introduces it, not the next morning.
- The two Python packages no longer share a workflow: an AIAssistant pull
  request does not pay for the cvtrain matrix, and a cvtrain pull request does
  not pay for the AIAssistant gates. The cvtrain workflow's path filters list
  the C++ files the cross-language contract tests read, so a Qt-side change
  still re-runs the Python gates.
- The suite grew from 443 to 459 tests at the time of this decision; after
  [ADR 0013](0013-resume-is-provenance-effective-facts-and-a-lease-is-evidence-of-life.md)
  it is 499. Coverage stays at 100 % of statements and branches (1 902 / 486).
- The mutation set stays at **80** in this decision. The new guards carry no
  mutant because they add no decision to the package's source — the same
  reason `checks.py` and `logging_setup.py` carry none: they hold no
  load-bearing decision. A guard that *did* add one would need its mutant in
  the same commit (ADR 0007). ADR 0013 is exactly such a decision set: it adds
  resume, effective-fact, metric and lease guards, and the set grows to
  **101**.
- One boundary is stated rather than papered over: the *training* stack
  cannot be hash-pinned, because the CUDA and CPU builds of `torch` share a
  version number and are not the same artifact. It is version-pinned and used
  by a behavioural job, not by a gate that decides green or red about the
  package's logic — and that is where the boundary of this ADR lies.
- The mutation sandbox now mirrors the requirement files and the workflows as
  well as the C++ sources. The reason is the same one that made it copy `src/`
  in the first place: a file the suite reads but the sandbox does not copy
  turns a guard into a *skip*, and a skip is not a verdict. The first subset
  run after this change failed its baseline for exactly that reason — the new
  presence guard refused to pass on a tree where the files it guards were
  missing.
