target_sources(3D-Reconstruction PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/Main.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/MainWindow.h
    ${CMAKE_CURRENT_SOURCE_DIR}/MainWindow.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/MainWindow.ui
    ${CMAKE_CURRENT_SOURCE_DIR}/AppShell.h
    ${CMAKE_CURRENT_SOURCE_DIR}/AppShell.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/resources.qrc
)

if(WIN32)
    target_sources(3D-Reconstruction PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/app.rc)
endif()
