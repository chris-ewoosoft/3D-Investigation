#ifndef PLUGINDEPENDENCYRESOLVER_H
#define PLUGINDEPENDENCYRESOLVER_H

#include <QList>
#include <QString>
#include <QStringList>

#include "Global.h"

/**
 * @brief Deterministic init-order resolution for IPlugin implementations.
 *
 * Contract (see IPlugin::dependencies / loadPhase / loadOrder):
 *  - dependencies(): names of plugins that MUST be initialized first. A hard
 *    edge: it always wins over phase and loadOrder hints.
 *  - loadPhase(): coarse grouping hint. Canonical values (low → high):
 *      "kernel" < "platform" < "default" < "ui" < "late".
 *    Unknown phases are wiring bugs — resolution fails loudly instead of
 *    silently mis-ordering plugins.
 *  - loadOrder(): fine-grained tie-break within one phase (lower first).
 *  - discoveryIndex: final stable tie-break (directory scan order).
 *
 * The loader (MainWindow) must abort plugin loading when ok == false; issues
 * name the exact plugin and reason (duplicate name, missing dependency,
 * unknown phase, dependency cycle).
 */
class APP_EXPORT PluginDependencyResolver {
public:
    struct PluginInfo {
        QString     name;
        QStringList dependencies;
        QString     phase = QStringLiteral("default");
        int         loadOrder = 100;
        int         discoveryIndex = 0;
    };

    enum class Severity { Error, Warning };

    struct Issue {
        Severity severity = Severity::Error;
        QString  plugin;  ///< related plugin name (may be empty)
        QString  message; ///< human-readable, ready to log
    };

    struct Result {
        bool       ok = false;
        QList<int> order; ///< indices into the input list; chỉ hợp lệ khi ok
        QList<Issue> issues;
    };

    /// Canonical phase names, ordered from lowest to highest priority rank.
    static QStringList knownPhases();

    /// Rank of @p phase inside knownPhases(); -1 when the phase is unknown.
    static int phaseRank(const QString &phase);

    /// Resolves the init order. Pure function — no Qt plugin loading involved,
    /// so the algorithm is unit-testable without QPluginLoader.
    static Result resolve(const QList<PluginInfo> &plugins);
};

#endif // PLUGINDEPENDENCYRESOLVER_H
