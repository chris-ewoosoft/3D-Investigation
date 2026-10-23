#ifndef USERTYPES_H
#define USERTYPES_H

#include <QDate>
#include <QString>

#include "Global.h"

/**
 * @brief The value types shared by the user-service interfaces.
 *
 * They live in their own header so that the narrow interfaces (IAuthService,
 * IUserProfileService, IUserAdminService, ISmtpCredentialService) can talk
 * about a user, a role and a failure code without including the aggregate
 * IUserManager — which would make the segregation circular.
 */

enum class UserRole { Admin, User };

/**
 * @brief Machine-readable failure codes for the auth/license operations.
 *
 * Services set both this code and a human-readable Vietnamese message; UI code
 * should branch on the code (never on message text) and fall back to the
 * message for AuthError::Unknown.
 */
enum class AuthError {
    None,
    UserNotFound,
    WrongPassword,
    WeakPassword,
    UserExists,
    InvalidKey,
    KeyAlreadyUsed,
    AlreadyActivated,
    ResetNotAuthorized, ///< the reset was not preceded by a valid OTP proof
    NotAuthorized,     ///< the caller lacks the role required for the operation
    Unknown
};

struct APP_EXPORT UserInfo {
    QString username;
    QString email;
    UserRole role = UserRole::User;
    QString licenseKey;
    QString licenseStatus; // "None", "Trial", "Activated"
    QDate   licenseExpiry;
    QDate   trialStart;
    QString avatarPath;
};

/// Translation key for a code; empty for None/Unknown (callers then keep the
/// service-provided message).
inline QString authErrorKey(AuthError code)
{
    switch (code) {
    case AuthError::UserNotFound:     return QStringLiteral("auth.no_user");
    case AuthError::WrongPassword:    return QStringLiteral("auth.wrong_pass");
    case AuthError::WeakPassword:     return QStringLiteral("changepw.err_length");
    case AuthError::UserExists:        return QStringLiteral("auth.user_exists");
    case AuthError::InvalidKey:        return QStringLiteral("license.err_invalid_key");
    case AuthError::KeyAlreadyUsed:    return QStringLiteral("license.err_used_key");
    case AuthError::AlreadyActivated:  return QStringLiteral("license.err_already_activated");
    case AuthError::ResetNotAuthorized: return QStringLiteral("auth.reset_not_authorized");
    case AuthError::NotAuthorized:     return QStringLiteral("auth.not_authorized");
    case AuthError::None:
    case AuthError::Unknown:          break;
    }
    return QString();
}

#endif // USERTYPES_H
