#include "LicenseKeyLedger.h"

#include "CryptoUtils.h"
#include "SecretVault.h"

#include <QSettings>
#include <QRandomGenerator>
#include <QDebug>

void LicenseKeyLedger::clear()
{
    m_keys.clear();
    m_owners.clear();
    m_storedBlobs.clear();
    m_opaqueBlobs.clear();
}

void LicenseKeyLedger::load()
{
    clear();
    if (!m_settings) return;

    const QStringList storedKeys = m_settings->value("LicenseKeys/keys").toStringList();
    for (const QString &stored : storedKeys) {
        // Values may be DPAPI-encrypted (enc1:...) or legacy plaintext; the
        // vault passes the latter through so old configs migrate transparently.
        const QString key = SecretVault::decrypt(stored);
        if (key.isEmpty()) {
            // Undecryptable (other user/machine, tampered blob). Keep the raw
            // blob so a later save() does not drop the key from the inventory,
            // and report it instead of pretending the inventory is complete.
            qWarning() << "LicenseKeyLedger: a stored license key could not be decrypted; "
                          "it is preserved on disk but cannot be used.";
            m_opaqueBlobs.append(stored);
            continue;
        }
        // Subkey path uses a hash so the plaintext key never appears in the INI.
        const QString bucket = CryptoUtils::sha256Hex(key);
        bool used = m_settings->value("LicenseKeys/" + bucket + "_used", false).toBool();
        QString keyOwner = m_settings->value("LicenseKeys/" + bucket + "_user").toString();
        // Legacy format used the plaintext key directly in the path — migrate it.
        if (!m_settings->contains("LicenseKeys/" + bucket + "_used") &&
            m_settings->contains("LicenseKeys/" + key + "_used")) {
            used = m_settings->value("LicenseKeys/" + key + "_used", false).toBool();
            keyOwner = m_settings->value("LicenseKeys/" + key + "_user").toString();
            m_settings->remove("LicenseKeys/" + key + "_used");
            m_settings->remove("LicenseKeys/" + key + "_user");
        }
        m_keys[key] = used;
        if (!keyOwner.isEmpty()) m_owners[key] = keyOwner;
        // Remember the protected blob so a later save() can keep it if the OS
        // protection call is temporarily unavailable.
        if (SecretVault::protectionOf(stored) == SecretVault::Protection::Protected)
            m_storedBlobs[key] = stored;
    }
}

void LicenseKeyLedger::save()
{
    if (!m_settings) return;

    // The blobs that could not be decrypted are preserved verbatim: a save
    // must never destroy a key it merely failed to read.
    QStringList storedKeys = m_opaqueBlobs;
    const QStringList keys = m_keys.keys();
    for (const QString &key : keys) {
        // Store DPAPI-encrypted; the path bucket is a hash of the key.
        const QString sealed = SecretVault::encrypt(key);
        if (sealed.isEmpty()) {
            // Fail closed: never write a license key in the clear. Reuse the
            // previously protected blob when there is one, so the inventory
            // keeps the key instead of losing it.
            const QString previous = m_storedBlobs.value(key);
            if (previous.isEmpty()) {
                qWarning() << "LicenseKeyLedger: a license key could not be protected and no "
                              "previously protected copy exists; it is NOT written in the clear "
                              "and must be issued again.";
            } else {
                storedKeys.append(previous);
            }
        } else {
            storedKeys.append(sealed);
            m_storedBlobs[key] = sealed;
        }
        const QString bucket = CryptoUtils::sha256Hex(key);
        m_settings->setValue("LicenseKeys/" + bucket + "_used", m_keys[key]);
        m_settings->setValue("LicenseKeys/" + bucket + "_user", m_owners.value(key));
    }
    m_settings->setValue("LicenseKeys/keys", storedKeys);
}

QString LicenseKeyLedger::generate()
{
    const QString chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"; // exclude ambiguous 0,1,I,O
    QString key;
    for (int group = 0; group < 4; ++group) {
        for (int i = 0; i < 5; ++i)
            key += chars[QRandomGenerator::system()->bounded(chars.size())];
        if (group < 3) key += '-';
    }
    m_keys[key] = false;
    return key;
}

QStringList LicenseKeyLedger::available() const
{
    return m_keys.keys();
}

bool LicenseKeyLedger::contains(const QString &key) const
{
    return m_keys.contains(key);
}

bool LicenseKeyLedger::isUsed(const QString &key) const
{
    return m_keys.value(key, false);
}

QString LicenseKeyLedger::owner(const QString &key) const
{
    return m_owners.value(key);
}

bool LicenseKeyLedger::claim(const QString &key, const QString &username)
{
    if (!m_keys.contains(key) || m_keys.value(key)) return false;
    m_keys[key] = true;
    m_owners[key] = username;
    return true;
}

bool LicenseKeyLedger::release(const QString &key, QString &previousOwner)
{
    if (!m_keys.contains(key)) return false;

    previousOwner = m_owners.take(key);
    m_keys.remove(key);

    if (m_settings) {
        const QString bucket = CryptoUtils::sha256Hex(key);
        m_settings->remove("LicenseKeys/" + bucket + "_used");
        m_settings->remove("LicenseKeys/" + bucket + "_user");
        // Legacy plaintext-path entries (pre-encryption configs)
        m_settings->remove("LicenseKeys/" + key + "_used");
        m_settings->remove("LicenseKeys/" + key + "_user");
    }
    return true;
}
