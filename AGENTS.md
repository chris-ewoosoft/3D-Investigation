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

## Build layout (AppCore split)

`src/core`, `src/utils` and `src/services` compile into **`AppCore.dll`**
(the plugin-facing ABI: interfaces + kernel + services). The executable only
contains the `src/app` shell and imports from AppCore; plugins and tests link
`AppCore` instead of the exe (no circular link, no `ENABLE_EXPORTS`). Qt
plugins and translations are deployed next to the exe by POST_BUILD steps;
`AppCore.dll` is copied there too (the CI package step picks up
`build/Release/*.dll` wholesale).

## Plugin-facing service interfaces

Plugins talk to app services only through `src/core/I*.h` interfaces resolved
from the `ServiceRegistry` (`ctx->services()->get<T>()`), never through
concrete singletons or exe classes. Conventions:

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

## Required checks

- C++ changes: configure/build the affected target; run the relevant CTest
  tests when dependencies are available.
- Python changes: from `AIAssistant/`, run `ruff check .` and
  `python -m compileall -q evals src` (`StartChatbotServer.py` was removed with
  the V6 refactor; the check referenced a file that no longer exists).
- AIComputerVision changes: from `AIComputerVision/`, run `ruff check .`,
  `python -m mypy`, then `coverage run -m unittest discover -s tests` and
  `coverage report` (the coverage gate is 100% statements + branches).
- Workflow changes: keep `workflow_dispatch`, path filters, and a finite job
  timeout. Do not cancel long dependency-bootstrap jobs.

## Change discipline

- Keep C++ and Python changes independent unless the interface requires both.
- Do not commit model weights outside Git LFS, caches, logs, build trees, IDE
  state, or secrets.
- Prefer one focused change plus a verification result over broad rewrites.
