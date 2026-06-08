#ifndef FORGOT_PASSWORD_DIALOG_H
#define FORGOT_PASSWORD_DIALOG_H

#include "../../utils/ModernDialog.h"

class QLineEdit;
class QLabel;
class QPushButton;
class QStackedWidget;

class ForgotPasswordDialog : public ModernDialog {
    Q_OBJECT
public:
    explicit ForgotPasswordDialog(QWidget *parent = nullptr);

private slots:
    void onSendOtp();
    void onVerifyOtp();
    void onResetPassword();

private:
    void setupUi();
    void applyStyle();
    void setError(const QString &msg);

    QStackedWidget *m_stack;

    // Step 1 – Enter email
    QLineEdit   *m_emailEdit;
    QPushButton *m_sendOtpBtn;

    // Step 2 – Enter OTP
    QLineEdit   *m_otpEdit;
    QPushButton *m_verifyOtpBtn;

    // Step 3 – New password
    QLineEdit   *m_newPassEdit;
    QLineEdit   *m_confirmPassEdit;
    QPushButton *m_resetBtn;

    QLabel  *m_statusLabel;
    QString  m_pendingEmail;
};

#endif // FORGOT_PASSWORD_DIALOG_H
