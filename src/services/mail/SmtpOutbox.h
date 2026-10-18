#ifndef SMTPOUTBOX_H
#define SMTPOUTBOX_H

#include "IOutgoingMail.h"
#include "Global.h"

class IUserManager;

/**
 * @brief IOutgoingMail adapter over SmtpMailer, backed by the credentials
 *        stored in IUserManager (composition-root injected).
 *
 * Lives in services/mail so that core code never links against the SMTP
 * transport directly.
 */
class APP_EXPORT SmtpOutbox final : public IOutgoingMail {
public:
    explicit SmtpOutbox(IUserManager *users);

    bool send(const QString &from,
              const QString &to,
              const QString &subject,
              const QString &htmlBody,
              QString &errorMsg) override;

private:
    IUserManager *m_users; ///< not owned; must outlive this adapter
};

#endif // SMTPOUTBOX_H
