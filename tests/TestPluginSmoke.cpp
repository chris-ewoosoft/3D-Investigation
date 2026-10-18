#include <QtTest>
#include <QDir>
#include <QStringList>

#include "IPlugin.h"
#include "PluginHost.h"

/**
 * @brief Smoke test on the real plugin binaries.
 *
 * Unlike TestPluginHost (which injects a fake loader), this test exercises
 * QPluginLoader against the DLLs produced by the default build: a broken
 * Q_PLUGIN_METADATA block, a missing export, a stale IPlugin ABI or invalid
 * ordering metadata is caught before the application is even started.
 *
 * The plugin directory is injected at compile time by CMake
 * (PLUGIN_SMOKE_DIR, resolved from the ViewerPlugin target) and the test target
 * depends on every plugin target so the DLLs exist when CTest runs.
 */
class TestPluginSmoke : public QObject {
    Q_OBJECT

private slots:
    void allShippedPluginsLoadAndResolve();
};

void TestPluginSmoke::allShippedPluginsLoadAndResolve()
{
#ifdef PLUGIN_SMOKE_DIR
    const QString directory = QStringLiteral(PLUGIN_SMOKE_DIR);
#else
    const QString directory;
#endif
    QVERIFY2(!directory.isEmpty(), "PLUGIN_SMOKE_DIR was not configured by CMake");
    QVERIFY2(QDir(directory).exists(),
             qPrintable(QStringLiteral("plugin directory not found: ") + directory));

    PluginHost host;
    host.discover(directory);
    QVERIFY2(host.errors().isEmpty(),
             qPrintable(host.errors().join(QStringLiteral(" | "))));

    QStringList names;
    for (IPlugin *plugin : host.plugins()) {
        QCOMPARE(plugin->apiVersion(), IPlugin::kApiVersion);
        names << plugin->pluginName();
    }
    names.sort();

    QStringList expected{"AI Assistant Plugin", "AI Processor Plugin",
                         "Reconstruction Plugin", "Send Mail Plugin",
                         "User Authentication Plugin", "Viewer Plugin"};
    expected.sort();
    QCOMPARE(names, expected);

    // The dependency/phase metadata of the real binaries must resolve cleanly.
    QVERIFY2(host.resolveOrder(),
             qPrintable(host.errors().join(QStringLiteral(" | "))));

    // Host teardown (~PluginHost → shutdown()) must be safe even though the
    // plugins were never initialized.
}

QTEST_MAIN(TestPluginSmoke)
#include "TestPluginSmoke.moc"
