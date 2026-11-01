# ── Guard: DeployRuntime.cmake must only copy when something changed ────────
#
# The runtime set (ONNX Runtime + TensorRT + builder resources) is ~4.1 GB.
# `cmake -E copy_if_different` reads the destination to answer "is it the
# same?", which cost ~45 s after every relink of the executable. The script
# under test answers it with stat() alone, so this guard pins the decision:
#
#   * missing destination        → copy
#   * same size, source older    → skip (the sentinel content must survive)
#   * same size, source newer    → copy
#   * different size             → copy
#
# Usage:
#   cmake -DSCRIPT=<repo>/cmake/DeployRuntime.cmake -DWORK_DIR=<scratch dir>
#         -P tests/CheckDeployRuntime.cmake

if(NOT DEFINED SCRIPT OR NOT DEFINED WORK_DIR)
    message(FATAL_ERROR "CheckDeployRuntime.cmake needs SCRIPT and WORK_DIR")
endif()

file(REMOVE_RECURSE "${WORK_DIR}")
file(MAKE_DIRECTORY "${WORK_DIR}/src")
set(_src "${WORK_DIR}/src/plugin.dll")
set(_dst_dir "${WORK_DIR}/dst")
set(_dst "${_dst_dir}/plugin.dll")

function(deploy)
    execute_process(
        COMMAND "${CMAKE_COMMAND}"
                "-DDEST=${_dst_dir}"
                "-DSOURCES=${_src}"
                -P "${SCRIPT}"
        RESULT_VARIABLE _rc
        OUTPUT_VARIABLE _out
        ERROR_VARIABLE _out)
    if(NOT _rc EQUAL 0)
        message(FATAL_ERROR "DeployRuntime.cmake failed:\n${_out}")
    endif()
    set(LAST_OUTPUT "${_out}" PARENT_SCOPE)
endfunction()

function(expect_content expected reason)
    file(READ "${_dst}" _actual)
    if(NOT _actual STREQUAL expected)
        message(FATAL_ERROR
            "${reason}: expected '${expected}' in the destination, got '${_actual}'")
    endif()
endfunction()

# 1. Missing destination → copied.
file(WRITE "${_src}" "AAAA")
deploy()
expect_content("AAAA" "a missing destination must be copied")
if(NOT LAST_OUTPUT MATCHES "1 copied")
    message(FATAL_ERROR "expected 1 copied, got:\n${LAST_OUTPUT}")
endif()

# 2. Same size, destination newer than the source → must NOT be copied.
#    A sentinel with the same size catches "it copied but the content happened
#    to match", and keeps the check independent of file timestamps.
file(WRITE "${_dst}" "BBBB")
deploy()
expect_content("BBBB" "an up-to-date destination must not be overwritten")
if(NOT LAST_OUTPUT MATCHES "0 copied")
    message(FATAL_ERROR "expected 0 copied, got:\n${LAST_OUTPUT}")
endif()

# 3. Same size, source newer → copied.
execute_process(COMMAND "${CMAKE_COMMAND}" -E sleep 1)
file(WRITE "${_src}" "CCCC")
deploy()
expect_content("CCCC" "a newer source must be copied")

# 4. Different size → copied, even without a newer timestamp.
file(WRITE "${_src}" "CCCCDD")
deploy()
expect_content("CCCCDD" "a size change must be copied")

# 5. Missing destination again → copied.
file(REMOVE "${_dst}")
deploy()
expect_content("CCCCDD" "a removed destination must be restored")

message(STATUS "DeployRuntime copy decisions OK")
