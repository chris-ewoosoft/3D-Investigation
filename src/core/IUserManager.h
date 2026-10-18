#ifndef IUSERMANAGER_H
#define IUSERMANAGER_H

#include <QDate>
#include <QString>
#include <QStringList>
#include "Global.h"

/**
 * @brief Contract for authentication, OTP, licensing and mail-credential
 *        storage. Registered in ServiceRegistry so consumers can depend on
 *        this interface instead of the concrete singleton.
 *
 * The concrete implementation (UserManager) is a QApplication-lifetime
 * singleton; plugins may still resolve it through:
 *   auto* users = ctx->services()->get<IUserManager>();
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
    Unknown
};

// Translation key for a code; empty for None/Unknown (callers then keep the
// service-provided message).
static inline QString authErrorKey(AuthError code)
{
    switch (code) {
    case AuthError::UserNotFound:     return QStringLiteral("auth.no_user");
    case AuthError::WrongPassword:    return QStringLiteral("auth.wrong_pass");
    case AuthError::WeakPassword:     return QStringLiteral("changepw.err_length");
    case AuthError::UserExists:        return QStringLiteral("auth.user_exists");
    case AuthError::InvalidKey:        return QStringLiteral("license.err_invalid_key");
    case AuthError::KeyAlreadyUsed:    return QStringLiteral("license.err_used_key");
    case AuthError::AlreadyActivated:  return QStringLiteral("license.err_already_activated");
    case AuthError::None:
    case AuthError::Unknown:          break;
    }
    return QString();
}

struct APP_EXPORT UserInfo {
    QString username;
    QString passwordHash;
    QString email;
    UserRole role = UserRole::User;
    QString licenseKey;
    QString licenseStatus; // "None", "Trial", "Activated"
    QDate   licenseExpiry;
    QDate   trialStart;
    QString avatarPath;
};

class IUserManager {
public:
    /// ServiceRegistry lookup key (see ServiceRegistry.h).
    static constexpr const char *ServiceKey =
        "com.3dreconstruction.service.IUserManager";

    virtual ~IUserManager() = default;

    /// Translation key for a code; empty for None/Unknown (callers then keep
    /// the service-provided message).
    static QString authErrorKey(AuthError code) {
        switch (code) {
        case AuthError::UserNotFound:     return QStringLiteral("auth.no_user");
        case AuthError::WrongPassword:    return QStringLiteral("auth.wrong_pass");
        case AuthError::WeakPassword:     return QStringLiteral("changepw.err_length");
        case AuthError::UserExists:        return QStringLiteral("auth.user_exists");
        case AuthError::InvalidKey:        return QStringLiteral("license.err_invalid_key");
        case AuthError::KeyAlreadyUsed:    return QStringLiteral("license.err_used_key");
        case AuthError::AlreadyActivated:  return QStringLiteral("license.err_already_activated");
        case AuthError::None:
        case AuthError::Unknown:          break;
        }
        return QString();
    }

    // ── Config lifecycle ──────────────────────────────────────────────────────
    virtual bool loadConfig(const QString &configPath) = 0;
    virtual void saveConfig() = 0;

    // ── Auth ──────────────────────────────────────────────────────────────────
    virtual bool login(const QString &usernameOrEmail, const QString &password,
                       QString &errorMsg, AuthError *code = nullptr) = 0;
    virtual void logout() = 0;
    virtual bool isLoggedIn() const = 0;
    virtual UserInfo currentUser() const = 0;
    virtual QString currentUsername() const = 0;

    virtual void setRememberMe(bool enable, const QString &username) = 0;
    virtual bool isRememberMeEnabled() const = 0;
    virtual QString savedUsername() const = 0;

    // ── Password management ──────────────────────────────────────────────────
    virtual bool changePassword(const QString &username, const QString &oldPass,
                                const QString &newPass, QString &error,
                                AuthError *code = nullptr) = 0;
    virtual bool resetPassword(const QString &username, const QString &newPass) = 0;
    virtual void updateAvatar(const QString &username, const QString &path) = 0;

    // ── OTP ──────────────────────────────────────────────────────────────────
    virtual bool sendPasswordResetOtp(const QString &email, QString &error) = 0;
    virtual bool verifyOtp(const QString &email, const QString &otp) = 0;
    virtual QString findUsernameByEmail(const QString &email) const = 0;

    // ── License ──────────────────────────────────────────────────────────────
    virtual QString licenseStatusStr(const QString &username) const = 0;
    virtual int     daysRemaining(const QString &username) const = 0;
    virtual bool    needsActivation(const QString &username) const = 0;
    virtual bool    isLicenseExpired(const QString &username) const = 0;
    virtual bool    activateTrial(const QString &username, QString &error,
                               AuthError *code = nullptr) = 0;
    virtual bool    activateLicenseKey(const QString &username, const QString &key,
                                    QString &error,
                                    AuthError *code = nullptr) = 0;

    // ── Admin – users ────────────────────────────────────────────────────────
    virtual QStringList allUsernames() const = 0;
    virtual UserInfo    userInfo(const QString &username) const = 0;
    virtual bool addUser(const UserInfo &info, const QString &plainPassword,
                         QString &error, AuthError *code = nullptr) = 0;
    virtual bool updateUser(const UserInfo &info, QString &error) = 0;
    virtual bool deleteUser(const QString &username, QString &error) = 0;

    // ── Admin – license keys ─────────────────────────────────────────────────
    virtual QString     generateLicenseKey() = 0;
    virtual QStringList availableLicenseKeys() const = 0;
    virtual bool        revokeLicenseKey(const QString &key, QString &error) = 0;

    // ── SMTP configuration ───────────────────────────────────────────────────
    virtual QString smtpSenderEmail() const = 0;
    virtual QString smtpSenderPassword() const = 0;
    virtual void    setSmtpCredentials(const QString &email, const QString &password) = 0;

    virtual QString configPath() const = 0;

    // ── Per-user storage ─────────────────────────────────────────────────────
    virtual QString getUserPref(const QString &username, const QString &key, const QString &defaultVal = {}) const = 0;
    virtual void    setUserPref(const QString &username, const QString &key, const QString &value) = 0;
    virtual QString getUserSecret(const QString &username, const QString &key, const QString &defaultVal = {}) const = 0;
    virtual void    setUserSecret(const QString &username, const QString &key, const QString &value) = 0;
};

#endif // IUSERMANAGER_H
