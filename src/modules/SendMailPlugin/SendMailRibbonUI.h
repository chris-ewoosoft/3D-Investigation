#ifndef SEND_MAIL_RIBBON_UI_H
#define SEND_MAIL_RIBBON_UI_H

#include <QObject>

class IAppContext;
class QGroupBox;
class QToolButton;
class QWidget;

class SendMailRibbonUI : public QObject {
    Q_OBJECT
public:
    SendMailRibbonUI(IAppContext *ctx, QWidget *panel, QObject *parent = nullptr);

    QToolButton *btnToggleMail() const { return m_btnToggleMail; }
    QToolButton *btnSettings() const { return m_btnSettings; }
    QGroupBox *groupMail() const { return m_groupMail; }

private:
    IAppContext *m_ctx = nullptr;
    QGroupBox *m_groupMail = nullptr;
    QToolButton *m_btnToggleMail = nullptr;
    QToolButton *m_btnSettings = nullptr;
};

#endif // SEND_MAIL_RIBBON_UI_H
