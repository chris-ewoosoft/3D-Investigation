#ifndef UI_CONSTANTS_H
#define UI_CONSTANTS_H
namespace AppConstants { namespace UI {
    constexpr int BTN_HEIGHT_SMALL   = 34;
    constexpr int BTN_HEIGHT_MEDIUM  = 38;
    constexpr int BTN_HEIGHT_LARGE   = 42;
    constexpr int BTN_HEIGHT_XLARGE  = 46;
    constexpr int INPUT_HEIGHT       = 40;
    constexpr int TOOL_BTN_MIN_WIDTH = 80;
    constexpr int NAV_WIDGET_HEIGHT     = 80;
    constexpr int NAV_BTN_MIN_WIDTH     = 80;
    constexpr int NAV_AUTO_BTN_MIN_WIDTH = 100;
    constexpr int SETTINGS_WIDTH         = 660;
    constexpr int SETTINGS_HEIGHT        = 480;
    constexpr int SETTINGS_NAV_WIDTH     = 200;
    constexpr int ABOUT_DIALOG_MIN_WIDTH    = 440;
    constexpr int LICENSE_DIALOG_MIN_WIDTH  = 480;
    constexpr int ICON_SIZE_SMALL  = 32;
    constexpr int ICON_SIZE_LARGE  = 64;
} // namespace UI

/** Stable identifiers for IShellService::getMenu().
 *
 * Plugins request a menu by id and the shell creates it on first use; keeping
 * the ids in one place stops a plugin from stashing actions into a menu that
 * only it knows about. */
namespace MenuIds {
    inline constexpr const char* Viewer         = "viewer_menu";
    inline constexpr const char* Reconstruction = "recon_menu";
    inline constexpr const char* AiProcessor    = "ai_menu";
} // namespace MenuIds

} // namespace AppConstants
#endif
