#ifndef IUSERPREFERENCES_H
#define IUSERPREFERENCES_H

#include <QString>
#include "Global.h"

/**
 * @brief Segregated contract for per-user preferences and secure DPAPI credentials.
 *
 * Adheres to the Interface Segregation Principle (ISP): components needing
 * user configuration (e.g. Viewer preferences, AI temperature, mail settings)
 * depend on this lightweight contract rather than the full IUserManager (which
 * exposes authentication, OTP, licensing, and administration operations).
 *
 * Concrete UserManager implements both IUserManager and IUserPreferences, and
 * the composition root registers it under both keys in ServiceRegistry.
 */
class IUserPreferences {
public:
    /// ServiceRegistry lookup key (see ServiceRegistry.h).
    static constexpr const char *ServiceKey =
        "com.3dreconstruction.service.IUserPreferences";

    virtual ~IUserPreferences() = default;

    // ── Per-user preferences (stored under [User_<username>] in Config_User.ini) ──
    virtual QString getUserPref(const QString &username, const QString &key, const QString &defaultVal = {}) const = 0;
    virtual void    setUserPref(const QString &username, const QString &key, const QString &value) = 0;

    // ── Per-user secrets (encrypted with DPAPI via SecretVault, never stored plaintext) ─
    virtual QString getUserSecret(const QString &username, const QString &key, const QString &defaultVal = {}) const = 0;
    virtual void    setUserSecret(const QString &username, const QString &key, const QString &value) = 0;
};

#endif // IUSERPREFERENCES_H
