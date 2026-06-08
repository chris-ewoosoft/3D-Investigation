#include <QtTest>
#include <QApplication>
#include "AppConfig.h"

class TestAppConfig : public QObject {
    Q_OBJECT

private slots:
    void testSingletonInstance() {
        AppConfig& instance1 = AppConfig::instance();
        AppConfig& instance2 = AppConfig::instance();
        QCOMPARE(&instance1, &instance2);
    }

    void testDirectoryPaths() {
        AppConfig& config = AppConfig::instance();
        
        // This will verify that the paths are not empty
        QVERIFY(!config.appDir().isEmpty());
        QVERIFY(!config.configPath().isEmpty());
        QVERIFY(!config.logsDir().isEmpty());
        QVERIFY(!config.pluginsDir().isEmpty());
    }
};

QTEST_MAIN(TestAppConfig)

#include "TestAppConfig.moc"
