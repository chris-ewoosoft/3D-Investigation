#ifndef LOGIN_DIALOG_H
#define LOGIN_DIALOG_H

#include "../../utils/ModernDialog.h"

class QLineEdit;
class QLabel;
class QPushButton;
class QCheckBox;

class LoginDialog : public ModernDialog {
    Q_OBJECT
public:
    explicit LoginDialog(QWidget *parent = nullptr);
    QString loggedInUsername() const { return m_loggedInUser; }

private slots:
    void onLogin();
    void onForgotPassword();
    void onChangePassword();

private:
    void setupUi();
    void applyStyle();
    void setError(const QString &msg);

    QLineEdit   *m_userEdit;
    QLineEdit   *m_passEdit;
    QLabel      *m_statusLabel;
    QCheckBox   *m_rememberCheck;
    QString      m_loggedInUser;
};

#endif // LOGIN_DIALOG_H
