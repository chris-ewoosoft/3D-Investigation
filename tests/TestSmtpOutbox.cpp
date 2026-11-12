#include <QtTest>
#include <QTemporaryDir>

#include "SmtpOutbox.h"
#include "UserManager.h"

/**
 * @brief Unit tests for the SmtpOutbox IOutgoingMail adapter (Phase A:
 *        core no longer links SMTP transport directly).
 *
 * Only the validation branches are exercised — the success path would
 * require a live SMTP server, so it stays out of unit scope. Contract:
 *  - a missing user manager is an explicit error,
 *  - unconfigured or mismatched sender credentials fail with a clear reason,
 *    before any transport is constructed.
 */
class TestSmtpOutbox : public QObject {
    Q_OBJECT

private slots:
    void nullUserManagerFails();
    void unconfiguredSenderFails();
    void senderMismatchFails();
};

void TestSmtpOutbox::nullUserManagerFails()
{
    SmtpOutbox outbox(nullptr);
    QString error;
    QVERIFY(!outbox.send("a@example.com", "b@example.com",
                         "subject", "body", error));
    QVERIFY(error.contains("user manager", Qt::CaseInsensitive));
}

void TestSmtpOutbox::unconfiguredSenderFails()
{
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    QVERIFY(UserManager::instance()->loadConfig(dir.filePath("Config.ini")));
    UserManager::instance()->logout();

    SmtpOutbox outbox(UserManager::instance());
    QString error;
    QVERIFY(!outbox.send("a@example.com", "b@example.com",
                         "subject", "body", error));
    // Vietnamese user-facing message (config hint), not a generic failure.
    QVERIFY(error.contains("Chưa cấu hình email gửi"));
}

void TestSmtpOutbox::senderMismatchFails()
{
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    QVERIFY(UserManager::instance()->loadConfig(dir.filePath("Config.ini")));
    UserManager::instance()->logout();
    UserManager::instance()->setSmtpCredentials("configured@example.com", "secret");

    SmtpOutbox outbox(UserManager::instance());
    QString error;
    QVERIFY(!outbox.send("other@example.com", "b@example.com",
                         "subject", "body", error));
    QVERIFY(error.contains("does not match", Qt::CaseInsensitive));
}

QTEST_GUILESS_MAIN(TestSmtpOutbox)
#include "TestSmtpOutbox.moc"
