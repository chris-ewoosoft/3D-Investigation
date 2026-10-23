#ifndef IUSERMANAGER_H
#define IUSERMANAGER_H

#include <QString>
#include <QStringList>

#include "Global.h"
#include "IAuthService.h"
#include "ILicenseService.h"
#include "ISmtpCredentialService.h"
#include "IUserAdminService.h"
#include "IUserPreferences.h"
#include "IUserProfileService.h"
#include "UserTypes.h"

/**
 * @brief Contract for authentication, OTP and mail-credential storage.
 *
 * Registered in ServiceRegistry so consumers can depend on this interface
 * instead of the concrete singleton. License queries/activation are inherited
 * from ILicenseService; per-user preferences from IUserPreferences.
 *
 * The concrete implementation (UserManager) is a QApplication-lifetime
 * singleton; plugins may still resolve it through:
 *   auto* users = ctx->services()->get<IUserManager>();
 *
 * ── Interface Segregation ────────────────────────────────────────────────────
 * This interface is the AGGREGATE: it inherits every capability of the user
 * service so that the composition root can register one implementation under
 * one key. A consumer must NOT depend on it for a single capability — it
 * would then be unable to compile against a smaller service, and every
 * change to the administration surface would be a change to its dependency.
 * Depend on the narrow contract instead:
 *
 *   * `IAuthService`             — sign in/out, the current identity, the
 *                                  password/OTP surface, the store lifecycle
 *   * `IUserProfileService`      — the profile of the current account
 *   * `IUserAdminService`        — administering the accounts
 *   * `ISmtpCredentialService`   — the outgoing-mail credentials
 *   * `IUserPreferences`         — per-user preferences and secrets
 *   * `ILicenseService`          — licence queries and activation
 *
 * Each of them has its own ServiceRegistry key, and the composition root
 * registers the same UserManager instance under all of them.
 */
class IUserManager : public IAuthService,
                     public IUserProfileService,
                     public IUserAdminService,
                     public ISmtpCredentialService,
                     public IUserPreferences,
                     public ILicenseService {
public:
    /// ServiceRegistry lookup key (see ServiceRegistry.h).
    static constexpr const char *ServiceKey =
        "com.3dreconstruction.service.IUserManager";

    virtual ~IUserManager() = default;

    /// Translation key for a code; empty for None/Unknown (callers then keep
    /// the service-provided message). Kept here for the callers that still
    /// hold the aggregate; new code uses authErrorKey() from UserTypes.h.
    static QString authErrorKey(AuthError code) { return ::authErrorKey(code); }
};

#endif // IUSERMANAGER_H
