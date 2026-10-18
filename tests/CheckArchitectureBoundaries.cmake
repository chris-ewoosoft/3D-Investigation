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
#      IUserManager từ ServiceRegistry.
#   4. Plugins must not call process singletons (AppConfig::instance /
#      LanguageManager::instance) — resolve IAppPaths / ILanguageService from
#      the ServiceRegistry instead.
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

# ── 4. Plugins must not call process singletons ───────────────────────────────
foreach(source IN LISTS PLUGIN_SOURCES)
    file(READ "${source}" content)
    if(content MATCHES "(AppConfig|LanguageManager)::instance[ \t]*\\(\\)")
        file(RELATIVE_PATH rel "${SOURCE_DIR}" "${source}")
        list(APPEND VIOLATIONS
            "${rel}: gọi singleton ::instance() — plugin phải resolve service qua ServiceRegistry")
    endif()
endforeach()

if(VIOLATIONS)
    list(JOIN VIOLATIONS "\n  - " report)
    message(FATAL_ERROR "Architecture boundary violations:\n  - ${report}")
endif()

message(STATUS "Architecture boundaries OK")
