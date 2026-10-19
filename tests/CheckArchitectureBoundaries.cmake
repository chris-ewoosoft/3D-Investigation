# ──────────────────────────────────────────────────────────────────────────────
# Architecture boundary guard — encodes the invariants from AGENTS.md so a
# refactor that forgets them fails CI instead of silently eroding the plugin ABI:
#
#   1. Plugin-facing surface = core interfaces (I*.h) + shared non-interface
#      core glue (Global/AppConstants/AIMode/SignalBus/AgentActionManifest/
#      ServiceRegistry) + shared UI utilities (src/utils/**) + the plugin's
#      own headers. Includes of concrete service headers (SceneService,
#      ShellService, …), app-shell headers (AppShell, MainWindow, …), or core
#      singleton headers (UserManager, AppConfig, SecretVault, …) are
#      boundary violations. Forbidden names are derived from the source tree
#      itself — there is no hand-maintained name list to drift out of sync.
#   2. Services must not reach up into the app shell (src/app/**).
#   3. UserManager::instance() chỉ được gọi ở composition root (AppShell.cpp)
#      và trong chính UserManager.{h,cpp}; mọi nơi khác phải resolve
#      IUserManager từ ServiceRegistry. AppConfig::bootstrap() tương tự: chỉ
#      Main.cpp / AppShell.cpp / Logger.cpp (bootstrap) và chính AppConfig.
#   4. Plugins must not call process singletons (AppConfig::instance /
#      AppConfig::bootstrap) — resolve IAppPaths from the ServiceRegistry
#      instead. LanguageManager không còn singleton; ILanguageService được
#      resolve từ registry.
#   5. Plugin nặng/nhẹ phải khớp với source của chính nó: plugin có include
#      VTK/OpenCV/PCL phải dùng SharedPCH + tự khai báo heavy libs; plugin
#      còn lại phải dùng SharedPCHLight và không được tham chiếu heavy SDK —
#      nếu không, "plugin nhỏ không kéo dependency nặng" lại bị phá vỡ trong
#      im lặng.
#   6. src/utils/** là bề mặt ABI dùng chung cho plugin nhẹ → không được
#      include heavy SDK (VTK/OpenCV/PCL). Code heavy thuộc src/services/*.
#   7. Teardown phải có biên: `waitForFinished()` trần (mặc định Qt chờ 30 s)
#      bị cấm trong src/** — luôn truyền timeout tường minh.
#
# Chạy: cmake -DSOURCE_DIR=<repo-root> -P tests/CheckArchitectureBoundaries.cmake
# ──────────────────────────────────────────────────────────────────────────────

if(NOT DEFINED SOURCE_DIR)
    message(FATAL_ERROR "SOURCE_DIR is required (repo root)")
endif()

set(VIOLATIONS "")

# ── Name sets derived from the source tree (single source of truth) ───────────
file(GLOB_RECURSE SERVICE_HEADERS "${SOURCE_DIR}/src/services/*.h")
file(GLOB_RECURSE APP_HEADERS     "${SOURCE_DIR}/src/app/*.h")
file(GLOB_RECURSE CORE_HEADERS    "${SOURCE_DIR}/src/core/*.h")
file(GLOB_RECURSE UTILS_HEADERS   "${SOURCE_DIR}/src/utils/*.h")

set(APP_BASENAMES "")
foreach(h IN LISTS APP_HEADERS)
    get_filename_component(base "${h}" NAME)
    list(APPEND APP_BASENAMES "${base}")
endforeach()

# Forbidden for plugins: every service/app header, plus core headers that are
# NOT plugin-facing interfaces (I*.h).
set(FORBIDDEN_HEADERS "")
foreach(h IN LISTS SERVICE_HEADERS APP_HEADERS)
    get_filename_component(base "${h}" NAME)
    list(APPEND FORBIDDEN_HEADERS "${base}")
endforeach()
foreach(h IN LISTS CORE_HEADERS)
    get_filename_component(base "${h}" NAME)
    if(NOT base MATCHES "^I[A-Z].*\\.h$")
        list(APPEND FORBIDDEN_HEADERS "${base}")
    endif()
endforeach()
list(REMOVE_DUPLICATES FORBIDDEN_HEADERS)

# Allowed shared core glue: read-only constants, value types, and the
# registry/signal plumbing that IS part of the plugin-facing ABI.
set(CORE_ALLOWLIST
    Global.h
    AppConstants.h
    AIMode.h
    SignalBus.h
    AgentActionManifest.h
    ServiceRegistry.h)

set(UTILS_BASENAMES "")
foreach(h IN LISTS UTILS_HEADERS)
    get_filename_component(base "${h}" NAME)
    list(APPEND UTILS_BASENAMES "${base}")
endforeach()

file(GLOB_RECURSE PLUGIN_SOURCES
    "${SOURCE_DIR}/src/modules/*.cpp"
    "${SOURCE_DIR}/src/modules/*.h")

# ── 1. Plugin → forbidden include ─────────────────────────────────────────────
foreach(source IN LISTS PLUGIN_SOURCES)
    file(READ "${source}" content)
    string(REGEX MATCHALL "#[ \t]*include[ \t]+[\"<][^\">]+[\">]" includes "${content}")
    foreach(inc IN LISTS includes)
        # System / third-party includes are not our boundary.
        if(inc MATCHES "#[ \t]*include[ \t]+<")
            continue()
        endif()
        string(REGEX REPLACE ".*[\"<]([^\">]+)[\">].*" "\\1" path "${inc}")
        get_filename_component(base "${path}" NAME)

        if(base MATCHES "^I[A-Z].*\\.h$")
            continue()   # core interface — the plugin-facing ABI
        endif()
        if(base IN_LIST CORE_ALLOWLIST)
            continue()
        endif()
        if(base IN_LIST UTILS_BASENAMES)
            continue()   # shared UI utility
        endif()
        if(base IN_LIST FORBIDDEN_HEADERS)
            file(RELATIVE_PATH rel "${SOURCE_DIR}" "${source}")
            list(APPEND VIOLATIONS
                "${rel}: include \"${path}\" — plugin chỉ được include I*.h, header shared (AppConstants/SignalBus/ServiceRegistry/…), utils dùng chung, hoặc header nội bộ của plugin")
        endif()
        # Anything else = plugin-local header — allowed by the principle.
    endforeach()
endforeach()

# ── 2. Service → app-shell include ────────────────────────────────────────────
file(GLOB_RECURSE SERVICE_SOURCES
    "${SOURCE_DIR}/src/services/*.cpp"
    "${SOURCE_DIR}/src/services/*.h")

foreach(source IN LISTS SERVICE_SOURCES)
    file(READ "${source}" content)
    string(REGEX MATCHALL "#[ \t]*include[ \t]+[\"<][^\">]+[\">]" includes "${content}")
    foreach(inc IN LISTS includes)
        if(inc MATCHES "#[ \t]*include[ \t]+<")
            continue()
        endif()
        string(REGEX REPLACE ".*[\"<]([^\">]+)[\">].*" "\\1" path "${inc}")
        get_filename_component(base "${path}" NAME)
        if(base IN_LIST APP_BASENAMES)
            file(RELATIVE_PATH rel "${SOURCE_DIR}" "${source}")
            list(APPEND VIOLATIONS
                "${rel}: service include \"${base}.h\" — services không được phụ thuộc src/app (lục đục nguồn services → app)")
        endif()
    endforeach()
endforeach()

# ── 3. UserManager::instance() chỉ ở composition root ─────────────────────────
file(GLOB_RECURSE ALL_SOURCES
    "${SOURCE_DIR}/src/*.cpp"
    "${SOURCE_DIR}/src/*.h")

foreach(source IN LISTS ALL_SOURCES)
    get_filename_component(name "${source}" NAME)
    if(name STREQUAL "UserManager.cpp"
       OR name STREQUAL "UserManager.h"
       OR name STREQUAL "AppShell.cpp")
        continue()
    endif()

    file(READ "${source}" content)
    if(content MATCHES "UserManager::instance[ \t]*\\(\\)")
        file(RELATIVE_PATH rel "${SOURCE_DIR}" "${source}")
        list(APPEND VIOLATIONS
            "${rel}: gọi UserManager::instance() ngoài composition root")
    endif()
endforeach()

# ── 3b. AppConfig::bootstrap() chỉ ở bootstrap/composition root ───────────────
foreach(source IN LISTS ALL_SOURCES)
    get_filename_component(name "${source}" NAME)
    if(name STREQUAL "AppConfig.cpp"
       OR name STREQUAL "AppConfig.h"
       OR name STREQUAL "Main.cpp"
       OR name STREQUAL "AppShell.cpp"
       OR name STREQUAL "Logger.cpp")
        continue()
    endif()

    file(READ "${source}" content)
    if(content MATCHES "AppConfig::(bootstrap|instance)[ \t]*\\(\\)")
        file(RELATIVE_PATH rel "${SOURCE_DIR}" "${source}")
        list(APPEND VIOLATIONS
            "${rel}: gọi AppConfig::bootstrap()/instance() ngoài bootstrap — resolve IAppPaths")
    endif()
endforeach()

# ── 4. Plugins must not call process singletons ───────────────────────────────
foreach(source IN LISTS PLUGIN_SOURCES)
    file(READ "${source}" content)
    if(content MATCHES "(AppConfig|LanguageManager)::(instance|bootstrap)[ \t]*\\(\\)")
        file(RELATIVE_PATH rel "${SOURCE_DIR}" "${source}")
        list(APPEND VIOLATIONS
            "${rel}: gọi singleton ::instance()/::bootstrap() — plugin phải resolve service qua ServiceRegistry")
    endif()
endforeach()

# ── 5. Plugin nặng/nhẹ: PCH + third-party link phải khớp với source ───────────
# "Nặng" suy ra từ source (include trực tiếp VTK/OpenCV/PCL) — không có danh
# sách tay để lệch pha. Nặng → SharedPCH; nhẹ → SharedPCHLight và không được
# tham chiếu heavy SDK trong CMakeLists.
set(HEAVY_PLUGIN_NAMES "")
foreach(source IN LISTS PLUGIN_SOURCES)
    file(READ "${source}" content)
    if(content MATCHES "#[ \t]*include[ \t]+[<\"](opencv2/|pcl/|vtk|QVTK)")
        file(RELATIVE_PATH rel "${SOURCE_DIR}/src/modules" "${source}")
        string(REGEX REPLACE "/.*" "" plugin "${rel}")
        list(APPEND HEAVY_PLUGIN_NAMES "${plugin}")
    endif()
endforeach()
list(REMOVE_DUPLICATES HEAVY_PLUGIN_NAMES)

file(GLOB PLUGIN_CMAKELISTS "${SOURCE_DIR}/src/modules/*/CMakeLists.txt")
foreach(cml IN LISTS PLUGIN_CMAKELISTS)
    get_filename_component(plugin_dir "${cml}" DIRECTORY)
    get_filename_component(plugin "${plugin_dir}" NAME)
    file(READ "${cml}" content)
    file(RELATIVE_PATH rel "${SOURCE_DIR}" "${cml}")

    list(FIND HEAVY_PLUGIN_NAMES "${plugin}" heavy_idx)
    if(heavy_idx GREATER -1)
        if(content MATCHES "SharedPCHLight")
            list(APPEND VIOLATIONS
                "${rel}: plugin nặng (source include VTK/OpenCV/PCL) nhưng build với SharedPCHLight — phải REUSE_FROM SharedPCH")
        endif()
    else()
        if(content MATCHES "(VTK_LIBRARIES|VTK_INCLUDE_DIRS|OpenCV_LIBS|OpenCV_INCLUDE_DIRS|PCL_LIBRARIES|PCL_INCLUDE_DIRS|onnxruntime|SharedPCH[^A-Za-z])")
            list(APPEND VIOLATIONS
                "${rel}: plugin nhẹ tham chiếu heavy SDK/SharedPCH — phải dùng SharedPCHLight và không kéo VTK/OpenCV/PCL/ONNX")
        endif()
    endif()

    if(NOT content MATCHES "AppCore")
        list(APPEND VIOLATIONS
            "${rel}: plugin phải link AppCore (ABI chung interface + utils)")
    endif()
endforeach()

# ── 6. src/utils là bề mặt ABI dùng chung → không include heavy SDK ───────────
foreach(h IN LISTS UTILS_HEADERS)
    file(READ "${h}" content)
    if(content MATCHES "#[ \t]*include[ \t]+[<\"](opencv2/|pcl/|vtk|QVTK)")
        file(RELATIVE_PATH rel "${SOURCE_DIR}" "${h}")
        list(APPEND VIOLATIONS
            "${rel}: utils header include heavy SDK — plugin nhẹ include utils sẽ kéo theo; chuyển sang src/services/*")
    endif()
endforeach()

# ── 7. Teardown có biên: không waitForFinished() trần ────────────────────────
# Bản không tham số chờ mặc định 30 s của Qt — treo shutdown nếu process con
# "cứng đầu"; guard chặn phát sinh mới.
file(GLOB_RECURSE ALL_CXX_SOURCES "${SOURCE_DIR}/src/*.cpp" "${SOURCE_DIR}/src/*.h")
foreach(source IN LISTS ALL_CXX_SOURCES)
    file(READ "${source}" content)
    if(content MATCHES "waitForFinished[ \t]*\\([ \t]*\\)")
        file(RELATIVE_PATH rel "${SOURCE_DIR}" "${source}")
        list(APPEND VIOLATIONS
            "${rel}: waitForFinished() không truyền timeout — luôn dùng waitForFinished(<ms>) (mặc định Qt 30 s, treo teardown)")
    endif()
endforeach()

if(VIOLATIONS)
    list(JOIN VIOLATIONS "\n  - " report)
    message(FATAL_ERROR "Architecture boundary violations:\n  - ${report}")
endif()

message(STATUS "Architecture boundaries OK")
