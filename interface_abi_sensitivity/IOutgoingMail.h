#ifndef IOUTGOINGMAIL_H
#define IOUTGOINGMAIL_H

#include <QString>

/**
 * @brief Transport-agnostic outbound-mail seam for core code.
 *
 * core/ must not depend on any concrete SMTP/IMAP transport (the transport
 * lives in services/mail). Core components that need to notify users by
 * email — e.g. UserManager's password-reset OTP — depend on this interface
 * instead. The composition root (AppShell) injects the concrete adapter
 * (SmtpOutbox) at startup; when no adapter is injected the operation fails
 * with a clear error rather than silently succeeding.
 */
class IOutgoingMail {
public:
    virtual ~IOutgoingMail() = default;

    /**
     * @brief Send a single HTML email using the application's outgoing account.
     * @param from        Sender address (must match the configured account).
     * @param to          Recipient address.
     * @param subject     Subject line (plain text, header-injection safe).
     * @param htmlBody    HTML body.
     * @param errorMsg    Output: human-readable error description on failure.
     * @return true on success.
     */
    virtual bool send(const QString &from,
                      const QString &to,
                      const QString &subject,
                      const QString &htmlBody,
                      QString &errorMsg) = 0;
};

#endif // IOUTGOINGMAIL_H
