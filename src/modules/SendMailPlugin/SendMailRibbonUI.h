#ifndef SEND_MAIL_RIBBON_UI_H
#define SEND_MAIL_RIBBON_UI_H

#include "AbstractRibbonPanel.h"

class QToolButton;

class SendMailRibbonUI : public AbstractRibbonPanel {
    Q_OBJECT
public:
    SendMailRibbonUI(IAppContext *ctx, QWidget *panel, QObject *parent = nullptr);

    QToolButton *btnToggleMail() const { return m_btnToggleMail; }
    QToolButton *btnSettings() const { return m_btnSettings; }
    QGroupBox *groupMail() const { return group(); }

private:
    QToolButton *m_btnToggleMail = nullptr;
    QToolButton *m_btnSettings = nullptr;
};

#endif // SEND_MAIL_RIBBON_UI_H
