# ── Deploy runtime DLLs only when they actually changed ──────────────────────
# `cmake -E copy_if_different` reads the whole destination file before it can
# decide whether a copy is needed. The local runtime set (ONNX Runtime +
# TensorRT + its builder resources) is ~4.1 GB, so that check alone cost ~45 s
# after every relink of the executable (measured on this repo: one 414 MB DLL
# that was already identical still took 4.5 s to "compare").
#
# This script answers the same question with two stat() calls per file:
#   * destination missing                  → copy
#   * size differs                         → copy
#   * source newer than the destination    → copy
# otherwise the file is left untouched, its timestamp is preserved and the
# executable directory stays stable for incremental runs.
#
# Inputs (from the command line):
#   DEST    — destination directory
#   SOURCES — list of source files

if(NOT DEFINED DEST OR NOT DEFINED SOURCES)
    message(FATAL_ERROR "DeployRuntime.cmake needs DEST and SOURCES")
endif()

set(_copied 0)
set(_skipped 0)
foreach(_src IN LISTS SOURCES)
    if(NOT EXISTS "${_src}")
        # The previous `cmake -E copy_if_different` failed the build here, and
        # it should: a missing runtime DLL is a crash at load time, not a
        # warning.
        message(FATAL_ERROR "DeployRuntime: source does not exist: ${_src}")
    endif()
    get_filename_component(_name "${_src}" NAME)
    set(_dst "${DEST}/${_name}")
    set(_needs_copy FALSE)
    if(NOT EXISTS "${_dst}")
        set(_needs_copy TRUE)
    else()
        file(SIZE "${_src}" _src_size)
        file(SIZE "${_dst}" _dst_size)
        if(NOT _src_size EQUAL _dst_size)
            set(_needs_copy TRUE)
        else()
            file(TIMESTAMP "${_src}" _src_time "%s")
            file(TIMESTAMP "${_dst}" _dst_time "%s")
            if(_src_time GREATER _dst_time)
                set(_needs_copy TRUE)
            endif()
        endif()
    endif()
    if(_needs_copy)
        # `file(COPY)` is NOT unconditional: it "optimizes out" a destination
        # whose timestamp is not older than the source (CMake docs), which can
        # silently swallow a copy after a same-second size change. `cmake -E
        # copy` always writes, and this branch is rare (first deploy or an SDK
        # update), so the extra process does not matter.
        execute_process(
            COMMAND "${CMAKE_COMMAND}" -E copy "${_src}" "${_dst}"
            RESULT_VARIABLE _copy_rc
            ERROR_VARIABLE _copy_err)
        if(NOT _copy_rc EQUAL 0)
            message(FATAL_ERROR
                "DeployRuntime: failed to copy ${_src} to ${_dst}: ${_copy_err}")
        endif()
        math(EXPR _copied "${_copied}+1")
    else()
        math(EXPR _skipped "${_skipped}+1")
    endif()
endforeach()

message(STATUS "DeployRuntime: ${_copied} copied, ${_skipped} up to date")
