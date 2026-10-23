#ifndef ILICENSESERVICE_H
#define ILICENSESERVICE_H

#include <QString>
#include <QStringList>
#include "Global.h"

enum class AuthError;

/**
 * @brief Segregated contract for software licensing, key generation and activation.
 *
 * Adheres to the Interface Segregation Principle (ISP): components needing only
 * license checks or activation (e.g. LicenseActivationDialog, feature gating)
 * depend on this contract rather than the monolithic IUserManager.
 *
 * Concrete UserManager implements both IUserManager and ILicenseService, and
 * the composition root registers it under both keys in ServiceRegistry.
 */
class ILicenseService {
public:
    /// ServiceRegistry lookup key (see ServiceRegistry.h).
    static constexpr const char *ServiceKey =
        "com.3dreconstruction.service.ILicenseService";

    virtual ~ILicenseService() = default;

    // ── User license queries and activation ──────────────────────────────────
    virtual QString licenseStatusStr(const QString &username) const = 0;
    virtual int     daysRemaining(const QString &username) const = 0;
    virtual bool    needsActivation(const QString &username) const = 0;
    virtual bool    isLicenseExpired(const QString &username) const = 0;
    virtual bool    activateTrial(const QString &username, QString &error,
                                  AuthError *code = nullptr) = 0;
    virtual bool    activateLicenseKey(const QString &username, const QString &key,
                                       QString &error, AuthError *code = nullptr) = 0;

    // ── Administration: license key inventory ────────────────────────────────
    // Administrator-only, and authorised in the core: a caller without the
    // role is refused with AuthError::NotAuthorized rather than silently
    // receiving an empty inventory.
    virtual QString     generateLicenseKey(AuthError *code = nullptr) = 0;
    virtual QStringList availableLicenseKeys(AuthError *code = nullptr) const = 0;
    virtual bool        revokeLicenseKey(const QString &key, QString &error,
                                         AuthError *code = nullptr) = 0;
};

#endif // ILICENSESERVICE_H
