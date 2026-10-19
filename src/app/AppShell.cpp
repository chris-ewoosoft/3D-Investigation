#include "AppShell.h"
#include "ISceneService.h"
#include "IUserManager.h"
#include "IUserPreferences.h"
#include "ILicenseService.h"
#include "IViewerService.h"
#include "ISettingsService.h"
#include "IAppPaths.h"
#include "SceneService.h"
#include "ViewerService.h"
#include "SettingsService.h"
#include "ReconstructionService.h"
#include "AIService.h"
#include "IReconstructionService.h"
#include "IAIService.h"
#include "IAIAssistantService.h"
#include "IMailService.h"
#include "MailService.h"
#include "SmtpOutbox.h"
#include "UserManager.h"
#include "LanguageManager.h"
#include "ILanguageService.h"
#include "AppConfig.h"
#include "Logger.h"
#include "CustomProgressDialog.h"
#include "ShellService.h"
#include "AIProcessor.h"
#include "AIAssistant.h"
#include <QMenuBar>
#include <QMenu>
#include <QWidget>
#include <QTimer>
#include <QVTKOpenGLNativeWidget.h>

AppShell::AppShell(QMainWindow*           mainWindow,
                   QVTKOpenGLNativeWidget* vtkWidget,
                   QObject*               parent)
    : QObject(parent)
    , m_vtkWidget(vtkWidget)
    , m_registry(std::make_unique<ServiceRegistry>())
    , m_languageManager(std::make_unique<LanguageManager>())
{
    m_signalBus = new SignalBus(this);

    // Widget-independent singletons register at CONSTRUCTION, not in
    // initializeServices(): MainWindow's UI setup (title bar / tab labels)
    // calls context()->translate() BEFORE initializeServices() runs, and
    // translate() must never see an empty registry. UserManager::instance()
    // is touched ONLY here (composition root).
    m_registry->registerService<IUserManager>(UserManager::instance());
    m_registry->registerService<IUserPreferences>(UserManager::instance());
    m_registry->registerService<ILicenseService>(UserManager::instance());
    m_registry->registerService<ILanguageService>(m_languageManager.get());
    // Path provider — AppConfig remains the process singleton for bootstrap
    // (Main/Logger), but consumers resolve it through the registry so the
    // path surface is mockable and the singleton stays at the composition root.
    m_registry->registerService<IAppPaths>(&AppConfig::bootstrap());
    Logger::setLogsDir(AppConfig::bootstrap().logsDir());
    // UI-shell contract (menus/tabs/progress dialog) — implementation lives in
    // AppCore (ShellService) so the contract is unit-testable; AppShell only
    // wires it and delegates.
    m_shellService = std::make_unique<ShellService>(m_signalBus);
    m_shellService->setMenuParent(mainWindow);
    m_registry->registerService<IShellService>(m_shellService.get());

    // Outgoing-mail seam: UserManager (core) notifies users by email but must
    // never link the SMTP transport in services/mail directly — the adapter is
    // wired here, at the composition root.
    m_outbox = std::make_unique<SmtpOutbox>(UserManager::instance());
    UserManager::instance()->setOutgoingMail(m_outbox.get());
}

AppShell::~AppShell() {
  // UserManager is a process-lifetime static singleton that outlives this
  // shell. Detach the injected outbox BEFORE m_outbox is destroyed (it is the
  // last member, hence the first destroyed), otherwise any later UserManager
  // email path would dereference a dangling IOutgoingMail pointer.
  UserManager::instance()->setOutgoingMail(nullptr);
  // Registry không sở hữu services, nhưng phải ngừng expose chúng trước khi
  // các unique_ptr dưới đây giải phóng impl — bất kỳ get() trễ nào trong
  // teardown sẽ nhận nullptr thay vì dangling pointer.
  m_registry->clear();
}

// ─── initializeServices ───────────────────────────────────────────────────────
void AppShell::initializeServices() {
    // ── Phase 1: Critical services (needed for UI immediately) ────────────────
    m_sceneService    = std::make_unique<SceneService>(
        m_registry->get<IUserPreferences>(), m_vtkWidget, this);
    m_viewerService = std::make_unique<ViewerService>(m_sceneService.get(), m_signalBus, this);
    m_settingsService = std::make_unique<SettingsService>(
        AppConfig::bootstrap().configPath());

    m_registry->registerService<ISceneService>(m_sceneService.get());
    m_registry->registerService<IViewerService>(m_viewerService.get());
    m_registry->registerService<ISettingsService>(m_settingsService.get());
    // IUserManager + ILanguageService + IShellService were registered in the
    // constructor (widget-independent; UI setup translates before this runs).
}

void AppShell::initializeDeferredServices() {
    // Phase-3: deferred (heavy) service wiring. Kept as one explicit block
    // here rather than a builder hierarchy — the composition root must stay
    // the single place that knows the concrete services.
    if (m_reconService) return;

    // Reconstruction module
    m_reconService = std::make_unique<ReconstructionService>();
    m_registry->registerService<IReconstructionService>(m_reconService.get());

    // AI module (builder-ready: can split into AIServiceBuilder + AIAssistantBuilder)
    m_aiService    = std::make_unique<AIService>(this);
    m_registry->registerService<IAIService>(m_aiService.get());

    // Assistant module (session state lives in AIAssistant; persistence in
    // ChatSessionStore — see src/services/ai)
    m_aiAssistant  = std::make_unique<AIAssistant>(
        m_registry->get<ILanguageService>(),
        m_registry->get<IUserManager>(),
        m_registry->get<IAppPaths>(),
        AppConfig::bootstrap().pythonInterpreter(),
        AppConfig::bootstrap().aiServerPort(),
        this);
    m_registry->registerService<IAIAssistantService>(m_aiAssistant.get());

    // Mail module
    m_mailService  = std::make_unique<MailService>(m_registry->get<IUserManager>());
    m_registry->registerService<IMailService>(m_mailService.get());
}

// ─── IAppContext ──────────────────────────────────────────────────────────────
ISceneService* AppShell::scene() {
    return m_sceneService.get();
}

IViewerService* AppShell::viewer() {
    return m_viewerService.get();
}

ISettingsService* AppShell::settings() {
    return m_settingsService.get();
}

// ─── Shell wiring ────────────────────────────────────────────────────────────
IShellService* AppShell::shell() {
    return m_shellService.get();
}

void AppShell::setMenuBar(QMenuBar* bar) {
    m_shellService->setMenuBar(bar);
}

void AppShell::setProgressDialog(CustomProgressDialog* dlg) {
    m_shellService->setProgressDialog(dlg);
}

// ─── Language ────────────────────────────────────────────────────────────────
QString AppShell::translate(const QString& key) {
    auto* lang = m_registry->get<ILanguageService>();
    return lang ? lang->translate(key) : key;
}

void AppShell::setLanguage(const QString& lang) {
    auto* service = m_registry->get<ILanguageService>();
    if (service) service->setLanguage(lang);
}
