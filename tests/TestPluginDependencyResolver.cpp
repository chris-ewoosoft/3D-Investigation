#include <QTest>

#include "PluginDependencyResolver.h"

using Resolver = PluginDependencyResolver;
using Plugin = PluginDependencyResolver::PluginInfo;
using Result = PluginDependencyResolver::Result;

namespace {

Plugin makePlugin(const QString &name, int loadOrder = 100,
                  const QStringList &dependencies = {},
                  const QString &phase = QStringLiteral("default"))
{
    Plugin plugin;
    plugin.name = name;
    plugin.loadOrder = loadOrder;
    plugin.dependencies = dependencies;
    plugin.phase = phase;
    return plugin;
}

/// Gán discoveryIndex theo thứ tự danh sách — như MainWindow làm khi scan
/// thư mục plugins.
QList<Plugin> withDiscoveryOrder(QList<Plugin> plugins)
{
    for (int i = 0; i < plugins.size(); ++i) plugins[i].discoveryIndex = i;
    return plugins;
}

QStringList orderedNames(const Result &result, const QList<Plugin> &plugins)
{
    QStringList names;
    for (int index : result.order) names.append(plugins[index].name);
    return names;
}

bool hasError(const Result &result, const QString &needle)
{
    for (const auto &issue : result.issues) {
        if (issue.severity == Resolver::Severity::Error
            && issue.message.contains(needle)) {
            return true;
        }
    }
    return false;
}

} // namespace

class TestPluginDependencyResolver : public QObject {
    Q_OBJECT

private slots:
    void knownPhasesAreRanked();
    void emptyListResolves();
    void sortsByLoadOrder();
    void dependencyBeatsLoadOrder();
    void dependencyBeatsPhase();
    void phaseBeatsLoadOrder();
    void discoveryIndexIsStableTieBreak();
    void diamondDependencies();
    void duplicateDependencyEntriesAreDeduplicated();
    void missingDependencyFails();
    void unknownPhaseFails();
    void duplicateNamesFail();
    void emptyPluginNameFails();
    void selfDependencyIsACycle();
    void cycleIsReportedWithPath();
    void cycleIsFoundAfterAnAcyclicComponentWasScanned();
    void failedResultCarriesNoOrder();
};

void TestPluginDependencyResolver::knownPhasesAreRanked()
{
    QCOMPARE(Resolver::knownPhases().size(), 5);
    QCOMPARE(Resolver::phaseRank(QStringLiteral("kernel")), 0);
    QCOMPARE(Resolver::phaseRank(QStringLiteral("platform")), 1);
    QCOMPARE(Resolver::phaseRank(QStringLiteral("default")), 2);
    QCOMPARE(Resolver::phaseRank(QStringLiteral("ui")), 3);
    QCOMPARE(Resolver::phaseRank(QStringLiteral("late")), 4);
    QCOMPARE(Resolver::phaseRank(QStringLiteral("nope")), -1);
}

void TestPluginDependencyResolver::emptyListResolves()
{
    const Result result = Resolver::resolve(QList<Plugin>{});
    QVERIFY(result.ok);
    QVERIFY(result.order.isEmpty());
}

void TestPluginDependencyResolver::sortsByLoadOrder()
{
    const QList<Plugin> plugins = withDiscoveryOrder({
        makePlugin(QStringLiteral("b"), 1),
        makePlugin(QStringLiteral("a"), 100),
    });
    const Result result = Resolver::resolve(plugins);
    QVERIFY(result.ok);
    QCOMPARE(orderedNames(result, plugins), (QStringList{"b", "a"}));
}

void TestPluginDependencyResolver::dependencyBeatsLoadOrder()
{
    // "b" muốn load sớm (loadOrder 1) nhưng phụ thuộc "a" (loadOrder 999):
    // cạnh phụ thuộc luôn thắng hint.
    const QList<Plugin> plugins = withDiscoveryOrder({
        makePlugin(QStringLiteral("b"), 1, {QStringLiteral("a")}),
        makePlugin(QStringLiteral("a"), 999),
    });
    const Result result = Resolver::resolve(plugins);
    QVERIFY(result.ok);
    QCOMPARE(orderedNames(result, plugins), (QStringList{"a", "b"}));
}

void TestPluginDependencyResolver::dependencyBeatsPhase()
{
    // "ui" phụ thuộc "late": phase chỉ là hint, cạnh phụ thuộc thắng.
    const QList<Plugin> plugins = withDiscoveryOrder({
        makePlugin(QStringLiteral("u"), 100, {QStringLiteral("l")}, QStringLiteral("ui")),
        makePlugin(QStringLiteral("l"), 100, {}, QStringLiteral("late")),
    });
    const Result result = Resolver::resolve(plugins);
    QVERIFY(result.ok);
    QCOMPARE(orderedNames(result, plugins), (QStringList{"l", "u"}));
}

void TestPluginDependencyResolver::phaseBeatsLoadOrder()
{
    // Không có cạnh phụ thuộc: phase rank quyết định trước loadOrder.
    const QList<Plugin> plugins = withDiscoveryOrder({
        makePlugin(QStringLiteral("z"), 1, {}, QStringLiteral("late")),
        makePlugin(QStringLiteral("k"), 999, {}, QStringLiteral("kernel")),
    });
    const Result result = Resolver::resolve(plugins);
    QVERIFY(result.ok);
    QCOMPARE(orderedNames(result, plugins), (QStringList{"k", "z"}));
}

void TestPluginDependencyResolver::discoveryIndexIsStableTieBreak()
{
    // Cùng phase + loadOrder: giữ nguyên thứ tự scan thư mục (ổn định).
    const QList<Plugin> plugins = withDiscoveryOrder({
        makePlugin(QStringLiteral("c")),
        makePlugin(QStringLiteral("a")),
    });
    const Result result = Resolver::resolve(plugins);
    QVERIFY(result.ok);
    QCOMPARE(orderedNames(result, plugins), (QStringList{"c", "a"}));
}

void TestPluginDependencyResolver::diamondDependencies()
{
    // d ← b, c ← a; b/c cùng key nên tie-break bằng discoveryIndex (c index 1 < b index 2).
    const QList<Plugin> plugins = withDiscoveryOrder({
        makePlugin(QStringLiteral("d"), 100, {QStringLiteral("b"), QStringLiteral("c")}),
        makePlugin(QStringLiteral("c"), 100, {QStringLiteral("a")}),
        makePlugin(QStringLiteral("b"), 100, {QStringLiteral("a")}),
        makePlugin(QStringLiteral("a")),
    });
    const Result result = Resolver::resolve(plugins);
    QVERIFY(result.ok);
    QCOMPARE(orderedNames(result, plugins), (QStringList{"a", "c", "b", "d"}));
}

void TestPluginDependencyResolver::duplicateDependencyEntriesAreDeduplicated()
{
    // Khai báo trùng tên không được tính thành 2 cạnh (sẽ tạo cycle giả).
    const QList<Plugin> plugins = withDiscoveryOrder({
        makePlugin(QStringLiteral("a")),
        makePlugin(QStringLiteral("b"), 100, {QStringLiteral("a"), QStringLiteral("a")}),
    });
    const Result result = Resolver::resolve(plugins);
    QVERIFY(result.ok);
    QCOMPARE(orderedNames(result, plugins), (QStringList{"a", "b"}));
}

void TestPluginDependencyResolver::missingDependencyFails()
{
    const QList<Plugin> plugins = withDiscoveryOrder({
        makePlugin(QStringLiteral("b"), 100, {QStringLiteral("ghost")}),
    });
    const Result result = Resolver::resolve(plugins);
    QVERIFY(!result.ok);
    QVERIFY(hasError(result, QStringLiteral("ghost")));
    QCOMPARE(result.issues.first().plugin, QStringLiteral("b"));
}

void TestPluginDependencyResolver::unknownPhaseFails()
{
    const QList<Plugin> plugins = withDiscoveryOrder({
        makePlugin(QStringLiteral("x"), 100, {}, QStringLiteral("banana")),
    });
    const Result result = Resolver::resolve(plugins);
    QVERIFY(!result.ok);
    QVERIFY(hasError(result, QStringLiteral("banana")));
}

void TestPluginDependencyResolver::duplicateNamesFail()
{
    const QList<Plugin> plugins = withDiscoveryOrder({
        makePlugin(QStringLiteral("a")),
        makePlugin(QStringLiteral("a")),
    });
    const Result result = Resolver::resolve(plugins);
    QVERIFY(!result.ok);
    QVERIFY(hasError(result, QStringLiteral("duplicate plugin name")));
}

void TestPluginDependencyResolver::emptyPluginNameFails()
{
    // A plugin whose pluginName() is empty cannot be addressed by a
    // dependency or shown in an error — the resolver must reject it instead
    // of loading an anonymous plugin.
    const QList<Plugin> plugins = withDiscoveryOrder({makePlugin(QString())});
    const Result result = Resolver::resolve(plugins);
    QVERIFY(!result.ok);
    QVERIFY(result.order.isEmpty());
    QVERIFY(hasError(result, QStringLiteral("empty name")));
}

void TestPluginDependencyResolver::selfDependencyIsACycle()
{
    const QList<Plugin> plugins = withDiscoveryOrder({
        makePlugin(QStringLiteral("a"), 100, {QStringLiteral("a")}),
    });
    const Result result = Resolver::resolve(plugins);
    QVERIFY(!result.ok);
    QVERIFY(hasError(result, QStringLiteral("dependency cycle")));
    QVERIFY(hasError(result, QStringLiteral("a -> a")));
}

void TestPluginDependencyResolver::cycleIsReportedWithPath()
{
    // a → b → a, c độc lập: báo đúng chu trình, không chỉ "có cycle".
    const QList<Plugin> plugins = withDiscoveryOrder({
        makePlugin(QStringLiteral("a"), 100, {QStringLiteral("b")}),
        makePlugin(QStringLiteral("b"), 100, {QStringLiteral("a")}),
        makePlugin(QStringLiteral("c")),
    });
    const Result result = Resolver::resolve(plugins);
    QVERIFY(!result.ok);
    QVERIFY(hasError(result, QStringLiteral("a -> b -> a")));
}

void TestPluginDependencyResolver::cycleIsFoundAfterAnAcyclicComponentWasScanned()
{
    // The cycle reporter walks the graph depth-first. When the first scanned
    // component is acyclic, the walk has to backtrack out of it before it
    // reaches the cycle — a decision of its own, not an accident of the
    // cycle being found on the very first path.
    const QList<Plugin> plugins = withDiscoveryOrder({
        makePlugin(QStringLiteral("c")),
        makePlugin(QStringLiteral("a"), 100, {QStringLiteral("b")}),
        makePlugin(QStringLiteral("b"), 100, {QStringLiteral("a")}),
    });
    const Result result = Resolver::resolve(plugins);
    QVERIFY(!result.ok);
    QVERIFY(hasError(result, QStringLiteral("a -> b -> a")));
}

void TestPluginDependencyResolver::failedResultCarriesNoOrder()
{
    // order chỉ hợp lệ khi ok — loader không được dùng order từ result lỗi.
    const QList<Plugin> plugins = withDiscoveryOrder({
        makePlugin(QStringLiteral("b"), 100, {QStringLiteral("ghost")}),
    });
    const Result result = Resolver::resolve(plugins);
    QVERIFY(!result.ok);
    QVERIFY(result.order.isEmpty());
}

#include "TestPluginDependencyResolver.moc"

QTEST_APPLESS_MAIN(TestPluginDependencyResolver)
