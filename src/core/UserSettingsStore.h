#ifndef USER_SETTINGS_STORE_H
#define USER_SETTINGS_STORE_H

#include <QString>
#include <QStringList>

#include "Global.h"

class QSettings;

/**
 * @brief SMTP credentials and per-user preferences/secrets in the user INI.
 *
 * Not thread-safe by design: UserManager serialises every call with its
 * database mutex, so a preference update cannot race a user/licence write.
 *
 * **Two namespaces, two confidentiality levels.** A *preference* is a plain
 * setting (theme, grid visibility, mail server address) and is stored in the
 * clear. A *secret* (mail password, AI provider API key) is stored through
 * the OS vault (SecretVault / DPAPI) and must never reach the preference
 * namespace. The set of secret keys is declared in secretKeys() so the
 * distinction is enforced at the only place that knows about the disk: a
 * caller that mistakenly uses setUserPref() with a secret key still gets an
 * encrypted value, and the plaintext copy is retired.
 */
class APP_EXPORT UserSettingsStore {
public:
    void attachSettings(QSettings *settings) { m_settings = settings; }

    QString smtpSenderEmail() const;
    QString smtpSenderPassword() const;
    void setSmtpCredentials(const QString &email, const QString &password);

    QString getUserSecret(const QString &username, const QString &key,
                          const QString &defaultVal = {}) const;
    void setUserSecret(const QString &username, const QString &key, const QString &value);

    QString getUserPref(const QString &username, const QString &key,
                        const QString &defaultVal = {}) const;
    void setUserPref(const QString &username, const QString &key, const QString &value);

    /// The per-user keys whose values are secrets. They are always stored
    /// through the OS vault, whichever setter the caller uses.
    static const QStringList &secretKeys();

    /// True when @p key names a secret value.
    static bool isSecretKey(const QString &key);

    /// Move every legacy plaintext secret into the protected namespace and
    /// delete the plaintext copy: the per-user "pref_<key>" entries of the
    /// secret keys, plus the SMTP password. Returns the number of migrated
    /// values. A value that cannot be protected is left untouched (never
    /// deleted) so the user does not silently lose it.
    int migrateLegacySecrets();

private:
    /// Store @p value under "User_<username>/secret_<key>", encrypted, and
    /// retire any plaintext copy. Returns false when the value could not be
    /// protected (the stored value is then left untouched — never downgraded).
    bool storeSecret(const QString &username, const QString &key, const QString &value);

    /// Read "User_<username>/secret_<key>" and migrate a legacy plaintext
    /// preference of the same name into the protected namespace.
    QString readSecret(const QString &username, const QString &key,
                       const QString &defaultVal) const;

    QSettings *m_settings = nullptr;
};

#endif // USER_SETTINGS_STORE_H
