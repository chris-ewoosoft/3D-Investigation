#ifndef IPLUGIN_H
#define IPLUGIN_H

#include <QtPlugin>
#include <QString>

class IAppContext;

/**
 * @brief Plugin lifecycle contract resolved by PluginDependencyResolver
 *        BEFORE any initialize() call.
 *
 * Ordering rules (hard edge always wins over hints):
 *  1. dependencies() — names of plugins that must initialize first.
 *  2. loadPhase()    — coarse grouping hint; canonical values (low → high):
 *                      "kernel" < "platform" < "default" < "ui" < "late".
 *  3. loadOrder()    — fine-grained tie-break inside one phase (lower first).
 *  4. Discovery order of the plugin directory (stable tie-break).
 *
 * Missing dependencies, duplicate plugin names, unknown phases and dependency
 * cycles abort plugin loading with a concrete error — they are wiring bugs,
 * not runtime conditions (same fail-fast stance as ServiceRegistry).
 */
class IPlugin {
public:
    /// Version of the IPlugin/plugin-host contract. Bump it whenever the
    /// virtual surface below changes; PluginHost then refuses to load plugins
    /// built against a different version instead of crashing on a stale ABI.
    static constexpr int kApiVersion = 1;

    virtual ~IPlugin() = default;
    virtual QString pluginName() const = 0;
    [[nodiscard]] virtual bool initialize(IAppContext* context) = 0;
    virtual void cleanup() = 0;

    /// Contract version implemented by this plugin (defaults to kApiVersion).
    virtual int apiVersion() const { return kApiVersion; }

    /// Fine-grained init-order hint inside one phase (lower first).
    virtual int  loadOrder() const { return 100; }

    /// Names of plugins that MUST be initialized before this one.
    virtual QStringList dependencies() const { return {}; }

    /// Coarse init-order hint; see canonical phase list above.
    virtual QString loadPhase() const { return QStringLiteral("default"); }
};

#define IPlugin_iid "com.3dreconstruction.IPlugin"
Q_DECLARE_INTERFACE(IPlugin, IPlugin_iid)

#endif // IPLUGIN_H
