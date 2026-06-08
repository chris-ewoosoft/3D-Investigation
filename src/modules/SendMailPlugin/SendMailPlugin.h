#ifndef SEND_MAIL_PLUGIN_H
#define SEND_MAIL_PLUGIN_H

#include "IPlugin.h"

#include <QObject>

class IAppContext;
class MailDockWidget;
class SendMailRibbonUI;

class SendMailPlugin : public QObject, public IPlugin {
    Q_OBJECT
    Q_PLUGIN_METADATA(IID IPlugin_iid)
    Q_INTERFACES(IPlugin)

public:
    QString pluginName() const override { return "Send Mail Plugin"; }
    void initialize(IAppContext *context) override;
    void cleanup() override;
    int loadOrder() const override { return 45; }

private:
    IAppContext *m_ctx = nullptr;
    SendMailRibbonUI *m_ribbonUI = nullptr;
    MailDockWidget *m_dockUI = nullptr;
};

#endif // SEND_MAIL_PLUGIN_H
