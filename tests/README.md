# C++ verification stack

Four gates prove the C++ side of the repository. All of them run locally with
the commands below and are enforced in `.github/workflows/Build-C++-ci.yml`
after the build step. The reference run recorded here is
2026-10, Windows 11, MSVC 2022 (VS 17.12), Qt 6.9.3, Release.

| # | Gate | Command | What it proves |
|---|------|---------|----------------|
| 1 | Architecture boundaries | `cmake -DSOURCE_DIR=<repo> -P tests/CheckArchitectureBoundaries.cmake` | Layering invariants (heavy/light PCH split, plugin includes, blocking-I/O waits, singleton confinement …). Runs in CTest as `TestArchitectureBoundaries`. |
| 2 | Unit + integration suite | `ctest --test-dir build -C Release --output-on-failure` | 25 tests: kernel units, plugin host/resolver with an injected loader, real-plugin smoke, `TestAppShell` composition-root wiring, `TestAIAssistant` request state machine (injected transport), plus `TestReconstructionFinalize` (labelled `slow`). |
| 3 | Coverage gate | `python tests/coverage_gate.py` | Measured line coverage of the kernel/guard/composition files, with per-file floors; runs OpenCppCoverage over the owning tests. |
| 4 | Mutation check | `python tests/mutation_check.py` | Every load-bearing guard decision is *pinned*: one inverted decision per mutant, rebuilt and re-run; the owning test must fail. |

Run them from the repository root with Qt and the vcpkg `bin` directory on
`PATH` (the same environment the CTest step uses), e.g.:

```powershell
$env:PATH = "C:/Qt/6.9.3/msvc2022_64/bin;$env:VCPKG_INSTALLED_DIR/x64-windows/bin;$env:PATH"
ctest --test-dir build -C Release -LE slow --output-on-failure   # fast round
python tests/coverage_gate.py
python tests/mutation_check.py
```

## Gate 3 — coverage floors

`tests/coverage_gate.py` runs the owning tests under OpenCppCoverage, merges
the per-test cobertura reports (a line counts as covered when *any* test
executes it) and enforces the ratchet in `FLOORS`. Measured reference:

| file | measured | floor |
|------|----------|-------|
| `src/app/appshell.cpp` | 100 % (61/61) | 100 |
| `src/services/ai/chatsessionmanager.cpp` | 100 % (56/56) | 100 |
| `src/services/ai/chatsessionstore.cpp` | 100 % (46/46) | 100 |
| `src/services/settings/settingsservice.cpp` | 100 % (18/18) | 100 |
| `src/services/shell/shellservice.cpp` | 96.1 % (49/51) | 96 |
| `src/core/plugindependencyresolver.cpp` | 93.3 % (84/90) | 93 |
| `src/core/secretvault.cpp` | 92.9 % (26/28) | 92 |
| `src/core/pluginhost.cpp` | 91.8 % (90/98) | 91 |
| `src/core/languagemanager.cpp` | 96.5 % (55/57) | 96 |
| `src/core/usermanager.cpp` | 57.5 % (253/440) | 57 |
| `src/core/appconfig.cpp` | 43.9 % (36/82) | 43 |

Rules for the table:

* Floors are a ratchet — raise one when its file reaches the next integer,
  never lower one to make a change pass.
* The remaining uncovered lines are documented classes: closing braces that
  MSVC attributes as lines, DPAPI-failure paths (`SecretVault` 38/43), the
  QPluginLoader error branches, and the untested parts of `UserManager`
  (license/mail/user-management flows outside the credential contract).
* `--measure` prints every file seen in the reports without enforcing, so the
  next candidate for a floor is easy to pick.

**Header-only guards are not gated by coverage.** `ServiceRegistry.h` and
`MailThreadGuard.h` compile inline into their callers under `/O2 /Ob2`, so the
tool cannot map their lines reliably. Their decisions are pinned by gate 4
instead (null registration refused, GUI-thread warning fires).

Release builds must ship PDBs for gate 3 to map anything: `CMakeLists.txt`
section 5b adds `/Zi` to the Release compile flags and `/DEBUG /OPT:REF
/OPT:ICF` to the link flags (symbols without changing Release codegen). The
tool is discovered through `OPENCPPCOVERAGE_EXE`, `PATH`, or the known install
locations; CI installs it with `choco install opencppcoverage --no-progress -y`.

## Gate 4 — mutation check

`tests/mutation_check.py` owns the "is the guard pinned?" question for the
guards that coverage can only say are *executed*:

* `ServiceRegistry` refuses null registration (mutation: accept it),
* `PluginHost` API-version gate, discovery library filter, initialize()
  rollback, reverse-order shutdown,
* `PluginDependencyResolver` unknown phase, missing dependency, dependency
  cycle,
* `SecretVault` legacy-plaintext passthrough + empty-stays-empty,
* `UserManager` PBKDF2 iteration count,
* `MailThreadGuard` GUI-thread warning,
* `AppShell` deferred-phase idempotence,
* and three **planted violations** that the architecture guard itself must
  reject: a concrete service include in a plugin, a bare
  `waitForFinished()` in `src/`, a heavy SDK include in `src/utils/`.

Discipline (mirrors `AIAssistant/scripts/mutation_check.py`):

* every owning test must pass on the unmutated tree first — a missing DLL on
  `PATH` must never masquerade as a killed mutant;
* an anchor that no longer matches is STALE and fails the run;
* a mutant that does not compile is INVALID, not a kill;
* the source is restored byte-identical in a `finally`, a lock file in the
  build directory refuses overlapping runs, and the touched targets are
  rebuilt once so the build tree ends up with the original binaries;
* `--only SUBSTR` runs a subset by label.

Reference result: **16/16 killed, 0 survived, 0 stale, 0 invalid**.
