#ifndef IUSERADMINSERVICE_H
#define IUSERADMINSERVICE_H

#include <QString>
#include <QStringList>

#include "Global.h"
#include "UserTypes.h"

/**
 * @brief Segregated contract: administering the accounts of the installation.
 *
 * Adheres to the Interface Segregation Principle (ISP): the administrator
 * surface is its own capability, separate from signing in and from the
 * profile of the current session.
 *
 * Every operation below is administrator-only and is authorised in the core:
 * an unauthenticated or non-admin caller is refused with
 * AuthError::NotAuthorized. Reads (@c userInfo) are additionally allowed for
 * an account reading its own record.
 *
 * Concrete UserManager implements IUserManager (which inherits this
 * contract), and the composition root registers it under BOTH keys in
 * ServiceRegistry.
 */
class IUserAdminService {
public:
    /// ServiceRegistry lookup key (see ServiceRegistry.h).
    static constexpr const char *ServiceKey =
        "com.3dreconstruction.service.IUserAdminService";

    virtual ~IUserAdminService() = default;

    virtual QStringList allUsernames(AuthError *code = nullptr) const = 0;
    virtual UserInfo    userInfo(const QString &username, AuthError *code = nullptr) const = 0;
    virtual bool addUser(const UserInfo &info, const QString &plainPassword,
                         QString &error, AuthError *code = nullptr) = 0;
    virtual bool updateUser(const UserInfo &info, QString &error,
                            AuthError *code = nullptr) = 0;
    virtual bool deleteUser(const QString &username, QString &error,
                            AuthError *code = nullptr) = 0;
};

#endif // IUSERADMINSERVICE_H
