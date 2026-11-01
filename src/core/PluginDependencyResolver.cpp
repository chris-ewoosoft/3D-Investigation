#include "PluginDependencyResolver.h"

#include <QHash>
#include <QSet>

#include <algorithm>
#include <functional>
#include <queue>
#include <vector>

namespace {

/// Ready-queue comparator: smallest (phaseRank, loadOrder, discoveryIndex)
/// first (std::priority_queue is a max-heap, so the comparator inverts).
struct ReadyCompare {
    const QList<PluginDependencyResolver::PluginInfo> *plugins = nullptr;

    bool operator()(int lhs, int rhs) const {
        const auto &a = (*plugins)[lhs];
        const auto &b = (*plugins)[rhs];
        const int rankA = PluginDependencyResolver::phaseRank(a.phase);
        const int rankB = PluginDependencyResolver::phaseRank(b.phase);
        if (rankA != rankB) return rankA > rankB;
        if (a.loadOrder != b.loadOrder) return a.loadOrder > b.loadOrder;
        return a.discoveryIndex > b.discoveryIndex;
    }
};

/// Extracts one concrete dependency cycle among the unresolved nodes so the
/// error message can show the actual path ("a -> b -> a") instead of a
/// generic "some plugins form a cycle".
QList<int> findCycle(const QList<QList<int>> &deps, const QList<bool> &resolved)
{
    enum class State { Unvisited, InStack, Done };
    std::vector<State> state(deps.size(), State::Unvisited);
    std::vector<int> stack;
    QList<int> cycle;

    std::function<bool(int)> visit = [&](int node) -> bool {
        state[node] = State::InStack;
        stack.push_back(node);
        for (int dep : deps[node]) {
            if (resolved[dep]) continue;
            if (state[dep] == State::InStack) {
                const auto pos = std::find(stack.begin(), stack.end(), dep);
                for (auto it = pos; it != stack.end(); ++it) cycle.append(*it);
                cycle.append(dep);
                return true;
            }
            if (state[dep] == State::Unvisited && visit(dep)) return true;
        }
        stack.pop_back();
        state[node] = State::Done;
        return false;
    };

    const int count = static_cast<int>(deps.size());
    for (int i = 0; i < count; ++i) {
        if (!resolved[i] && state[i] == State::Unvisited && visit(i)) break;
    }
    return cycle;
}

QString namesOf(const QList<PluginDependencyResolver::PluginInfo> &plugins,
                const QList<int> &indices)
{
    QStringList names;
    for (int index : indices) names.append(plugins[index].name);
    return names.join(QStringLiteral(" -> "));
}

} // namespace

QStringList PluginDependencyResolver::knownPhases()
{
    return {
        QStringLiteral("kernel"),
        QStringLiteral("platform"),
        QStringLiteral("default"),
        QStringLiteral("ui"),
        QStringLiteral("late"),
    };
}

int PluginDependencyResolver::phaseRank(const QString &phase)
{
    static const QStringList kPhases = knownPhases();
    return kPhases.indexOf(phase);
}

PluginDependencyResolver::Result
PluginDependencyResolver::resolve(const QList<PluginInfo> &plugins)
{
    Result result;
    const int count = static_cast<int>(plugins.size());

    // ── Validate names: non-empty and unique ─────────────────────────────────
    QHash<QString, int> indexByName;
    indexByName.reserve(count);
    for (int i = 0; i < count; ++i) {
        const PluginInfo &plugin = plugins[i];
        if (plugin.name.trimmed().isEmpty()) {
            result.issues.append({Severity::Error, plugin.name,
                                  QStringLiteral("plugin #%1 has an empty name").arg(i)});
            continue;
        }
        if (indexByName.contains(plugin.name)) {
            result.issues.append({Severity::Error, plugin.name,
                                  QStringLiteral("duplicate plugin name — two plugins both "
                                                 "report pluginName() == \"%1\"").arg(plugin.name)});
            continue;
        }
        indexByName.insert(plugin.name, i);
    }

    // ── Validate phases ──────────────────────────────────────────────────────
    for (int i = 0; i < count; ++i) {
        if (phaseRank(plugins[i].phase) < 0) {
            result.issues.append({Severity::Error, plugins[i].name,
                                  QStringLiteral("unknown loadPhase \"%1\" — canonical phases: %2")
                                      .arg(plugins[i].phase, knownPhases().join(QStringLiteral(", ")))});
        }
    }

    // ── Build the dependency graph (hard edges) ──────────────────────────────
    QList<QList<int>> deps(count);       // node → prerequisites
    QList<QList<int>> dependents(count); // node → consumers
    for (int i = 0; i < count; ++i) {
        QSet<int> unique;
        for (const QString &name : plugins[i].dependencies) {
            const auto it = indexByName.constFind(name);
            if (it == indexByName.constEnd()) {
                result.issues.append({Severity::Error, plugins[i].name,
                                      QStringLiteral("depends on \"%1\" but that plugin is not loaded")
                                          .arg(name)});
                continue;
            }
            if (!unique.contains(it.value())) {
                unique.insert(it.value());
                deps[i].append(it.value());
                dependents[it.value()].append(i);
            }
        }
    }

    // ── Kahn's algorithm with a priority tie-break ───────────────────────────
    std::vector<int> indegree(static_cast<size_t>(count));
    for (int i = 0; i < count; ++i) indegree[static_cast<size_t>(i)] = static_cast<int>(deps[i].size());

    ReadyCompare compare;
    compare.plugins = &plugins;
    std::priority_queue<int, std::vector<int>, ReadyCompare> ready(compare);
    for (int i = 0; i < count; ++i) {
        if (indegree[static_cast<size_t>(i)] == 0) ready.push(i);
    }

    QList<int> order;
    order.reserve(count);
    while (!ready.empty()) {
        const int node = ready.top();
        ready.pop();
        order.append(node);
        for (int dependent : dependents[node]) {
            if (--indegree[static_cast<size_t>(dependent)] == 0) ready.push(dependent);
        }
    }

    // ── Report cycles explicitly ─────────────────────────────────────────────
    if (order.size() != count) {
        QList<bool> resolved(count, false);
        for (int index : order) resolved[index] = true;
        const QList<int> cycle = findCycle(deps, resolved);
        result.issues.append({Severity::Error,
                              cycle.isEmpty() ? QString() : plugins[cycle.first()].name,
                              QStringLiteral("dependency cycle: %1").arg(namesOf(plugins, cycle))});
    }

    // ── Verdict ──────────────────────────────────────────────────────────────
    bool hasError = false;
    for (const Issue &issue : result.issues) {
        if (issue.severity == Severity::Error) {
            hasError = true;
            break;
        }
    }
    result.ok = !hasError && order.size() == count;
    if (result.ok) result.order = order;
    return result;
}
