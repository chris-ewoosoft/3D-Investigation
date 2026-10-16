#ifndef ABOUT_DIALOG_H
#define ABOUT_DIALOG_H

#include "../../utils/ModernDialog.h"

class AboutDialog : public ModernDialog {
    Q_OBJECT
public:
    explicit AboutDialog(const QString &username, QWidget *parent = nullptr);

private slots:
    void onActivateLicense();

private:
    void setupUi();
    void applyStyle();
    QString m_username;
};

#endif // ABOUT_DIALOG_H
