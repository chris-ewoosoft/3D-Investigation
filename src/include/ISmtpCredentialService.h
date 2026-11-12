#ifndef ISMTPCREDENTIALSERVICE_H
#define ISMTPCREDENTIALSERVICE_H

#include <QString>

#include "Global.h"

/**
 * @brief Segregated contract: the credentials the outgoing mail uses.
 *
 * Adheres to the Interface Segregation Principle (ISP): a component that only
 * reads or writes the SMTP sender credentials depends on this contract, not
 * on the aggregate IUserManager.
 *
 * The password is a SECRET: it is stored through the DPAPI-backed vault
 * (SecretVault), never in the clear — see IUserPreferences for the secret
 * namespace.
 *
 * Concrete UserManager implements IUserManager (which inherits this
 * contract), and the composition root registers it under BOTH keys in
 * ServiceRegistry.
 */
class ISmtpCredentialService {
public:
    /// ServiceRegistry lookup key (see ServiceRegistry.h).
    static constexpr const char *ServiceKey =
        "com.3dreconstruction.service.ISmtpCredentialService";

    virtual ~ISmtpCredentialService() = default;

    virtual QString smtpSenderEmail() const = 0;
    virtual QString smtpSenderPassword() const = 0;
    virtual void    setSmtpCredentials(const QString &email, const QString &password) = 0;
};

#endif // ISMTPCREDENTIALSERVICE_H
