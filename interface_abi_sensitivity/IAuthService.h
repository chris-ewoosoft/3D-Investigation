#ifndef IAUTHSERVICE_H
#define IAUTHSERVICE_H

#include <QString>
#include <QStringList>

#include "Global.h"
#include "UserTypes.h"

/**
 * @brief Segregated contract: who is signed in, and what that session may do.
 *
 * Adheres to the Interface Segregation Principle (ISP): a component that only
 * needs the current identity (a plugin showing "signed in as X") depends on
 * this contract, not on the aggregate IUserManager — which also exposes user
 * administration, licence activation and SMTP credentials.
 *
 * The password/OTP surface is part of it because it is the same capability:
 * proving and changing the credential of the current account.
 *
 * Concrete UserManager implements IUserManager (which inherits this
 * contract), and the composition root registers it under BOTH keys in
 * ServiceRegistry.
 */
class IAuthService {
public:
    /// ServiceRegistry lookup key (see ServiceRegistry.h).
    static constexpr const char *ServiceKey =
        "com.3dreconstruction.service.IAuthService";

    virtual ~IAuthService() = default;

    // ── Session ───────────────────────────────────────────────────────────────
    virtual bool login(const QString &usernameOrEmail, const QString &password,
                       QString &errorMsg, AuthError *code = nullptr) = 0;
    virtual void logout() = 0;
    virtual bool isLoggedIn() const = 0;
    virtual QString currentUsername() const = 0;

    /// Whether the current session holds the administrator role.
    ///
    /// The decision lives in the core, not in the UI: every holder of this
    /// service — including any plugin — is subject to the same check, so a
    /// presentation-layer gate can never be the only one.
    virtual bool isAdmin() const = 0;

    virtual void setRememberMe(bool enable, const QString &username) = 0;
    virtual bool isRememberMeEnabled() const = 0;
    virtual QString savedUsername() const = 0;

    // ── Password management ───────────────────────────────────────────────────
    virtual bool changePassword(const QString &username, const QString &oldPass,
                                const QString &newPass, QString &error,
                                AuthError *code = nullptr) = 0;
    /// Reset the password of the account owning @p email.
    ///
    /// Requires a prior successful verifyOtp(email, otp): the proof of
    /// ownership is recorded by the core and consumed by this call, so a
    /// reset cannot be replayed and no holder of the service can reset a
    /// password without having proven ownership first. The new password is
    /// subject to the same policy as changePassword().
    virtual bool resetPassword(const QString &email, const QString &newPass,
                                QString &error, AuthError *code = nullptr) = 0;

    // ── OTP ───────────────────────────────────────────────────────────────────
    virtual bool sendPasswordResetOtp(const QString &email, QString &error) = 0;
    virtual bool verifyOtp(const QString &email, const QString &otp) = 0;
    virtual QString findUsernameByEmail(const QString &email) const = 0;

    // ── Store lifecycle ───────────────────────────────────────────────────────
    virtual bool loadConfig(const QString &configPath) = 0;
    virtual void saveConfig() = 0;
    virtual QString configPath() const = 0;
};

#endif // IAUTHSERVICE_H
