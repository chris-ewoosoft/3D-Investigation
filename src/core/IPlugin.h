#ifndef IPLUGIN_H
#define IPLUGIN_H

#include <QtPlugin>
#include <QString>

class IAppContext;

class IPlugin {
public:
    virtual ~IPlugin() = default;
    virtual QString pluginName() const = 0;
    virtual void initialize(IAppContext* context) = 0;
    virtual void cleanup() = 0;
    virtual int  loadOrder() const { return 100; }
    virtual void onAppReady() {}
};

#define IPlugin_iid "com.3dreconstruction.IPlugin"
Q_DECLARE_INTERFACE(IPlugin, IPlugin_iid)

#endif // IPLUGIN_H
