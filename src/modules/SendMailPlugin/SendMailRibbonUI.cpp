#include "SendMailRibbonUI.h"

#include "IAppContext.h"

SendMailRibbonUI::SendMailRibbonUI(IAppContext *ctx, QWidget *panel, QObject *parent)
    : AbstractRibbonPanel(ctx, parent)
{
    if (!beginPanel(panel, "sendMailGroup")) return;

    m_btnToggleMail = addButton("☒", "#ef4444", "#b91c1c",
                                ctx->translate("mail.open_close"), 100);
    m_btnSettings = addButton("⚙", "#f59e0b", "#b45309",
                              ctx->translate("mail.settings"));

    finishPanel(ctx->translate("mail.menu"));
}
