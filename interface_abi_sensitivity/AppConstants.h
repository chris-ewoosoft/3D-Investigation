#ifndef APP_CONSTANTS_H
#define APP_CONSTANTS_H
#include "ReconstructionConstants.h"
#include "AIConstants.h"
#include "UIConstants.h"
#include "FileConstants.h"

namespace AppConstants {
/** Stable plugin names used by IPlugin::dependencies(). Keep in sync with the
 *  pluginName() overrides — the resolver matches these strings exactly. */
namespace PluginNames {
    inline constexpr const char* UserAuthentication = "User Authentication Plugin";
} // namespace PluginNames
} // namespace AppConstants
#endif
