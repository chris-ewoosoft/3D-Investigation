#ifndef SMTP_MAILER_H
#define SMTP_MAILER_H

#include <QObject>
#include <QString>
#include <QStringList>

class QSslSocket;
#include "Global.h"

class APP_EXPORT SmtpMailer {
public:
    // Auto-detect SMTP host/port from sender email domain.
    SmtpMailer(const QString &senderEmail, const QString &senderPassword);

    bool sendMail(const QString &from, const QString &to,
                  const QString &subject, const QString &htmlBody,
                  QString &errorMsg);

    bool sendMail(const QString &from,
                  const QStringList &to,
                  const QStringList &cc,
                  const QStringList &bcc,
                  const QString &subject,
                  const QString &htmlBody,
                  const QStringList &attachmentPaths,
                  const QString &displayName,
                  QString &errorMsg);

private:
    bool waitForCode(QSslSocket *sock, const QString &expectedCode,
                     QString &response, int timeoutMs = 8000);
    bool sendCmd(QSslSocket *sock, const QString &cmd,
                 const QString &expectedCode, QString &response, int timeoutMs = 8000);

    QString m_host;
    int     m_port;
    QString m_username;
    QString m_password;
    bool    m_useSsl; // true = SMTPS port 465, false = STARTTLS port 587
};

#endif // SMTP_MAILER_H
