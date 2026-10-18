#include "LanguageManager.h"
#include <QRegularExpression>
#include <QtTest>


class TestLanguageManager : public QObject {
  Q_OBJECT

private slots:
  void testSingletonInstance() {
    LanguageManager &lm1 = LanguageManager::instance();
    LanguageManager &lm2 = LanguageManager::instance();
    QCOMPARE(&lm1, &lm2);
  }

  void testSetLanguage() {
    LanguageManager &lm = LanguageManager::instance();

    QSignalSpy spy(&lm, &LanguageManager::languageChanged);

    lm.setLanguage("en");
    QCOMPARE(lm.currentLanguage(), QString("en"));
    QCOMPARE(spy.count(), 1);

    lm.setLanguage("vi");
    QCOMPARE(lm.currentLanguage(), QString("vi"));
    QCOMPARE(spy.count(), 2);
  }

  void testTranslateFallback() {
    LanguageManager &lm = LanguageManager::instance();

    // When key doesn't exist, translate should fallback to key
    QString fallback = lm.translate("non.existent.key");
    QCOMPARE(fallback, QString("non.existent.key"));
  }

  void testTranslateKnownKeyPerLanguage() {
    LanguageManager &lm = LanguageManager::instance();
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
    LanguageManager &lm = LanguageManager::instance();
    // A null key must not crash and must not produce a fake translation.
    QCOMPARE(lm.translate(static_cast<const char *>(nullptr)), QString());
    QCOMPARE(lm.translate("char.translate.missing"), QStringLiteral("char.translate.missing"));
  }

  void testDisplayNameAndFlagFollowLanguage() {
    LanguageManager &lm = LanguageManager::instance();
    lm.setLanguage("en");
    QCOMPARE(lm.displayName(), QString("English"));
    QCOMPARE(lm.flagEmoji(), QString::fromUtf8("🇬🇧"));
    lm.setLanguage("vi");
    QCOMPARE(lm.displayName(), QString::fromUtf8("Tiếng Việt"));
    QCOMPARE(lm.flagEmoji(), QString::fromUtf8("🇻🇳"));
  }

  void testUnknownLanguageIsIgnored() {
    LanguageManager &lm = LanguageManager::instance();
    lm.setLanguage("en");
    QTest::ignoreMessage(QtWarningMsg, QRegularExpression("Unknown language"));
    lm.setLanguage("fr");
    QCOMPARE(lm.currentLanguage(), QString("en"));
  }
};

QTEST_GUILESS_MAIN(TestLanguageManager)

#include "TestLanguageManager.moc"