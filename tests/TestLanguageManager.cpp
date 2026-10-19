#include "LanguageManager.h"
#include <QRegularExpression>
#include <QtTest>


class TestLanguageManager : public QObject {
  Q_OBJECT

private slots:
  void testSetLanguage() {
    LanguageManager lm;

    QSignalSpy spy(&lm, &LanguageManager::languageChanged);

    lm.setLanguage("en");
    QCOMPARE(lm.currentLanguage(), QString("en"));
    QCOMPARE(spy.count(), 1);

    lm.setLanguage("vi");
    QCOMPARE(lm.currentLanguage(), QString("vi"));
    QCOMPARE(spy.count(), 2);
  }

  void testTranslateFallback() {
    LanguageManager lm;

    // When key doesn't exist, translate should fallback to key
    QString fallback = lm.translate("non.existent.key");
    QCOMPARE(fallback, QString("non.existent.key"));
  }

  void testTranslateKnownKeyPerLanguage() {
    LanguageManager lm;
    lm.setLanguage("en");
    const QString en = lm.translate("app.title");
    QVERIFY(!en.isEmpty());
    QVERIFY(en != QLatin1String("app.title"));

    lm.setLanguage("vi");
    const QString vi = lm.translate("app.title");
    QVERIFY(!vi.isEmpty());
    // The dictionary carries both languages independently.
    QVERIFY(vi != en);
  }

  void testCharPointerTranslate() {
    LanguageManager lm;
    // A null key must not crash and must not produce a fake translation.
    QCOMPARE(lm.translate(static_cast<const char *>(nullptr)), QString());
    QCOMPARE(lm.translate("char.translate.missing"), QStringLiteral("char.translate.missing"));
  }

  void testDisplayNameAndFlagFollowLanguage() {
    LanguageManager lm;
    lm.setLanguage("en");
    QCOMPARE(lm.displayName(), QString("English"));
    QCOMPARE(lm.flagEmoji(), QString::fromUtf8("🇬🇧"));
    lm.setLanguage("vi");
    QCOMPARE(lm.displayName(), QString::fromUtf8("Tiếng Việt"));
    QCOMPARE(lm.flagEmoji(), QString::fromUtf8("🇻🇳"));
  }

  void testUnknownLanguageIsIgnored() {
    LanguageManager lm;
    lm.setLanguage("en");
    QTest::ignoreMessage(QtWarningMsg, QRegularExpression("Unknown language"));
    lm.setLanguage("fr");
    QCOMPARE(lm.currentLanguage(), QString("en"));
  }
};

QTEST_GUILESS_MAIN(TestLanguageManager)

#include "TestLanguageManager.moc"
