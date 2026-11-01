# Build performance

What makes this project slow to build, what has been fixed, and how to keep the
edit → run loop short. All numbers were measured on the development machine
(Ryzen 5 7235HS, 4 physical / 8 logical cores, Ninja single-config, Release,
Qt 6.9.3 + MSVC 14.42); absolute values vary, the ratios are the point.

## TL;DR

- A normal edit is **seconds**: 7.6 s for a light service, ~73 s for the
  heaviest AI translation unit, plus 2-4 s of linking. There is no longer a
  reason for a "Run" to rebuild the whole project.
- **Only a real interface change** (a header under `src/include/` or
  `src/utils/`) recompiles every target. That is the plugin ABI gate doing its
  job, not a build-system bug.
- The 4.1 GB runtime deploy (ONNX Runtime + TensorRT + builder resources) is a
  **stat-only** check now: unchanged files cost no I/O.

## What used to cost minutes

### 1. The generated ABI header was rewritten on every check

`cmake/GenerateInterfaceAbi.cmake` ran whenever a header of the plugin-facing
surface was touched and called `file(WRITE)` unconditionally. `file(WRITE)`
always updates the timestamp, and `src/include/IPlugin.h` includes
`generated/InterfaceAbiHash.h`, so **every target — AppCore, the executable,
all six plugins, their AUTOMOC runs — recompiled**, even when the hash value
was identical (a comment edit, a formatter run, a `git checkout` touching the
mtime).

Measured: the unchanged header cost a full ~2.5 min rebuild.

The generator now compares the new content with the existing file and only
writes when it differs (it prints `(unchanged)` / `(regenerated)`). Ninja's
`restat` then keeps the whole graph clean: `ninja -n` reports
`no work to do`. `tests/CheckInterfaceAbiIncremental.cmake` pins both
directions (unchanged → no write, tampered → repaired).

### 2. `copy_if_different` read 4.1 GB after every relink

The exe's POST_BUILD steps deployed `onnxruntime_providers_cuda.dll` (673 MB),
`nvinfer_builder_resource_*_10.dll` (2.9 GB) and friends with
`cmake -E copy_if_different`, which **reads the destination to compare it**
before deciding to copy. Measured: 4.5 s for a single identical 414 MB DLL, and
45.9 s for the whole exe edge.

`cmake/DeployRuntime.cmake` answers the same question with `file(SIZE)` and
`file(TIMESTAMP)`:
missing → copy, size differs → copy, source newer → copy, otherwise skip.
The exe edge dropped to **3.3 s** (link + deploy), and a rebuild that changes
only AppCore does not relink the plugins at all.

### 3. Unity batch size

`CMAKE_UNITY_BUILD_BATCH_SIZE` is 4 (was 8). The heavy AI and SfM sources are
the critical path either way (one AI translation unit is 70-90 s), but the
smaller batches also shrink the unit that carries `mocs_compilation.cpp`, which
AUTOMOC rewrites whenever a `Q_OBJECT` source changes — that unit is rebuilt
together with the edited one. Back-to-back measurements: clean build 202 s (4)
vs 215 s (8); editing `AIProcessor.cpp` 73 s (4) vs 102 s (8). The same
configuration later measured 156 s for a clean build under lighter load.

The machine was also running other builds during some of these measurements,
so treat the absolute values as indicative; the direction was consistent.

## Dev recipes

### Fast build without symbols

```powershell
cmake -S . -B build/fast -G Ninja -DCMAKE_BUILD_TYPE=Release `
  -DCMAKE_TOOLCHAIN_FILE=<vcpkg>/scripts/buildsystems/vcpkg.cmake `
  -DVCPKG_INSTALLED_DIR=<vcpkg>/installed -DVCPKG_MANIFEST_INSTALL=OFF `
  -DQT_QMAKE_EXECUTABLE=C:/Qt/6.9.3/msvc2022_64/bin/qmake.exe `
  -DENABLE_RELEASE_PDB=OFF
```

`ENABLE_RELEASE_PDB=OFF` removes `/Zi` from Release/RelWithDebInfo and `/DEBUG`
from the linker (the option defaults to ON). It skips the compile-time PDB
writes and the link-time PDB merge, and produces no `.pdb` — so **never use it
for the build that `python tests/coverage_gate.py` measures**, which maps
addresses through the PDB.

### Defender

Real-time protection scans the thousands of `.obj`/`.pdb` files each build.
If you have administrator rights, exclude the repository and the compiler/sdk
locations:

```powershell
Add-MpPreference -ExclusionPath "F:\PROJECTS\QT\3D-Reconstruction"
Add-MpPreference -ExclusionPath "C:\Qt"
Add-MpPreference -ExclusionPath "C:\Program Files\Microsoft Visual Studio"
```

Only do this on a development machine, and only for the directories you build
in — an exclusion lowers the protection for everything below it.

### Disk

A build tree carries its own copy of the runtime DLLs (~4.5 GB) plus objects
and PDBs. The three trees on this machine measure:

| tree           | size  |
|----------------|-------|
| `build`        | 14 GB |
| `build_arch`   | 6.2 GB|
| `build_review` | 7.5 GB|

Delete stale trees you no longer use (they are generated output); do not keep
three copies just in case. `build/` and `build_*` are never committed.

## Verifying the build is not doing extra work

```powershell
# must print "ninja: no work to do" when nothing changed
# (run from Qt Creator's build environment, or after vcvars64.bat)
cmake --build build/Desktop/Release --target all

# the ABI guards (also run by ctest -R InterfaceAbi when BUILD_TESTING=ON)
cmake -DSOURCE_DIR=. `
      -DINTERFACE_ABI_HEADER=build/Desktop/Release/generated/InterfaceAbiHash.h `
      -P tests/CheckInterfaceAbi.cmake
cmake -DSOURCE_DIR=. -DWORK_DIR=build/abi-incremental `
      -P tests/CheckInterfaceAbiIncremental.cmake
```

## Rules for future changes

- Do not go back to `file(WRITE)` in `GenerateInterfaceAbi.cmake`: an unchanged
  hash must not touch the header.
- Do not use `cmake -E copy_if_different` for the runtime DLL set; route new
  groups through `cmake/DeployRuntime.cmake` (it also keeps the destination
  timestamps stable).
- Changing a plugin-facing header on purpose still rebuilds everything — that
  is the ABI gate, and the correct behaviour.
- A configure is not free (~30-80 s: PCL/VTK discovery plus the timestamp
  scan). Qt Creator re-runs it when `CMakeLists.txt` or a `*.cmake` module
  changes; avoid editing build files during a debug session.
