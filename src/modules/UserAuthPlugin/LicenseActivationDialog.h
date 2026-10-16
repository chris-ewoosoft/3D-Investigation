#ifndef LICENSE_ACTIVATION_DIALOG_H
#define LICENSE_ACTIVATION_DIALOG_H

#include "../../utils/ModernDialog.h"

class QRadioButton;
class QLineEdit;
class QLabel;
class QPushButton;

class LicenseActivationDialog : public ModernDialog {
    Q_OBJECT
public:
    explicit LicenseActivationDialog(const QString &username, bool allowClose = false,
                                     QWidget *parent = nullptr);

private slots:
    void onActivate();
    void onRadioChanged();

private:
    void setupUi();
    void applyStyle();
    void setStatus(const QString &msg, bool ok);

    QRadioButton *m_trialRadio;
    QRadioButton *m_keyRadio;
    QLineEdit    *m_keyEdit;
    QLabel       *m_statusLabel;
    QPushButton  *m_activateBtn;
    QString       m_username;
    bool          m_allowClose;
};

#endif // LICENSE_ACTIVATION_DIALOG_H
