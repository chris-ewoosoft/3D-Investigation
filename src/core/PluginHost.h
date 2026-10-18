#ifndef PLUGINHOST_H
#define PLUGINHOST_H

#include <QString>
#include <QStringList>
#include <functional>
#include <vector>
#include "Global.h"

class IAppContext;
class IPlugin;
class QPluginLoader;

/**
 * @brief Owns the plugin load → resolve → initialize → cleanup lifecycle.
 *
 * Extracted from the exe shell (MainWindow) so the contract is compiled into
 * AppCore and unit-testable with an injected loader (tests never build a real
 * DLL). Guarantees:
 *  - discovery only probes library files whose name matches a filter,
 *  - dependency/phase ordering is delegated to PluginDependencyResolver and a
 *    resolution failure drops every plugin (never load a partial order),
 *  - initialize() runs in resolved order with rollback of the already
 *    initialized plugins on the first failure,
 *  - cleanup() runs in reverse order of successful initialization, exactly
 *    once per plugin.
 *
 * The shell keeps the two responsibilities that need the concrete window:
 * picking the plugin directory and registering the deferred services before
 * initializeAll() is called.
 */
class APP_EXPORT PluginHost {
public:
    /// Loads one library and returns its IPlugin instance (nullptr + *error on
    /// failure). Defaults to QPluginLoader; tests inject a fake.
    using Loader = std::function<IPlugin *(const QString &filePath, QString *error)>;

    /// Uses QPluginLoader; the loaders stay alive for the host's lifetime.
    PluginHost();
    /// Test seam: custom loader — no QPluginLoader instance is created.
    explicit PluginHost(Loader loader);
    ~PluginHost();

    PluginHost(const PluginHost &) = delete;
    PluginHost &operator=(const PluginHost &) = delete;

    /// (Re)loads plugins from @p directory. Libraries whose file name does not
    /// contain @p nameFilter (case-insensitive) are ignored, so Qt/vcpkg
    /// dependencies are never probed. Returns the number of loaded plugins;
    /// failures are reported through errors().
    int discover(const QString &directory,
                 const QString &nameFilter = QStringLiteral("Plugin"));

    /// Resolves initialize() order (dependencies > phase > loadOrder >
    /// discovery order). On failure errors() carries the concrete issue list
    /// and every plugin is dropped — the caller must not continue loading.
    bool resolveOrder();

    /// initialize() in resolved order. On the first failure every plugin that
    /// already initialized is cleaned up (reverse order) and the host empties
    /// itself; returns false so the caller can abort the application.
    bool initializeAll(IAppContext *context);

    /// cleanup() in reverse order of successful initialization, then drops the
    /// plugins and unloads the libraries. Safe to call repeatedly or early
    /// (before discover/initialize).
    void shutdown();

    QList<IPlugin *> plugins() const;
    bool isEmpty() const;

    /// Errors accumulated so far (discovery, resolution, initialization).
    QStringList errors() const { return m_errors; }
    /// Moves the errors out (clearing them) — for shells that log per phase.
    QStringList takeErrors();

private:
    IPlugin *loadWithQPluginLoader(const QString &filePath, QString *error);
    void clearLoaders();

    Loader m_loader;                        ///< empty → QPluginLoader-based default
    std::vector<QPluginLoader *> m_loaders; ///< default loader's instances
    QList<IPlugin *> m_plugins;             ///< discovered, then resolved order
    QList<IPlugin *> m_initialized;         ///< successfully initialized (init order)
    QStringList m_errors;
};

#endif // PLUGINHOST_H
