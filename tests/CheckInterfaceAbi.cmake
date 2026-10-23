# ── Guard: the plugin-facing interface ABI hash must be honest ───────────────
#
# Two properties are checked:
#
#   1. FRESHNESS   — the generated header must equal a fresh computation over
#                    the current files. A stale generated header would let a
#                    plugin built against different interfaces load.
#   2. SENSITIVITY — changing the content of an interface header must change
#                    the hash. A hash that ignores the content would defeat
#                    the whole mechanism.
#
# Usage:
#   cmake -DSOURCE_DIR=<repo> -DINTERFACE_ABI_HEADER=<generated header>
#         -P tests/CheckInterfaceAbi.cmake

if(NOT DEFINED SOURCE_DIR OR NOT DEFINED INTERFACE_ABI_HEADER)
    message(FATAL_ERROR "CheckInterfaceAbi.cmake needs SOURCE_DIR and INTERFACE_ABI_HEADER")
endif()

set(APP_REPO_ROOT "${SOURCE_DIR}")
include("${SOURCE_DIR}/cmake/PluginInterface.cmake")
include("${SOURCE_DIR}/cmake/InterfaceAbi.cmake")

collect_interface_surface(_surface APP_INTERFACE_ROOTS APP_INTERFACE_SEARCH_DIRS)
if(NOT _surface)
    message(FATAL_ERROR "the plugin-facing interface surface is empty — nothing to hash")
endif()
compute_interface_abi_hash(_expected ${_surface})

if(NOT EXISTS "${INTERFACE_ABI_HEADER}")
    message(FATAL_ERROR
        "the generated interface ABI header is missing: ${INTERFACE_ABI_HEADER}")
endif()
file(READ "${INTERFACE_ABI_HEADER}" _generated)
string(REGEX MATCH "APP_INTERFACE_ABI_HASH (0x[0-9a-f]+)ULL" _match "${_generated}")
if(NOT _match)
    message(FATAL_ERROR
        "the generated interface ABI header has no APP_INTERFACE_ABI_HASH: "
        "${INTERFACE_ABI_HEADER}")
endif()
set(_actual "${CMAKE_MATCH_1}")
if(NOT _actual STREQUAL _expected)
    message(FATAL_ERROR
        "the interface ABI hash is STALE: generated ${_actual}, "
        "the current interface surface hashes to ${_expected}")
endif()

# ── Sensitivity: perturb one interface header, the hash must change ─────────
set(_scratch "${CMAKE_CURRENT_BINARY_DIR}/interface_abi_sensitivity")
file(REMOVE_RECURSE "${_scratch}")
file(MAKE_DIRECTORY "${_scratch}")

# Copy the surface, preserving the file names (the hash uses the name).
foreach(_file IN LISTS _surface)
    get_filename_component(_name "${_file}" NAME)
    configure_file("${_file}" "${_scratch}/${_name}" COPYONLY)
endforeach()
file(GLOB _perturbed_files "${_scratch}/*")
list(SORT _perturbed_files)
if(NOT _perturbed_files)
    message(FATAL_ERROR "could not copy the interface surface for the sensitivity check")
endif()
list(GET _perturbed_files 0 _victim)
file(READ "${_victim}" _victim_content)
file(WRITE "${_victim}" "${_victim_content}\n// perturbed by CheckInterfaceAbi\n")
compute_interface_abi_hash(_perturbed ${_perturbed_files})
if(_perturbed STREQUAL _expected)
    message(FATAL_ERROR
        "the interface ABI hash does NOT change when an interface header "
        "changes — the hash ignores the content")
endif()

list(LENGTH _surface _surface_size)
message(STATUS "Interface ABI OK: ${_expected} over ${_surface_size} file(s)")
