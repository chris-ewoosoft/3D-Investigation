#ifndef MAIL_DOCK_WIDGET_H
#define MAIL_DOCK_WIDGET_H

#include "IMailService.h"

#include <QList>
#include <QObject>
#include <QStringList>

class IAppContext;
class QComboBox;
class QDockWidget;
class QLineEdit;
class QListWidget;
class QListWidgetItem;
class QPushButton;
class QTextBrowser;
class QTextEdit;
class QToolButton;
class QWidget;

class MailDockWidget : public QObject {
    Q_OBJECT
public:
    MailDockWidget(IAppContext *ctx, QObject *parent = nullptr);

    QDockWidget *dockWidget() const { return m_dock; }
    void showCompose();
    void showInbox();
    void showSettingsDialog();
    void showFilterDialog();
    void refreshInbox();
    void retranslate();

private slots:
    void sendCurrentMail();
    void addAttachments();
    void removeSelectedAttachment();
    void onInboxSelectionChanged();
    void applyTextFormat();
    void setTextColor();
    void insertLink();

private:
    void setupUi();
    QWidget *buildInboxPage();
    QWidget *buildComposePage();
    QWidget *buildFormatBar();
    void populateAttachments();
    void setBusy(bool busy);
    MailMessage composeMessage() const;
    void loadFilterSettings();
    void saveFilterSettings();

    IAppContext *m_ctx = nullptr;
    IMailService *m_mail = nullptr;
    QDockWidget *m_dock = nullptr;

    QWidget *m_root = nullptr;
    QListWidget *m_inboxList = nullptr;
    QTextBrowser *m_preview = nullptr;
    QLineEdit *m_toEdit = nullptr;
    QLineEdit *m_ccEdit = nullptr;
    QLineEdit *m_bccEdit = nullptr;
    QLineEdit *m_subjectEdit = nullptr;
    QTextEdit *m_bodyEdit = nullptr;
    QListWidget *m_attachmentList = nullptr;
    QPushButton *m_sendBtn = nullptr;
    QPushButton *m_refreshBtn = nullptr;
    QPushButton *m_filterBtn = nullptr;
    QComboBox *m_fontSize = nullptr;
    QStringList m_attachments;
    QList<MailMessage> m_messages;
    QList<int> m_displayedIndices;  // Map from list item row to m_messages index
    QStringList m_filterKeywords;   // Custom filter keywords
};

#endif // MAIL_DOCK_WIDGET_H
