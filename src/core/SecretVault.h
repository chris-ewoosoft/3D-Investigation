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
 *   "plain:<value>"  — explicitly unencrypted fallback (non-Windows), each
 *                      value markers itself so it is never mistaken for
 *                      protected data.
 *   anything else    — legacy pre-vault plaintext; decrypt() returns it as-is
 *                      so readers transparently migrate old Config.ini files.
 */
class APP_EXPORT SecretVault {
public:
    /// Protect a value for storage. Returns a self-describing string.
    static QString encrypt(const QString &plainValue);

    /// Recover the original value from anything encrypt() produced, or a
    /// legacy plaintext value.
    static QString decrypt(const QString &storedValue);
};

#endif // SECRETVAULT_H
