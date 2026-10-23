#ifndef IUSERPROFILESERVICE_H
#define IUSERPROFILESERVICE_H

#include <QString>

#include "Global.h"
#include "UserTypes.h"

/**
 * @brief Segregated contract: the profile of an account.
 *
 * Adheres to the Interface Segregation Principle (ISP): a component that only
 * reads or writes the self-service profile fields depends on this contract,
 * not on the aggregate IUserManager.
 *
 * Concrete UserManager implements IUserManager (which inherits this
 * contract), and the composition root registers it under BOTH keys in
 * ServiceRegistry.
 */
class IUserProfileService {
public:
    /// ServiceRegistry lookup key (see ServiceRegistry.h).
    static constexpr const char *ServiceKey =
        "com.3dreconstruction.service.IUserProfileService";

    virtual ~IUserProfileService() = default;

    /// The account of the current session.
    virtual UserInfo currentUser() const = 0;

    /// Write the self-service profile fields (e-mail, avatar) of @p username.
    ///
    /// Only the account itself or an administrator may write, and only these
    /// two fields: role, licence key, licence status and expiry are
    /// administrator state and are never written through this path, so a
    /// profile update can never carry a privilege change.
    virtual bool updateProfile(const QString &username, const QString &email,
                                const QString &avatarPath, QString &error,
                                AuthError *code = nullptr) = 0;
};

#endif // IUSERPROFILESERVICE_H
