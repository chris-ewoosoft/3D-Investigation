#include "MailMimeParser.h"

#include <QtTest>

class TestMailMimeParser : public QObject {
    Q_OBJECT

private slots:
    void splitsDelimitedAddresses()
    {
        const QStringList addresses =
            MailMimeParser::splitAddresses("Alice <alice@example.com>; bob@example.com");

        QCOMPARE(addresses, QStringList({"alice@example.com", "bob@example.com"}));
    }

    void parsesPlainTextFetchedMessage()
    {
        const QString raw =
            "* 1 FETCH (FLAGS (\\Seen) BODY[] {191}\r\n"
            "From: =?UTF-8?B?QWxpY2U=?= <alice@example.com>\r\n"
            "To: bob@example.com\r\n"
            "Subject: =?UTF-8?Q?Hello=20World?=\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n"
            "Content-Transfer-Encoding: quoted-printable\r\n"
            "\r\n"
            "Line=201\r\n"
            "Line=202\r\n"
            ")\r\n";

        const MailMessage message = MailMimeParser::parseFetchedMessage("42", raw);

        QCOMPARE(message.uid, QString("42"));
        QVERIFY(message.isRead);
        QCOMPARE(message.from, QString("Alice <alice@example.com>"));
        QCOMPARE(message.subject, QString("Hello World"));
        QVERIFY(message.htmlBody.contains("Line 1"));
        QVERIFY(message.htmlBody.contains("Line 2"));
        QVERIFY(message.htmlBody.startsWith("<pre"));
    }

    void prefersHtmlInAMultipartMessageAndSkipsAttachments()
    {
        const QString html = QStringLiteral("<p>Xin chào <strong>Qt</strong></p>");
        const QByteArray encodedHtml = html.toUtf8().toBase64();
        const QString raw =
            QStringLiteral("* 2 FETCH (BODY[] {512}\r\n"
                           "From: Alice <alice@example.com>\r\n"
                           "To: Bob <bob@example.com>\r\n"
                           "Subject: =?UTF-8?Q?Multipart?= =?UTF-8?Q?_message?=\r\n"
                           "Content-Type: multipart/alternative; boundary=\"mix-42\"\r\n"
                           "\r\n"
                           "--mix-42\r\n"
                           "Content-Type: text/plain; charset=utf-8\r\n"
                           "Content-Transfer-Encoding: quoted-printable\r\n"
                           "\r\n"
                           "Plain=20fallback\r\n"
                           "--mix-42\r\n"
                           "Content-Type: text/html; charset=utf-8\r\n"
                           "Content-Transfer-Encoding: base64\r\n"
                           "\r\n") +
            QString::fromLatin1(encodedHtml) +
            QStringLiteral("\r\n"
                           "--mix-42\r\n"
                           "Content-Type: application/octet-stream\r\n"
                           "Content-Disposition: attachment; filename=ignored.bin\r\n"
                           "\r\n"
                           "ignored\r\n"
                           "--mix-42--\r\n"
                           ")\r\n");

        const MailMessage message = MailMimeParser::parseFetchedMessage("43", raw);

        QCOMPARE(message.uid, QStringLiteral("43"));
        QCOMPARE(message.subject, QStringLiteral("Multipart message"));
        QCOMPARE(message.htmlBody, html);
        QVERIFY(!message.htmlBody.contains(QStringLiteral("Plain fallback")));
        QVERIFY(!message.htmlBody.contains(QStringLiteral("ignored")));
    }
};

QTEST_GUILESS_MAIN(TestMailMimeParser)
#include "TestMailMimeParser.moc"
