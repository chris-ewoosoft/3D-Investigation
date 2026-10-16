#ifndef SETTINGS_DIALOG_H
#define SETTINGS_DIALOG_H

#include "../../utils/ModernDialog.h"
#include <QListWidget>
#include <QStackedWidget>

class IAppContext;

class SettingsDialog : public ModernDialog {
    Q_OBJECT
public:
    explicit SettingsDialog(const QString &username, bool isAdmin,
                            IAppContext *ctx, QWidget *parent = nullptr);

private:
    void setupUi();

    QListWidget    *m_navList   = nullptr;
    QStackedWidget *m_stack     = nullptr;
    QString         m_username;
    bool            m_isAdmin;
    IAppContext    *m_ctx = nullptr;
};

#endif // SETTINGS_DIALOG_H
