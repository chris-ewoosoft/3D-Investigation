#ifndef AIASSISTANT_H
#define AIASSISTANT_H

#include <QObject>
#include <QProcess>
#include <QNetworkAccessManager>
#include <QNetworkReply>
#include <QList>
#include <QMap>
#include <QJsonObject>
#include <QJsonArray>
#include <QStringList>
#include <QDateTime>
#include <QFile>

struct ChatSession {
    QString id;           // UUID-like unique ID
    QString title;        // Auto-generated from first message
    QDateTime createdAt;
    QList<QJsonObject> messages;
};

#include "Global.h"

class APP_EXPORT AIAssistant : public QObject {
    Q_OBJECT
public:
    explicit AIAssistant(QObject *parent = nullptr);
    ~AIAssistant();

    void startServer(int modelIndex);
    void stopServer();
    void sendMessage(const QString &text, const QStringList &attachments = QStringList());
    void sendMessageToSession(const QString &sessionId, const QString &text, const QStringList &attachments = QStringList());
    void switchModel(int index);

    // Session management
    void newChat();                              // Create new session (keep old)
    void loadSession(const QString &sessionId); // Switch to existing session
    void deleteSession(const QString &sessionId);
    void clearHistory();
    void reloadSessions();

    QList<ChatSession> getSessions() const { return m_sessions; }
    QString currentSessionId() const { return m_currentSessionId; }
    QList<QJsonObject> getHistory() const;
    bool isThinking() const { return m_isThinking; }
    bool isSessionThinking(const QString &sessionId) const;
    bool isServerRunning() const { return aiServerProcess->state() != QProcess::NotRunning; }

signals:
    void historyChanged();
    void sessionsChanged();
    void serverStatusChanged(const QString &status);
    void errorOccurred(const QString &error);
    void responseReceived();

private slots:
    void onProcessReadyRead();
    void onProcessError();
    void onProcessFinished(int exitCode, QProcess::ExitStatus exitStatus);
    void onReplyFinished(QNetworkReply* reply);

private:
    QProcess *aiServerProcess;
    QNetworkAccessManager *networkManager;
    QList<ChatSession> m_sessions;
    QString m_currentSessionId;
    QMap<QNetworkReply*, QString> m_pendingRequests;  // Map reply to sessionId for tracking multiple requests
    bool m_isThinking = false;
    bool m_serverReadyEmitted = false;

    ChatSession* currentSession();
    ChatSession* getSession(const QString &sessionId);
    void saveAllSessions();
    void loadAllSessions();
    QString getSessionsPath();
    QString generateSessionId();
};

#endif // AIASSISTANT_H
