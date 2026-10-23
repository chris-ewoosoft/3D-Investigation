#include "BlockingCall.h"
#include "MailSettingsDialog.h"

#include "IAppContext.h"
#include "IMailService.h"
#include "ModernMessageBox.h"
#include "IAuthService.h"
#include "IUserPreferences.h"

#include <QDialog>
#include <QFormLayout>
#include <QFutureWatcher>
#include <QHBoxLayout>
#include <QLineEdit>
#include <QPushButton>
#include <QTextEdit>
#include <QVBoxLayout>
#include <QtConcurrent>

namespace MailSettingsDialog {

void show(QWidget *parent, IAppContext *context, IMailService *mailService)
{
    // The narrow contracts: the current identity (IAuthService) and the
    // per-user preferences/secrets (IUserPreferences) — not the whole user
    // service.
    auto* auth = context->services()->get<IAuthService>();
    auto* prefs = context->services()->get<IUserPreferences>();
    if (!auth || !prefs) return;

    // The background tasks of this dialog (the connection test). The wait at
    // the end of the function keeps them from outliving the dialog.
    blocking::TaskTracker tasks;

    QDialog dlg(parent);
    dlg.setWindowTitle(context->translate("mail.settings"));
    dlg.setMinimumWidth(460);
    auto *layout = new QVBoxLayout(&dlg);
    auto *form = new QFormLayout();

    auto *email = new QLineEdit(mailService ? mailService->senderEmail() : QString(), &dlg);
    auto *password = new QLineEdit(&dlg);
    password->setEchoMode(QLineEdit::Password);
    auto *displayName = new QLineEdit(auth->currentUsername(), &dlg);
    auto *signature = new QTextEdit(&dlg);
    signature->setAcceptRichText(true);
    signature->setMinimumHeight(110);

    const QString username = auth->currentUsername();
    password->setText(prefs->getUserSecret(username, "mail_password"));
    displayName->setText(prefs->getUserPref(username, "mail_display_name", username));
    signature->setHtml(prefs->getUserPref(
        username,
        "mail_signature",
        QString("<b>%1</b><br>3D-Reconstruction").arg(username)));

    form->addRow(context->translate("mail.account"), email);
    form->addRow(context->translate("mail.password"), password);
    form->addRow(context->translate("mail.display_name"), displayName);
    form->addRow(context->translate("mail.signature"), signature);
    layout->addLayout(form);

    auto *buttons = new QHBoxLayout();
    auto *testBtn = new QPushButton(context->translate("mail.test"), &dlg);
    auto *cancelBtn = new QPushButton(context->translate("common.cancel"), &dlg);
    auto *saveBtn = new QPushButton(context->translate("common.save"), &dlg);
    saveBtn->setObjectName("primary");
    buttons->addWidget(testBtn);
    buttons->addStretch();
    buttons->addWidget(cancelBtn);
    buttons->addWidget(saveBtn);
    layout->addLayout(buttons);

    QObject::connect(cancelBtn, &QPushButton::clicked, &dlg, &QDialog::reject);
    QObject::connect(saveBtn, &QPushButton::clicked, &dlg, [&]() {
        if (mailService) {
            mailService->setCredentials(email->text().trimmed(), password->text(), displayName->text().trimmed());
            context->services()->get<IUserPreferences>()->setUserPref(username, "mail_signature", signature->toHtml());
        }
        dlg.accept();
    });
    QObject::connect(testBtn, &QPushButton::clicked, &dlg, [&]() {
        if (!mailService) return;
        mailService->setCredentials(email->text().trimmed(), password->text(), displayName->text().trimmed());

        // Network I/O off the GUI thread; the dialog stays responsive.
        testBtn->setEnabled(false);
        auto *watcher = new QFutureWatcher<QPair<bool, QString>>(&dlg);
        QObject::connect(watcher, &QFutureWatcher<QPair<bool, QString>>::finished, &dlg,
                [&dlg, testBtn, context, watcher]() {
            const auto result = watcher->result();
            watcher->deleteLater();
            if (!dlg.isVisible()) return; // dialog closed while the test ran
            testBtn->setEnabled(true);
            if (result.first) {
                ModernMessageBox::information(&dlg, context->translate("common.success"), context->translate("mail.test_ok"));
            } else {
                ModernMessageBox::warning(&dlg, context->translate("common.error"), result.second);
            }
        });
        watcher->setFuture(blocking::run([mailService]() {
            QString error;
            const bool ok = mailService->testConnection(error);
            return qMakePair(ok, error);
        }, &tasks));
    });

    dlg.exec();
    // The connection test may still be running: it must not outlive the
    // dialog (and the mail service it calls). The wait is bounded.
    tasks.waitForFinished(3000);
}

}
