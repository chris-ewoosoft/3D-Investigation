#include "SettingsDialog.h"
#include "AdminUserManagerDialog.h"
#include "ThemeSelectionDialog.h"
#include "UserAuthPlugin.h"
#include "IAppContext.h"
#include "ISettingsService.h"
#include "UserManager.h"

#include <QHBoxLayout>
#include <QVBoxLayout>
#include <QLabel>
#include <QPainter>
#include <QLinearGradient>

static QIcon makeNavIcon(const QString &emoji, const QColor &c1, const QColor &c2) {
    QPixmap pix(32, 32);
    pix.fill(Qt::transparent);
    QPainter p(&pix);
    p.setRenderHint(QPainter::Antialiasing);
    QLinearGradient g(0, 0, 0, 32);
    g.setColorAt(0, c1); g.setColorAt(1, c2);
    p.setBrush(g); p.setPen(Qt::NoPen);
    p.drawRoundedRect(1, 1, 30, 30, 7, 7);
    QFont f = p.font(); f.setPixelSize(16); f.setBold(true);
    p.setFont(f); p.setPen(Qt::white);
    p.drawText(QRect(0, 0, 32, 32), Qt::AlignCenter, emoji);
    return QIcon(pix);
}

SettingsDialog::SettingsDialog(const QString &username, bool isAdmin,
                               IAppContext *ctx, QWidget *parent)
    : ModernDialog(UserAuthPlugin::translate("menu.settings"), parent)
    , m_username(username)
    , m_isAdmin(isAdmin)
    , m_ctx(ctx)
{
    resize(1000, 700);
    setMinimumSize(1000, 700);
    setupUi();
}

void SettingsDialog::setupUi()
{
    QWidget *content = new QWidget(this);
    QHBoxLayout *root = new QHBoxLayout(content);
    root->setContentsMargins(0, 0, 0, 0);
    root->setSpacing(0);

    // ── Left navigation list ──────────────────────────────────────────────────
    m_navList = new QListWidget(content);
    m_navList->setObjectName("settingsNavList");
    m_navList->setFixedWidth(200);
    m_navList->setStyleSheet(
        "QListWidget { background: rgba(0,0,0,0.18); border: none; border-right: 1px solid rgba(255,255,255,0.08); border-radius: 0; }"
        "QListWidget::item { padding: 12px 16px; border-radius: 6px; margin: 4px 6px; color: #cbd5e1; font-size: 10pt; }"
        "QListWidget::item:selected { background: rgba(99,102,241,0.25); color: #a5b4fc; font-weight: 600; }"
        "QListWidget::item:hover { background: rgba(255,255,255,0.07); }"
    );

    // ── Right info panel / Stack ───────────────────────────────────────────────
    m_stack = new QStackedWidget(content);
    
    QWidget *infoPanel = new QWidget(m_stack);
    QVBoxLayout *infoPl = new QVBoxLayout(infoPanel);
    infoPl->setContentsMargins(32, 32, 32, 32);
    infoPl->setSpacing(16);

    QLabel *hintIcon = new QLabel("⚙️", infoPanel);
    hintIcon->setAlignment(Qt::AlignCenter);
    hintIcon->setStyleSheet("font-size: 48px;");

    QLabel *hintTitle = new QLabel(UserAuthPlugin::translate("menu.settings"), infoPanel);
    hintTitle->setAlignment(Qt::AlignCenter);
    hintTitle->setStyleSheet("font-size: 16pt; font-weight: bold; color: #e2e8f0;");

    QLabel *hintDesc = new QLabel(
        UserAuthPlugin::translate("settings.hint_desc"), infoPanel);
    hintDesc->setAlignment(Qt::AlignCenter);
    hintDesc->setWordWrap(true);
    hintDesc->setStyleSheet("color: #64748b; font-size: 10pt;");

    infoPl->addStretch();
    infoPl->addWidget(hintIcon);
    infoPl->addWidget(hintTitle);
    infoPl->addWidget(hintDesc);
    infoPl->addStretch();
    
    m_stack->addWidget(infoPanel);
    
    // Create pages
    QWidget *adminPage = nullptr;
    if (m_isAdmin) {
        adminPage = new AdminUserManagerDialog(m_stack);
        m_stack->addWidget(adminPage);
    }
    
    QWidget *themePage = new ThemeSelectionDialog(m_username, m_stack);
    m_stack->addWidget(themePage);

    // --- Nav items ---
    if (m_isAdmin) {
        auto *navItem0 = new QListWidgetItem(makeNavIcon("👥", QColor("#6366f1"), QColor("#4f46e5")),
                                             UserAuthPlugin::translate("menu.user_mgmt"),
                                             m_navList);
        navItem0->setSizeHint(QSize(0, 48));
    }
    auto *navItem1 = new QListWidgetItem(makeNavIcon("🎨", QColor("#ec4899"), QColor("#db2777")),
                                         UserAuthPlugin::translate("menu.theme"),
                                         m_navList);
    navItem1->setSizeHint(QSize(0, 48));

    // ── Layout assembly ───────────────────────────────────────────────────────
    root->addWidget(m_navList);
    root->addWidget(m_stack, 1);

    connect(m_navList, &QListWidget::itemClicked, this, [this, adminPage, themePage](QListWidgetItem* item) {
        int row = m_navList->row(item);
        if (m_isAdmin && row == 0) {
            m_stack->setCurrentWidget(adminPage);
        } else {
            m_stack->setCurrentWidget(themePage);
        }
    });

    // Bottom close button
    QWidget *wrapper = new QWidget(this);
    QVBoxLayout *wl = new QVBoxLayout(wrapper);
    wl->setContentsMargins(0, 0, 0, 0);
    wl->setSpacing(0);
    wl->addWidget(content, 1);

    QWidget *footer = new QWidget(wrapper);
    footer->setObjectName("settingsFooter");
    footer->setStyleSheet("#settingsFooter { border-top: 1px solid rgba(255,255,255,0.08); background: transparent; }");
    QHBoxLayout *fl = new QHBoxLayout(footer);
    fl->setContentsMargins(16, 10, 16, 10);
    QPushButton *closeBtn = new QPushButton(UserAuthPlugin::translate("common.close"), footer);
    closeBtn->setObjectName("primary");
    closeBtn->setFixedHeight(38);
    closeBtn->setFixedWidth(100);
    closeBtn->setCursor(Qt::PointingHandCursor);
    connect(closeBtn, &QPushButton::clicked, this, &QDialog::accept);
    fl->addStretch();
    fl->addWidget(closeBtn);
    wl->addWidget(footer);

    setContentLayout(wl);
}
