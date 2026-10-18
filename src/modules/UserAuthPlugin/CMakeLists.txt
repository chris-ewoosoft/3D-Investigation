

add_library(UserAuthPlugin SHARED
    UserAuthPlugin.h
    UserAuthPlugin.cpp
    UserAuthPlugin.qrc
    LoginDialog.h
    LoginDialog.cpp
    ForgotPasswordDialog.h
    ForgotPasswordDialog.cpp
    ChangePasswordDialog.h
    ChangePasswordDialog.cpp
    LicenseActivationDialog.h
    LicenseActivationDialog.cpp
    AdminUserManagerDialog.h
    AdminUserManagerDialog.cpp
    AboutDialog.h
    AboutDialog.cpp
    AvatarCropperDialog.h
    AvatarCropperDialog.cpp
    ThemeSelectionDialog.h
    ThemeSelectionDialog.cpp
    SettingsDialog.h
    SettingsDialog.cpp
)

target_include_directories(UserAuthPlugin PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${PROJECT_SOURCE_DIR}/src/core
    ${PROJECT_SOURCE_DIR}/src/utils
    ${VTK_INCLUDE_DIRS}
)

target_precompile_headers(UserAuthPlugin REUSE_FROM SharedPCH)
target_link_libraries(UserAuthPlugin PRIVATE SharedPCH)

target_link_libraries(UserAuthPlugin
    PRIVATE
        Qt6::Core
        Qt6::Widgets
        Qt6::Network
        Qt6::Concurrent
        ${VTK_LIBRARIES}
        AppCore
)

set_target_properties(UserAuthPlugin PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/plugins"
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/plugins"
)
