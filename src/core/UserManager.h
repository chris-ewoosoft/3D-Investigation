#ifndef USER_MANAGER_H
#define USER_MANAGER_H

#include "IUserManager.h"
#include "LicenseKeyLedger.h"
#include "OtpStore.h"
#include "UserAccountStore.h"
#include "UserSettingsStore.h"

#include <QObject>
#include <QDateTime>
#include <QHash>
#include <shared_mutex>

#include "Global.h"

/**
 * @brief Scoped implementation of the segregated user-service contracts.
 *
 * AppShell owns one instance for the application lifetime and registers the
 * same object under each narrow interface. Production consumers resolve the
 * capability they need from ServiceRegistry; the private instance() helper is
 * retained only for isolated legacy test fixtures.
 */
class AppShell;
class QSettings;
class TestUserManagerCrypto; // regex test access
class TestMailCredentialLifecycle; // credential lifecycle test access
class TestSmtpOutbox; // SMTP outbox test access

class APP_EXPORT UserManager : public QObject, public IUserManager {
    Q_OBJECT
public:
    explicit UserManager(QObject *parent = nullptr);
    ~UserManager() override = default;
    void setOutgoingMail(class IOutgoingMail *outbox) { m_outgoingMail = outbox; }


private:
    /// Legacy test seam: preserved only for test fixtures that exercise
    /// UserManager without building an AppShell composition root.
    /// Production code never uses this: UserManager is instantiated and owned
    /// by AppShell and registered in ServiceRegistry.
    static UserManager* instance();
    friend class TestUserManagerCrypto;
    friend class TestMailCredentialLifecycle;
    friend class TestSmtpOutbox;

    // ── IUserManager ──────────────────────────────────────────────────────────
    bool loadConfig(const QString &configPath) override;
    void saveConfig() override;

    // Auth
    bool login(const QString &usernameOrEmail, const QString &password, QString &errorMsg,
               AuthError *code = nullptr) override;
    void logout() override;
    bool isLoggedIn() const override;
    UserInfo currentUser() const override;
    QString currentUsername() const override;
    bool isAdmin() const override;

    void setRememberMe(bool enable, const QString &username) override;
    bool isRememberMeEnabled() const override;
    QString savedUsername() const override;

    // Password management
    bool changePassword(const QString &username, const QString &oldPass,
                        const QString &newPass, QString &error,
                        AuthError *code = nullptr) override;
    bool resetPassword(const QString &email, const QString &newPass,
                        QString &error, AuthError *code = nullptr) override;
    bool updateProfile(const QString &username, const QString &email,
                        const QString &avatarPath, QString &error,
                        AuthError *code = nullptr) override;

    // OTP
    bool sendPasswordResetOtp(const QString &email, QString &error) override;
    bool verifyOtp(const QString &email, const QString &otp) override;
    QString findUsernameByEmail(const QString &email) const override;

    // License
    QString licenseStatusStr(const QString &username) const override;
    int     daysRemaining(const QString &username) const override;
    bool    needsActivation(const QString &username) const override;
    bool    isLicenseExpired(const QString &username) const override;
    bool    activateTrial(const QString &username, QString &error,
                          AuthError *code = nullptr) override;
    bool    activateLicenseKey(const QString &username, const QString &key, QString &error,
                               AuthError *code = nullptr) override;

    // Admin – users
    QStringList allUsernames(AuthError *code = nullptr) const override;
    UserInfo    userInfo(const QString &username, AuthError *code = nullptr) const override;
    bool addUser(const UserInfo &info, const QString &plainPassword, QString &error,
                 AuthError *code = nullptr) override;
    bool updateUser(const UserInfo &info, QString &error,
                    AuthError *code = nullptr) override;
    bool deleteUser(const QString &username, QString &error,
                    AuthError *code = nullptr) override;

    // Admin – license keys
    QString     generateLicenseKey(AuthError *code = nullptr) override;
    QStringList availableLicenseKeys(AuthError *code = nullptr) const override;
    bool        revokeLicenseKey(const QString &key, QString &error,
                                 AuthError *code = nullptr) override;

    // SMTP config
    QString smtpSenderEmail() const override;
    QString smtpSenderPassword() const override;
    void    setSmtpCredentials(const QString &email, const QString &password) override;

    QString configPath() const override { return m_configPath; }


    // Per-user preferences (lưu vào [User_<username>] section của Config_User.ini)
    QString getUserPref(const QString &username, const QString &key, const QString &defaultVal = {}) const override;
    void    setUserPref(const QString &username, const QString &key, const QString &value) override;

    // Per-user credentials — stored through SecretVault, never as plaintext.
    QString getUserSecret(const QString &username, const QString &key, const QString &defaultVal = {}) const override;
    void    setUserSecret(const QString &username, const QString &key, const QString &value) override;

private:

    /// Whether the current session holds the administrator role.
    /// The caller must hold m_mutex.
    bool isAdminLocked() const;

    /// Refuse an administrator-only operation when the session is not an
    /// administrator. Writes the error message and the AuthError code.
    /// The caller must hold m_mutex.
    bool refuseUnlessAdmin(QString &error, AuthError *code) const;

    mutable std::shared_mutex m_mutex;
    QString m_configPath;
    QSettings *m_settings = nullptr;
    QString    m_currentUsername;

    UserAccountStore  m_accounts;
    LicenseKeyLedger  m_licenseLedger;
    OtpStore          m_otpStore;
    UserSettingsStore m_userSettings;

    /// Proofs of ownership granted by a successful verifyOtp(): e-mail (lower
    /// case) -> expiry. A reset consumes its proof, so a single OTP can change
    /// a password exactly once and no holder of this service can reset a
    /// password without having proven ownership first.
    QHash<QString, QDateTime> m_authorizedResets;

    IOutgoingMail *m_outgoingMail = nullptr; ///< injected, not owned
};

#endif // USER_MANAGER_H
