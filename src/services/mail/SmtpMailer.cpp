#include "SmtpMailer.h"

#include "MailThreadGuard.h"

#include <QByteArray>
#include <QDateTime>
#include <QDebug>
#include <QElapsedTimer>
#include <QFile>
#include <QFileInfo>
#include <QMimeDatabase>
#include <QRandomGenerator>
#include <QSslSocket>
#include <QTextStream>

namespace {
QString sanitizeHeader(QString value)
{
    value.replace('\r', ' ');
    value.replace('\n', ' ');
    return value.trimmed();
}

QString encodedWord(const QString &value)
{
    if (value.isEmpty()) return {};
    return "=?UTF-8?B?" + QString::fromLatin1(value.toUtf8().toBase64()) + "?=";
}

QString joinMailboxList(const QStringList &addresses)
{
    QStringList clean;
    for (const QString &address : addresses) {
        const QString trimmed = sanitizeHeader(address);
        if (!trimmed.isEmpty()) clean << "<" + trimmed + ">";
    }
    return clean.join(", ");
}

QString wrapBase64(const QByteArray &bytes)
{
    const QByteArray encoded = bytes.toBase64();
    QString result;
    for (int i = 0; i < encoded.size(); i += 76) {
        result += QString::fromLatin1(encoded.mid(i, 76)) + "\r\n";
    }
    return result;
}

QString makeBoundary()
{
    return QString("----3DReconstruction-%1-%2")
        .arg(QDateTime::currentMSecsSinceEpoch())
        .arg(QRandomGenerator::global()->generate());
}
}

SmtpMailer::SmtpMailer(const QString &senderEmail, const QString &senderPassword)
    : m_username(senderEmail), m_password(senderPassword), m_port(587), m_useSsl(false)
{
    const QString domain = senderEmail.section('@', 1).toLower();
    if (domain == "gmail.com") {
        m_host = "smtp.gmail.com";
    } else if (domain == "hotmail.com" || domain == "outlook.com" || domain == "live.com") {
        m_host = "smtp-mail.outlook.com";
    } else if (domain == "yahoo.com") {
        m_host = "smtp.mail.yahoo.com";
    } else {
        m_host = "smtp." + domain;
    }
}

bool SmtpMailer::waitForCode(QSslSocket *sock, const QString &expectedCode,
                             QString &response, int timeoutMs)
{
    // A reply may span several lines (RFC 5321 §4.2.1): every line but the
    // last is "<code>-<text>", the last one is "<code><SP><text>". Reading
    // only up to the FIRST newline and testing that line desynchronises the
    // dialogue — the leftover line is then read as the answer to the NEXT
    // command, and the command after that fails with a misleading error.
    //
    // The reply therefore ends at the first line whose fourth character is
    // not a hyphen.
    QByteArray buffer;
    QElapsedTimer timer;
    timer.start();

    while (true) {
        const int remaining = timeoutMs - static_cast<int>(timer.elapsed());
        if (remaining <= 0) break;
        if (!sock->waitForReadyRead(remaining)) {
            buffer += sock->readAll();
            break;
        }
        buffer += sock->readAll();

        const QList<QByteArray> lines = buffer.split('\n');
        // The last element is the incomplete remainder (no newline yet): it
        // cannot be the final line of the reply.
        for (int i = 0; i < lines.size() - 1; ++i) {
            const QByteArray line = lines.at(i).trimmed();
            if (line.size() < 3) continue;
            if (line.size() > 3 && line.at(3) == '-') continue; // continuation
            response = QString::fromLatin1(buffer);
            const bool ok = line.startsWith(expectedCode.toLatin1());
            if (!ok)
                qWarning() << "[SMTP] Expected" << expectedCode << "got:" << response.trimmed();
            return ok;
        }
    }

    response = QString::fromLatin1(buffer);
    const bool ok = response.trimmed().startsWith(expectedCode);
    if (!ok) qWarning() << "[SMTP] Expected" << expectedCode << "got:" << response.trimmed();
    return ok;
}

bool SmtpMailer::sendCmd(QSslSocket *sock, const QString &cmd,
                         const QString &expectedCode, QString &response, int timeoutMs)
{
    response.clear();
    sock->write((cmd + "\r\n").toUtf8());
    sock->flush();
    return waitForCode(sock, expectedCode, response, timeoutMs);
}

bool SmtpMailer::sendMail(const QString &from, const QString &to,
                          const QString &subject, const QString &htmlBody,
                          QString &errorMsg)
{
    return sendMail(from, QStringList{to}, {}, {}, subject, htmlBody, {},
                    "3D-Reconstruction App", errorMsg);
}

bool SmtpMailer::connectAndAuthenticate(QSslSocket &sock, QString &errorMsg)
{
    if (!QSslSocket::supportsSsl()) {
        errorMsg = QString("TLS is not available. SSL library build: %1, runtime: %2. "
                           "Please ensure OpenSSL DLLs (libssl, libcrypto) are in the "
                           "application directory or PATH.")
                       .arg(QSslSocket::sslLibraryBuildVersionString(),
                            QSslSocket::sslLibraryVersionString());
        return false;
    }

    // Verify server certificates against system CAs — never downgrade to
    // VerifyNone: credentials leave the machine otherwise.
    sock.setPeerVerifyMode(QSslSocket::VerifyPeer);

    QString response;

    // Connect by hostname so SNI and certificate hostname verification both
    // use the real server name (connecting raw IPs defeats both).
    sock.connectToHost(m_host, m_port);

    if (!sock.waitForConnected(8000)) {
        errorMsg = QString("Cannot connect to %1:%2 - %3")
                       .arg(m_host).arg(m_port).arg(sock.errorString());
        return false;
    }

    if (!waitForCode(&sock, "220", response)) {
        errorMsg = "SMTP greeting failed: " + response;
        return false;
    }
    if (!sendCmd(&sock, "EHLO localhost", "250", response)) {
        errorMsg = "EHLO failed: " + response;
        return false;
    }
    if (!sendCmd(&sock, "STARTTLS", "220", response)) {
        errorMsg = "STARTTLS failed: " + response;
        return false;
    }
    sock.startClientEncryption();
    if (!sock.waitForEncrypted(8000)) {
        errorMsg = "TLS handshake failed: " + sock.errorString();
        return false;
    }
    if (!sendCmd(&sock, "EHLO localhost", "250", response)) {
        errorMsg = "EHLO after TLS failed: " + response;
        return false;
    }
    if (!sendCmd(&sock, "AUTH LOGIN", "334", response)) {
        errorMsg = "AUTH LOGIN failed: " + response;
        return false;
    }
    if (!sendCmd(&sock, QString::fromLatin1(m_username.toUtf8().toBase64()), "334", response)) {
        errorMsg = "Sending SMTP username failed: " + response;
        return false;
    }
    if (!sendCmd(&sock, QString::fromLatin1(m_password.toUtf8().toBase64()), "235", response)) {
        errorMsg = "SMTP authentication failed. Check the account password or app password.";
        return false;
    }
    return true;
}

bool SmtpMailer::testConnection(QString &errorMsg)
{
    mail::enforceBackgroundThread("SmtpMailer::testConnection");

    if (m_username.trimmed().isEmpty() || m_password.isEmpty()) {
        errorMsg = "Mail account is not configured.";
        return false;
    }

    QSslSocket sock;
    if (!connectAndAuthenticate(sock, errorMsg)) return false;

    QString response;
    sendCmd(&sock, "QUIT", "221", response);
    sock.disconnectFromHost();
    return true;
}

bool SmtpMailer::sendMail(const QString &from,
                          const QStringList &to,
                          const QStringList &cc,
                          const QStringList &bcc,
                          const QString &subject,
                          const QString &htmlBody,
                          const QStringList &attachmentPaths,
                          const QString &displayName,
                          QString &errorMsg)
{
    mail::enforceBackgroundThread("SmtpMailer::sendMail");

    // The recipient list must contain at least one *usable* address.  The
    // single-recipient overload wraps its argument into a list, so checking
    // the list size was not enough: an empty recipient slipped through and
    // the message was handed to the server without any RCPT TO.
    bool hasRecipient = false;
    for (const QString &recipient : to + cc + bcc) {
        if (!sanitizeHeader(recipient).isEmpty()) {
            hasRecipient = true;
            break;
        }
    }

    if (from.trimmed().isEmpty() || !hasRecipient) {
        errorMsg = "Missing sender or recipient.";
        return false;
    }

    QSslSocket sock;
    QString response;

    // The handshake is the same one testConnection() runs: one copy, so the
    // probe can never disagree with what sending actually does.
    if (!connectAndAuthenticate(sock, errorMsg)) return false;

    const QString cleanFrom = sanitizeHeader(from);
    if (!sendCmd(&sock, "MAIL FROM:<" + cleanFrom + ">", "250", response)) {
        errorMsg = "MAIL FROM failed: " + response;
        return false;
    }

    for (const QString &recipient : to + cc + bcc) {
        const QString cleanRecipient = sanitizeHeader(recipient);
        if (cleanRecipient.isEmpty()) continue;
        if (!sendCmd(&sock, "RCPT TO:<" + cleanRecipient + ">", "250", response)) {
            errorMsg = "RCPT TO failed: " + response;
            return false;
        }
    }

    if (!sendCmd(&sock, "DATA", "354", response)) {
        errorMsg = "DATA failed: " + response;
        return false;
    }

    QString message;
    QTextStream out(&message);
    const QString boundary = makeBoundary();
    const QString dateStr = QDateTime::currentDateTimeUtc().toString("ddd, dd MMM yyyy hh:mm:ss +0000");

    out << "From: " << encodedWord(displayName.isEmpty() ? "3D-Reconstruction App" : displayName)
        << " <" << cleanFrom << ">\r\n";
    out << "To: " << joinMailboxList(to) << "\r\n";
    if (!cc.isEmpty()) out << "Cc: " << joinMailboxList(cc) << "\r\n";
    out << "Subject: " << encodedWord(sanitizeHeader(subject)) << "\r\n";
    out << "Date: " << dateStr << "\r\n";
    out << "MIME-Version: 1.0\r\n";

    if (attachmentPaths.isEmpty()) {
        out << "Content-Type: text/html; charset=UTF-8\r\n";
        out << "Content-Transfer-Encoding: base64\r\n\r\n";
        out << wrapBase64(htmlBody.toUtf8());
    } else {
        out << "Content-Type: multipart/mixed; boundary=\"" << boundary << "\"\r\n\r\n";
        out << "--" << boundary << "\r\n";
        out << "Content-Type: text/html; charset=UTF-8\r\n";
        out << "Content-Transfer-Encoding: base64\r\n\r\n";
        out << wrapBase64(htmlBody.toUtf8()) << "\r\n";

        QMimeDatabase mimeDb;
        for (const QString &path : attachmentPaths) {
            QFile file(path);
            QFileInfo info(path);
            if (!file.open(QIODevice::ReadOnly)) {
                errorMsg = "Cannot read attachment: " + path;
                return false;
            }

            const QString fileName = sanitizeHeader(info.fileName());
            out << "--" << boundary << "\r\n";
            out << "Content-Type: " << mimeDb.mimeTypeForFile(info).name()
                << "; name=\"" << fileName << "\"\r\n";
            out << "Content-Transfer-Encoding: base64\r\n";
            out << "Content-Disposition: attachment; filename=\"" << fileName << "\"\r\n\r\n";
            out << wrapBase64(file.readAll()) << "\r\n";
        }
        out << "--" << boundary << "--\r\n";
    }
    out << "\r\n.";

    if (!sendCmd(&sock, message, "250", response)) {
        errorMsg = "Sending message content failed: " + response;
        return false;
    }

    sendCmd(&sock, "QUIT", "221", response);
    sock.disconnectFromHost();
    return true;
}
