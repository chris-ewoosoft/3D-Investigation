# ── Plugin-facing interface surface ──────────────────────────────────────────
#
# ONE definition of "what a plugin is compiled against", shared by:
#   * CMakeLists.txt          → generates the ABI hash header
#   * tests/CheckInterfaceAbi.cmake → guards that the generated hash is fresh
#
# The caller must define APP_REPO_ROOT before including this module.
#
# Since the split of src/core, the public surface IS two directories:
#   * src/include  — the plugin-facing headers (the I*.h interfaces + the
#                    allow-listed core glue + the DTOs they expose)
#   * src/utils    — the shared utility layer
# Everything else (src/core, src/services/**) is PRIVATE to AppCore: a plugin
# cannot even resolve a concrete header any more.

if(NOT DEFINED APP_REPO_ROOT)
    message(FATAL_ERROR "PluginInterface.cmake needs APP_REPO_ROOT to be set first")
endif()

# Where a quoted #include of the plugin-facing surface can be resolved.
set(APP_INTERFACE_SEARCH_DIRS
    "${APP_REPO_ROOT}/src/include"
    "${APP_REPO_ROOT}/src/utils"
    "${APP_REPO_ROOT}/src/services/ai")

# Roots of the surface: every header of the public directories. The transitive
# quoted-include closure of these roots is what a plugin really sees.
file(GLOB _app_interface_headers "${APP_REPO_ROOT}/src/include/*.h")
file(GLOB _app_interface_utils "${APP_REPO_ROOT}/src/utils/*.h")
set(APP_INTERFACE_ROOTS ${_app_interface_headers} ${_app_interface_utils})
list(REMOVE_DUPLICATES APP_INTERFACE_ROOTS)
