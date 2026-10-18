#ifndef USER_AUTH_PLUGIN_H
#define USER_AUTH_PLUGIN_H

#include <QObject>
#include "IPlugin.h"

class IAppContext;
class ILanguageService;
class IUserManager;
class QToolButton;
class QGroupBox;

class UserAuthPlugin : public QObject, public IPlugin {
    Q_OBJECT
    Q_PLUGIN_METADATA(IID IPlugin_iid)
    Q_INTERFACES(IPlugin)

public:
    static QString translate(const QString& key);
    /** Module-scoped ILanguageService binding (registry-sourced), for dialogs
     *  that need the service itself (e.g. ModernMessageBox::question). */
    static ILanguageService* languageService() { return s_lang; }
    /** Module-scoped IUserManager binding (registry-sourced). Bound during
     *  initialize(), cleared on cleanup(); valid for every UI code path. */
    static IUserManager* users();

    QString pluginName() const override { return "User Authentication Plugin"; }
    void initialize(IAppContext* context) override;
    void cleanup() override;
    int  loadOrder() const override { return 5; } // Load rất sớm, trước các module khác

private slots:
    void onLogout();
    void onChangePassword();
    void onAbout();
    void onChangeAvatar();
    void onLanguageChanged(const QString &lang);
    void onOpenSettings();

private:
    void setupMenus();
    void retranslateMenus();
    bool ensureAuthenticated();
    void checkLicense();
    void loadUserSettings();
    void saveUserPref(const QString &key, const QString &value);

    IAppContext *m_context = nullptr;
    QString m_currentUser;

    /// Module-scoped translation source, bound to the registry-provided
    /// ILanguageService during initialize() (cleared on cleanup). Feeds the
    /// static translate() facade used across this module's dialogs.
    static ILanguageService *s_lang;
    static IUserManager *s_users;

    // UI elements that need retranslation
    QToolButton *m_avatarBtn    = nullptr;
    QToolButton *m_langBtn      = nullptr;

    QGroupBox   *m_groupSettings = nullptr;
    QGroupBox   *m_groupHelp     = nullptr;
    QToolButton *m_btnSettings   = nullptr;   // single "Cài đặt" ribbon button
    QToolButton *m_btnAbout      = nullptr;
};

#endif // USER_AUTH_PLUGIN_H
