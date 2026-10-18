# Repository guide for AI agents

## Scope first

- C++/Qt application code is in `src/`; CMake tests are in `tests/`.
- The Python service is isolated in `AIAssistant/`.
- The YOLO training/export pipeline lives in `AIComputerVision/` (`cvtrain/`
  package). Exported `Models/*.onnx` files are Git LFS artifacts and
  `Models/manifest.json` records their provenance; keep the file names in sync
  with `src/core/AppConstants.h`.
- Treat `build*/`, `.vs/`, `AIAssistant/Cache/`, `AIAssistant/logs/`, and
  `AIComputerVision/runs/` as generated output. Never inspect or edit them
  unless a task explicitly concerns a generated artifact.
- Read the smallest relevant file set. Use `rg` before opening broad folders.

## Qt kit vs vcpkg Qt (load-failure footgun)

The runtime PATH loads the local Qt kit (`C:/Qt/6.9.3/msvc2022_64/bin`)
while plain `cmake -S . -B build` resolves `find_package(Qt6 ...)` to the
vcpkg tree, which carries a *different Qt version*. Binaries built against
vcpkg Qt then fail at load time with `0xc0000139`
(STATUS_ENTRYPOINT_NOT_FOUND) or exit code 127 (ERROR_PROC_NOT_FOUND).
Always configure from Qt Creator (which sets `QT_QMAKE_EXECUTABLE`) or pass
`-DQT_QMAKE_EXECUTABLE=C:/Qt/6.9.3/msvc2022_64/bin/qmake.exe` explicitly so
every target — exe, `AppCore.dll`, plugins, tests — uses the kit Qt.
CI is unaffected: no Qt Creator there, so the vcpkg Qt stays correct.

## Offline builds (no network at configure time)

Once dependencies are provisioned (Qt kit, the vcpkg `installed` tree and a
pre-extracted ONNX Runtime) the configure/build/test loop needs **no network** —
verified end-to-end behind a dead proxy. Two knobs make that work:

- `-DVCPKG_MANIFEST_INSTALL=OFF` reuses the existing `VCPKG_INSTALLED_DIR` tree
  instead of running vcpkg's manifest install plan (which needs the internet for
  `libaec`; CI handles that with a GitHub-mirror overlay port).
- `-DFETCHCONTENT_SOURCE_DIR_ONNXRUNTIME=<dir>` removes the **only**
  configure-time download in `CMakeLists.txt` (`FetchContent`, ~323 MB GPU zip).
  The directory must be an unzipped release with `include/` and `lib/` at its
  root: the versioned `onnxruntime-win-x64-gpu-<ver>/` wrapper from the zip has
  to be stripped (that is what `FetchContent` would have produced).

```powershell
cmake -B build -S . -G "Visual Studio 17 2022" `
  -DCMAKE_TOOLCHAIN_FILE=<vcpkg>/scripts/buildsystems/vcpkg.cmake `
  -DVCPKG_INSTALLED_DIR=<vcpkg>/installed -DVCPKG_MANIFEST_INSTALL=OFF `
  -DQT_QMAKE_EXECUTABLE=C:/Qt/6.9.3/msvc2022_64/bin/qmake.exe `
  -DBUILD_TESTING=ON `
  -DFETCHCONTENT_SOURCE_DIR_ONNXRUNTIME=<extracted onnxruntime-win-x64-gpu-<ver>>
```

Needs the MSVC developer environment (`vcvars64.bat`) or Qt Creator. CI
provisions the same directory once (`ONNXRUNTIME_DIR`), caches it, and reads the
version from `CMakeLists.txt`, so Configure never downloads it.

## Build layout (AppCore split)

`src/core`, `src/utils` and `src/services` compile into **`AppCore.dll`**
(the plugin-facing ABI: interfaces + kernel + services). The executable only
contains the `src/app` shell and imports from AppCore; plugins and tests link
`AppCore` instead of the exe (no circular link, no `ENABLE_EXPORTS`). Qt
plugins and translations are deployed next to the exe by POST_BUILD steps;
`AppCore.dll` is copied there too (the CI package step picks up
`build/Release/*.dll` wholesale).

Heavy third-party SDKs (VTK/OpenCV/PCL/Eigen/ONNX Runtime) are **PRIVATE** to
AppCore — they never propagate to plugin link lines. Two PCH targets encode
the heavy/light split: `SharedPCH` (`src/pch.h`: Qt + heavy SDK) for the exe,
AppCore and heavy plugins; `SharedPCHLight` (`src/pch_light.h`: Qt/STL only)
for light plugins. A plugin whose sources include VTK/OpenCV/PCL must declare
those includes/libs itself and reuse `SharedPCH`; every other plugin must
reuse `SharedPCHLight`. `tests/CheckArchitectureBoundaries.cmake` derives the
classification from the plugin sources, fails on drift, and also forbids
heavy SDK includes in `src/utils/**` (shared ABI surface — heavy code lives
in `src/services/*`, e.g. `scene/PanStyle`).

**AppCore stays a single DLL by design.** Splitting it physically into
AppContracts / AppPlatform / AppRuntime was evaluated and deliberately
deferred: the measurable defect (light plugins inheriting VTK/OpenCV/PCL/ONNX
headers and libs) is fixed by the PRIVATE heavy links + PCH split above,
without paying for extra DLL boundaries, export macros and PCH parity
problems. Revisit only if plugins from outside this repo must be supported.

## Plugin-facing service interfaces

Plugins talk to app services only through `src/core/I*.h` interfaces resolved
from the `ServiceRegistry` (`ctx->services()->get<T>()`), never through
concrete singletons or exe classes. Conventions:

- Plugin init order is resolved before any `initialize()` call by
  `PluginDependencyResolver` (compiled into AppCore): `dependencies()` are
  hard edges, `loadPhase()` is a hint over the canonical phases
  (`kernel < platform < default < ui < late`), then `loadOrder()` and
  directory-discovery order break ties. A missing dependency, duplicate
  name, unknown phase or dependency cycle aborts plugin loading with a
  concrete error. Plugin classes must not re-implement ordering logic.
- The load → resolve → initialize → cleanup lifecycle itself lives in
  `PluginHost` (AppCore), not in the exe shell: discovery filters by file
  name, a resolution failure drops every plugin, `initialize()` failure rolls
  back the already initialized plugins, and `shutdown()` cleans up in reverse
  init order. `IPlugin::apiVersion()` is the ABI gate — a plugin built against
  a different contract version is rejected instead of crashing.
  `tests/TestPluginHost.cpp` covers the contract with an injected loader;
  `tests/TestPluginSmoke.cpp` loads the real DLLs and needs the plugin
  dependencies on `PATH` (CI provides it).
- `IAppContext` no longer exposes the concrete `QMainWindow` (the deprecated
  `mainWindow()` was removed): shell interaction goes through
  `IShellService` (`shell()`), resolved from the context or registry.

- QObject-based interfaces (`ILanguageService`, `IReconstructionService`)
  derive **only** from `QObject` via the interface — concrete classes inherit
  the interface alone (no duplicate `QObject` base, no diamond). Signals live
  on the interface, not the impl. Export with `APP_EXPORT`.
- Plain interfaces (`IViewerService`, `ISettingsService`) need no signals.
- `ISceneService` is the documented exception that exposes VTK types — keep it
  to actor/renderer plumbing.
- Translation goes through `ILanguageService` / `IAppContext::translate()`;
  the `LM`/`LM_TR` macros are gone. `UserAuthPlugin::translate()` is a
  module-scoped facade over the registry-bound service.
- `UserManager::instance()` appears in exactly one production site: the
  AppShell registration (composition root). Everything else — plugins,
  dialogs, AppCore services — resolves `IUserManager` from the registry
  (ctor-injected where the component has no context).
- License keys are stored DPAPI-encrypted (`SecretVault`, `enc1:`) with
  SHA-256-hashed INI path buckets; legacy plaintext entries migrate on load.

## Blocking I/O contracts

- `IMailService` (and the mail stack under it) is synchronous and blocking:
  callers must run it off the GUI thread via `QtConcurrent` (pattern:
  MailDockWidget, MailSettingsDialog, ForgotPasswordDialog).
  `mail::enforceBackgroundThread()` warns in every build and asserts in debug
  builds when a blocking entry point is invoked on the GUI thread.
- Teardown paths bound every process wait with an explicit timeout
  (`waitForFinished(<ms>)`; the argument-less call waits Qt's 30 s default and
  freezes shutdown). The architecture guard rejects new un-timed calls.

## Desktop agent actions and chat sessions

- Demo fixtures for desktop-agent actions come from
  `Config/agent_action_manifest.json` → top-level `sample_data`
  (`AgentActionManifest::sampleData` / `applySampleDefaults`). The AI service
  fills `path` / `folder` parameters before emitting
  `SignalBus::agentUiActionRequested`; plugins must consume those parameters
  and never hard-code demo paths. `AIAssistant/tests/**` only requires the
  `actions` array, extra top-level keys are safe.
- Chat sessions are owned by `ChatSessionManager` (session list, current
  pointer, attachment/thumbnail cleanup on `deleteSession`); persistence stays
  in `ChatSessionStore` (username + config path injected — no singleton).
  `AIAssistant` keeps network/process/queue concerns only.
- Password hashes never leave `UserManager` (`m_passwordHashes`); `UserInfo`
  is profile-only, so `updateUser()` cannot clobber credentials. Tests observe
  the hash through the INI (`User_<name>/password_hash`).
- `src/core/AppManifest.json` is an authored, tracked contract file (read by
  `AIComputerVision/tests/test_contract.py`); it is not generated by CMake.

## Required checks

- C++ changes: configure/build the affected target; run the relevant CTest
  tests when dependencies are available. `tests/CMakeLists.txt` forces
  `QT_ASSUME_STDERR_HAS_CONSOLE=1` for every test so QtTest output (which goes
  to stderr) survives `ctest --output-on-failure`; without it Windows drops
  the report and failures look empty.
- Guard-layer C++ changes (`src/core/Plugin*`, `SecretVault`, `UserManager`,
  `AppShell`, chat-session/settings/shell services): run
  `python tests/coverage_gate.py` (measured floors; Release must ship PDBs —
  CMakeLists.txt section 5b) and `python tests/mutation_check.py` (inverts one
  decision per guard; the owning test must fail). Both need Qt + vcpkg `bin` on
  `PATH` and are enforced in the C++ workflow after the test step. See
  `tests/README.md` for the floor table and the coverage/mutation split for
  header-only guards.
- Python changes: from `AIAssistant/`, run `ruff check .` and
  `python -m compileall -q evals src` (`StartChatbotServer.py` was removed with
  the V6 refactor; the check referenced a file that no longer exists).
- AIComputerVision changes: from `AIComputerVision/`, run `ruff check .`,
  `python -m mypy`, then `coverage run -m unittest discover -s tests` and
  `coverage report` (the coverage gate is 100% statements + branches).
- Workflow changes: keep `workflow_dispatch`, path filters, and a finite job
  timeout. Do not cancel long dependency-bootstrap jobs. Keep the ONNX Runtime
  provision/cache steps (`ONNXRUNTIME_DIR`) and their
  `-DFETCHCONTENT_SOURCE_DIR_ONNXRUNTIME` configure flag in both C++ workflows —
  they are what keeps Configure from downloading the 323 MB zip on every run.

## Change discipline

- Keep C++ and Python changes independent unless the interface requires both.
- Do not commit model weights outside Git LFS, caches, logs, build trees, IDE
  state, or secrets.
- Prefer one focused change plus a verification result over broad rewrites.
