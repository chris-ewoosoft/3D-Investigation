#include <QtTest>
#include <QTemporaryDir>

#include "SettingsService.h"

/**
 * @brief Unit tests for SettingsService (ISettingsService impl).
 *
 * Uses the injected-path constructor so the test never touches the real
 * Config/Config.ini. Contract: last-used paths round-trip per key, unknown
 * keys default to empty, and saveSettings() is a no-op (interface compat).
 */
class TestSettingsService : public QObject {
    Q_OBJECT

private slots:
    void pathRoundTripsPerKey();
    void unknownKeyDefaultsToEmpty();
    void keysAreIndependent();
};

void TestSettingsService::pathRoundTripsPerKey()
{
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    SettingsService service(dir.filePath("Config.ini"));

    service.setLastUsedPath("obj", dir.filePath("model.obj"));
    QCOMPARE(service.getLastUsedPath("obj"), dir.filePath("model.obj"));
}

void TestSettingsService::unknownKeyDefaultsToEmpty()
{
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    SettingsService service(dir.filePath("Config.ini"));

    QCOMPARE(service.getLastUsedPath("never-written"), QString());
}

void TestSettingsService::keysAreIndependent()
{
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    SettingsService service(dir.filePath("Config.ini"));

    service.setLastUsedPath("obj", "/tmp/a.obj");
    service.setLastUsedPath("ply", "/tmp/b.ply");
    service.setLastUsedPath("obj", "/tmp/c.obj"); // overwrite one

    QCOMPARE(service.getLastUsedPath("obj"), QStringLiteral("/tmp/c.obj"));
    QCOMPARE(service.getLastUsedPath("ply"), QStringLiteral("/tmp/b.ply"));
}

QTEST_GUILESS_MAIN(TestSettingsService)
#include "TestSettingsService.moc"
