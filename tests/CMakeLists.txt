find_package(Qt6 REQUIRED COMPONENTS Test Core)
set(CMAKE_AUTOMOC ON)

# Architecture guard — plugin boundaries + UserManager singleton usage.
# Encodes the invariants documented in AGENTS.md; runs in milliseconds.
add_test(NAME TestArchitectureBoundaries
         COMMAND ${CMAKE_COMMAND}
                 "-DSOURCE_DIR=${CMAKE_SOURCE_DIR}"
                 -P "${CMAKE_CURRENT_SOURCE_DIR}/CheckArchitectureBoundaries.cmake")

# Test ServiceRegistry
qt_add_executable(TestServiceRegistry TestServiceRegistry.cpp)
target_include_directories(TestServiceRegistry PRIVATE ../src/core)
target_link_libraries(TestServiceRegistry PRIVATE Qt6::Test Qt6::Core)
add_test(NAME TestServiceRegistry COMMAND TestServiceRegistry)

# Test SignalBus
qt_add_executable(TestSignalBus TestSignalBus.cpp)
target_link_libraries(TestSignalBus PRIVATE Qt6::Test Qt6::Core AppCore)
add_test(NAME TestSignalBus COMMAND TestSignalBus)

# Test AppConfig
qt_add_executable(TestAppConfig TestAppConfig.cpp)
target_link_libraries(TestAppConfig PRIVATE Qt6::Test Qt6::Core AppCore)
add_test(NAME TestAppConfig COMMAND TestAppConfig)

# Test LanguageManager
qt_add_executable(TestLanguageManager TestLanguageManager.cpp)
target_link_libraries(TestLanguageManager PRIVATE Qt6::Test Qt6::Core AppCore)
add_test(NAME TestLanguageManager COMMAND TestLanguageManager)

# Test Mail MIME parser
qt_add_executable(TestMailMimeParser
    TestMailMimeParser.cpp
    ../src/services/mail/MailMimeParser.cpp
)
target_include_directories(TestMailMimeParser PRIVATE ../src/core ../src/services/mail)
target_link_libraries(TestMailMimeParser PRIVATE Qt6::Test Qt6::Core)
add_test(NAME TestMailMimeParser COMMAND TestMailMimeParser)

# Test Camera Params Parser
find_package(OpenCV REQUIRED COMPONENTS core)
qt_add_executable(TestCameraParamsParser
    TestCameraParamsParser.cpp
    ../src/services/reconstruction/CameraParamsParser.cpp
)
target_include_directories(TestCameraParamsParser PRIVATE ../src/services/reconstruction)
target_link_libraries(TestCameraParamsParser PRIVATE Qt6::Test Qt6::Core ${OpenCV_LIBS})
add_test(NAME TestCameraParamsParser COMMAND TestCameraParamsParser)

# Test PointCloudFilter
find_package(PCL REQUIRED COMPONENTS common filters io)
find_package(OpenCV REQUIRED COMPONENTS core imgproc features2d calib3d)

qt_add_executable(TestPointCloudFilter TestPointCloudFilter.cpp ../src/services/reconstruction/PointCloudFilter.cpp)
target_include_directories(TestPointCloudFilter PRIVATE 
    ../src/services/reconstruction
    ${PCL_INCLUDE_DIRS}
    ${OpenCV_INCLUDE_DIRS}
)
target_link_libraries(TestPointCloudFilter PRIVATE 
    Qt6::Test Qt6::Core Qt6::Widgets
    ${PCL_LIBRARIES}
    ${OpenCV_LIBS}
)
add_test(NAME TestPointCloudFilter COMMAND TestPointCloudFilter)

# Source dùng chung cho các test integration của reconstruction pipeline.
set(RECON_PIPELINE_SOURCES
    ../src/services/reconstruction/ReconstructionPipeline.cpp
    ../src/services/reconstruction/ReconstructionPostProcessor.cpp
    ../src/services/reconstruction/ReconstructionService.cpp
    ../src/services/reconstruction/ReconstructionWorker.cpp
    ../src/core/IReconstructionService.h
    ../src/services/reconstruction/SfmStage.cpp
    ../src/services/reconstruction/TrackSfM.cpp
    ../src/services/reconstruction/IncrementalSfM.cpp
    ../src/services/reconstruction/CameraParamsParser.cpp
    ../src/services/reconstruction/FeatureExtractor.cpp
    ../src/services/reconstruction/FeatureMatcher.cpp
    ../src/services/reconstruction/PoseEstimator.cpp
    ../src/services/reconstruction/Triangulator.cpp
    ../src/services/reconstruction/PointCloudFilter.cpp
    ../src/services/reconstruction/FeatureTrack.h
    ../src/services/reconstruction/MultiViewTriangulator.cpp
)

# Test ReconstructionPipeline (nhanh — không chạy hậu kỳ nặng)
qt_add_executable(TestReconstructionPipeline TestReconstructionPipeline.cpp
    ${RECON_PIPELINE_SOURCES}
)
target_include_directories(TestReconstructionPipeline PRIVATE 
    ../src/services/reconstruction
    ../src/core
    ${PCL_INCLUDE_DIRS}
    ${OpenCV_INCLUDE_DIRS}
)
target_link_libraries(TestReconstructionPipeline PRIVATE 
    Qt6::Test Qt6::Core Qt6::Widgets Qt6::Concurrent
    ${PCL_LIBRARIES}
    ${OpenCV_LIBS}
)
target_compile_definitions(TestReconstructionPipeline PRIVATE APP_LIBRARY)
add_test(NAME TestReconstructionPipeline COMMAND TestReconstructionPipeline)

# Test ReconstructionFinalize — hậu kỳ nặng (MLS densify 1M + Poisson depth 10
# + texture mesh). ~100s trong Release nên gắn label "slow": dev chạy
# `ctest -LE slow` khi cần vòng lặp nhanh, CI vẫn chạy mặc định.
qt_add_executable(TestReconstructionFinalize TestReconstructionFinalize.cpp
    ${RECON_PIPELINE_SOURCES}
)
target_include_directories(TestReconstructionFinalize PRIVATE
    ../src/services/reconstruction
    ../src/core
    ${PCL_INCLUDE_DIRS}
    ${OpenCV_INCLUDE_DIRS}
)
target_link_libraries(TestReconstructionFinalize PRIVATE
    Qt6::Test Qt6::Core Qt6::Widgets Qt6::Concurrent
    ${PCL_LIBRARIES}
    ${OpenCV_LIBS}
)
target_compile_definitions(TestReconstructionFinalize PRIVATE APP_LIBRARY)
add_test(NAME TestReconstructionFinalize COMMAND TestReconstructionFinalize)
set_tests_properties(TestReconstructionFinalize PROPERTIES TIMEOUT 900 LABELS slow)

# Test ShellService — IShellService contract (menu cache, tab panels, progress dialog)
qt_add_executable(TestShellService TestShellService.cpp)
target_include_directories(TestShellService PRIVATE ../src/services/shell)
target_link_libraries(TestShellService PRIVATE Qt6::Test Qt6::Core Qt6::Widgets AppCore)
add_test(NAME TestShellService COMMAND TestShellService)
# QTEST_MAIN → QApplication; CI không có desktop → offscreen platform plugin.
set_tests_properties(TestShellService PROPERTIES
    ENVIRONMENT "QT_QPA_PLATFORM=offscreen")

# Test Agent Actions
qt_add_executable(TestAgentActions TestAgentActions.cpp)
target_link_libraries(TestAgentActions PRIVATE Qt6::Test Qt6::Core AppCore)
add_test(NAME TestAgentActions COMMAND TestAgentActions)

# Test AI Yolo PostProcessor
qt_add_executable(TestAIYoloPostProcessor
    TestAIYoloPostProcessor.cpp
    ../src/services/ai/AIYoloPostProcessor.cpp
)
target_include_directories(TestAIYoloPostProcessor PRIVATE
    ../src/services/ai
)
target_link_libraries(TestAIYoloPostProcessor PRIVATE Qt6::Test Qt6::Core ${OpenCV_LIBS})
add_test(NAME TestAIYoloPostProcessor COMMAND TestAIYoloPostProcessor)

# Test UserManager crypto (PBKDF2 password hashing + SecretVault)
qt_add_executable(TestUserManagerCrypto TestUserManagerCrypto.cpp)
target_link_libraries(TestUserManagerCrypto PRIVATE Qt6::Test Qt6::Core AppCore)
if(WIN32)
    target_link_libraries(TestUserManagerCrypto PRIVATE crypt32)
endif()
add_test(NAME TestUserManagerCrypto COMMAND TestUserManagerCrypto)

# Test MailService credential lifecycle (cache invalidation on logout — no network)
qt_add_executable(TestMailCredentialLifecycle TestMailCredentialLifecycle.cpp)
target_include_directories(TestMailCredentialLifecycle PRIVATE
    ../src/core
    ../src/services/mail)
target_link_libraries(TestMailCredentialLifecycle PRIVATE Qt6::Test Qt6::Core AppCore)
add_test(NAME TestMailCredentialLifecycle COMMAND TestMailCredentialLifecycle)

# Test SmtpOutbox (IOutgoingMail adapter — validation branches, no network)
qt_add_executable(TestSmtpOutbox TestSmtpOutbox.cpp)
target_include_directories(TestSmtpOutbox PRIVATE ../src/services/mail)
target_link_libraries(TestSmtpOutbox PRIVATE Qt6::Test Qt6::Core AppCore)
if(WIN32)
    target_link_libraries(TestSmtpOutbox PRIVATE crypt32)
endif()
add_test(NAME TestSmtpOutbox COMMAND TestSmtpOutbox)

# Test SettingsService (injected config path — never touches real Config.ini)
qt_add_executable(TestSettingsService TestSettingsService.cpp)
target_include_directories(TestSettingsService PRIVATE ../src/services/settings)
target_link_libraries(TestSettingsService PRIVATE Qt6::Test Qt6::Core AppCore)
add_test(NAME TestSettingsService COMMAND TestSettingsService)

# Test CrosshairGeometry (pure DICOM-slice math used by SceneService;
# translation unit has no VTK dependency after ORI_* moved to the pure header)
qt_add_executable(TestCrosshairGeometry
    TestCrosshairGeometry.cpp
    ../src/services/scene/CrosshairGeometry.cpp
)
target_include_directories(TestCrosshairGeometry PRIVATE
    ../src/services/scene
)
target_link_libraries(TestCrosshairGeometry PRIVATE Qt6::Test Qt6::Core)
add_test(NAME TestCrosshairGeometry COMMAND TestCrosshairGeometry)
