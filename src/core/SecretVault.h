#ifndef SECRETVAULT_H
#define SECRETVAULT_H

#include <QString>
#include "Global.h"

/**
 * @brief OS-backed vault for plaintext secrets (mail/SMTP credentials).
 *
 * Stored format in INI files:
 *   "enc1:<base64>"  — OS-protected blob (DPAPI CurrentUser on Windows),
 *                      decryptable only by this user on this machine.
 *   "plain:<value>"  — explicitly unencrypted fallback, only produced on a
 *                      platform that has NO OS backend. The marker makes the
 *                      downgrade visible to every reader.
 *   anything else    — legacy pre-vault plaintext; decrypt() returns it as-is
 *                      so readers transparently migrate old Config.ini files.
 *
 * **Fail-closed contract.** On a platform that HAS an OS backend, a failing
 * protection call must never degrade into a plaintext write: encrypt() then
 * returns an empty string and the caller keeps the value it already has
 * instead of downgrading the secret on disk. A transient DPAPI failure
 * (profile not loaded, service session) therefore costs the user a re-entry,
 * not the confidentiality of the secret.
 *
 * On a platform WITHOUT an OS backend there is nothing to fail over to, so
 * the value is stored marked as "plain:" — a documented platform limit, not a
 * silent fallback.
 */
class APP_EXPORT SecretVault {
public:
    /// How a stored value is protected.
    enum class Protection {
        Empty,          ///< no value stored
        Protected,      ///< "enc1:…" — OS-protected blob
        PlaintextMarked, ///< "plain:…" — explicitly unprotected (no OS backend)
        LegacyPlaintext  ///< no marker — pre-vault value
    };

    /// Protect a value for storage. Returns a self-describing string.
    ///
    /// Fails closed: when the OS protection call fails on a platform that has
    /// a backend the returned string is EMPTY (the caller must keep the value
    /// it already has rather than writing cleartext). On a platform without a
    /// backend the value is returned marked as "plain:".
    static QString encrypt(const QString &plainValue);

    /// Recover the original value from anything encrypt() produced, or a
    /// legacy plaintext value.
    static QString decrypt(const QString &storedValue);

    /// Classify a stored value, so a caller can tell a protected blob from an
    /// explicitly unprotected one and from a legacy plaintext value.
    static Protection protectionOf(const QString &storedValue);

    /// True when @p storedValue is not OS-protected (plaintext-marked or
    /// legacy plaintext).
    static bool isUnprotected(const QString &storedValue);

    /// The value to persist when the OS protection call failed.
    ///
    /// Fail closed on a platform that HAS a backend (an empty result means
    /// "do not write anything"); on a platform WITHOUT one the value is
    /// returned marked as "plain:".
    ///
    /// Exposed as a named decision so it is directly testable — a DPAPI
    /// failure cannot be provoked from a test.
    static QString protectionFailureFallback(const QString &plainValue);

    /// Test seam: makes encrypt() fail as if the OS protection call had
    /// failed, so the fail-closed behaviour of the callers can be pinned.
    /// The failure itself cannot be provoked on a healthy system.
    ///
    /// Only the calling thread is affected, and the flag is off by default.
    static void setProtectionFailureForTesting(bool fail);
    static bool protectionFailsForTesting();
};

#endif // SECRETVAULT_H
