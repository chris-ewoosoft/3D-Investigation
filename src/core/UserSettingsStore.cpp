#include "UserSettingsStore.h"

#include "SecretVault.h"

#include <QDebug>
#include <QSettings>

const QStringList &UserSettingsStore::secretKeys()
{
    // Every key whose value must never be readable in the INI file. Keep this
    // list in sync with the callers that read the value back.
    static const QStringList keys = {
        QStringLiteral("mail_password"),
        QStringLiteral("ai_api_key"),
    };
    return keys;
}

bool UserSettingsStore::isSecretKey(const QString &key)
{
    return secretKeys().contains(key);
}

QString UserSettingsStore::smtpSenderEmail() const
{
    return m_settings ? m_settings->value("SMTP/sender_email").toString() : QString();
}

QString UserSettingsStore::smtpSenderPassword() const
{
    if (!m_settings) return {};
    // Values written by older builds are legacy plaintext; the vault passes
    // them through unchanged so the next save() migrates them into "enc1:".
    return SecretVault::decrypt(m_settings->value("SMTP/sender_password").toString());
}

void UserSettingsStore::setSmtpCredentials(const QString &email, const QString &password)
{
    if (!m_settings) return;
    m_settings->setValue("SMTP/sender_email", email);
    if (password.isEmpty()) {
        m_settings->remove("SMTP/sender_password");
        m_settings->sync();
        return;
    }
    const QString sealed = SecretVault::encrypt(password);
    if (sealed.isEmpty()) {
        // The OS refused to protect the value. Fail closed: keep whatever is
        // stored instead of writing the password in the clear.
        qWarning() << "UserSettingsStore: the SMTP password could not be protected; "
                      "the stored value was left untouched.";
        m_settings->sync();
        return;
    }
    m_settings->setValue("SMTP/sender_password", sealed);
    m_settings->sync();
}

QString UserSettingsStore::readSecret(const QString &username, const QString &key,
                                      const QString &defaultVal) const
{
    if (!m_settings || username.isEmpty() || key.isEmpty()) return defaultVal;
    const QString value = m_settings->value("User_" + username + "/secret_" + key).toString();
    if (!value.isEmpty()) {
        const QString secret = SecretVault::decrypt(value);
        if (!secret.isEmpty()) return secret;
        // The entry exists but cannot be read (other user/machine, tampered
        // blob). Falling back to the legacy plaintext copy would silently
        // resurrect an unprotected value — report the default instead.
        qWarning() << "UserSettingsStore: the stored secret '" << key
                   << "' could not be decrypted; it is not replaced by a plaintext fallback.";
        return defaultVal;
    }
    // Legacy plaintext preference written before secret storage existed;
    // migrated into "secret_<key>" the next time setUserSecret() runs.
    return m_settings->value("User_" + username + "/pref_" + key, defaultVal).toString();
}

QString UserSettingsStore::getUserSecret(const QString &username, const QString &key,
                                         const QString &defaultVal) const
{
    return readSecret(username, key, defaultVal);
}

bool UserSettingsStore::storeSecret(const QString &username, const QString &key,
                                    const QString &value)
{
    if (!m_settings || username.isEmpty() || key.isEmpty()) return false;
    if (value.isEmpty()) {
        m_settings->remove("User_" + username + "/secret_" + key);
        m_settings->remove("User_" + username + "/pref_" + key);
        m_settings->sync();
        return true;
    }
    const QString sealed = SecretVault::encrypt(value);
    if (sealed.isEmpty()) {
        // Fail closed: the previous value stays on disk untouched. Writing
        // the new one in the clear would be a silent downgrade.
        qWarning() << "UserSettingsStore: the secret '" << key
                   << "' could not be protected; the stored value was left untouched.";
        return false;
    }
    m_settings->setValue("User_" + username + "/secret_" + key, sealed);
    // Retire any legacy plaintext copy of the same secret.
    m_settings->remove("User_" + username + "/pref_" + key);
    m_settings->sync();
    return true;
}

void UserSettingsStore::setUserSecret(const QString &username, const QString &key,
                                      const QString &value)
{
    storeSecret(username, key, value);
}

QString UserSettingsStore::getUserPref(const QString &username, const QString &key,
                                       const QString &defaultVal) const
{
    if (!m_settings || username.isEmpty()) return defaultVal;
    // A secret key never lives in the preference namespace: read it from the
    // protected one so a caller that uses the "pref" API for a secret still
    // gets the value back.
    if (isSecretKey(key)) return readSecret(username, key, defaultVal);
    return m_settings->value("User_" + username + "/pref_" + key, defaultVal).toString();
}

void UserSettingsStore::setUserPref(const QString &username, const QString &key,
                                    const QString &value)
{
    if (!m_settings || username.isEmpty()) return;
    // A secret key must be encrypted, whatever the caller asked for: route it
    // to the protected namespace instead of the plaintext preference one.
    if (isSecretKey(key)) {
        storeSecret(username, key, value);
        return;
    }
    m_settings->setValue("User_" + username + "/pref_" + key, value);
    m_settings->sync();
}

int UserSettingsStore::migrateLegacySecrets()
{
    if (!m_settings) return 0;
    int migrated = 0;

    // The SMTP password used to be stored in the clear.
    const QString smtpPassword = m_settings->value("SMTP/sender_password").toString();
    if (!smtpPassword.isEmpty() && SecretVault::isUnprotected(smtpPassword)) {
        const QString sealed = SecretVault::encrypt(smtpPassword);
        if (sealed.isEmpty()) {
            qWarning() << "UserSettingsStore: the legacy SMTP password could not be protected; "
                          "the stored value was left untouched.";
        } else {
            m_settings->setValue("SMTP/sender_password", sealed);
            ++migrated;
        }
    }

    // Per-user secrets that older builds wrote as a plain preference.
    const QStringList allKeys = m_settings->allKeys();
    for (const QString &fullKey : allKeys) {
        const int separator = fullKey.indexOf('/');
        if (separator <= 0) continue;
        const QString section = fullKey.left(separator);
        const QString leaf = fullKey.mid(separator + 1);
        if (!section.startsWith(QStringLiteral("User_"))) continue;
        if (!leaf.startsWith(QStringLiteral("pref_"))) continue;
        const QString key = leaf.mid(QStringLiteral("pref_").size());
        if (!isSecretKey(key)) continue;
        const QString username = section.mid(QStringLiteral("User_").size());
        const QString legacy = m_settings->value(fullKey).toString();
        if (legacy.isEmpty()) {
            m_settings->remove(fullKey);
            continue;
        }
        const QString sealed = SecretVault::encrypt(legacy);
        if (sealed.isEmpty()) {
            // Fail closed: keep the plaintext copy rather than losing the
            // value — it is still protected by the file permissions and the
            // user can re-enter it.
            qWarning() << "UserSettingsStore: the legacy secret '" << key
                       << "' could not be protected; the stored value was left untouched.";
            continue;
        }
        m_settings->setValue("User_" + username + "/secret_" + key, sealed);
        m_settings->remove(fullKey);
        ++migrated;
    }

    if (migrated > 0) m_settings->sync();
    return migrated;
}
