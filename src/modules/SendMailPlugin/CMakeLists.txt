

add_library(SendMailPlugin SHARED
    SendMailPlugin.h
    SendMailPlugin.cpp
    SendMailRibbonUI.h
    SendMailRibbonUI.cpp
    MailFilterDialog.h
    MailFilterDialog.cpp
    MailInboxItemFactory.h
    MailInboxItemFactory.cpp
    MailMessageFormatter.h
    MailMessageFormatter.cpp
    MailSettingsDialog.h
    MailSettingsDialog.cpp
    MailDockWidget.h
    MailDockWidget.cpp
)

target_include_directories(SendMailPlugin PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${PROJECT_SOURCE_DIR}/src/core
    ${PROJECT_SOURCE_DIR}/src/utils
)

# Light PCH + light ABI: SendMailPlugin chỉ dùng Qt/OpenSSL, không parse hay
# link VTK/OpenCV/PCL (enforced by tests/CheckArchitectureBoundaries.cmake).
target_precompile_headers(SendMailPlugin REUSE_FROM SharedPCHLight)
target_link_libraries(SendMailPlugin PRIVATE SharedPCHLight)

target_link_libraries(SendMailPlugin
    PRIVATE
        Qt6::Core
        Qt6::Widgets
        Qt6::Network
        Qt6::Concurrent
        AppCore
)

set_target_properties(SendMailPlugin PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/plugins"
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/plugins"
)
