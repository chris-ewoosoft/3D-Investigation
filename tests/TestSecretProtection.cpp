#include <QtTest>

#include <QFile>
#include <QSettings>
#include <QTemporaryDir>

#include "LicenseKeyLedger.h"
#include "SecretVault.h"
#include "UserSettingsStore.h"

/**
 * @brief The at-rest confidentiality contract of the user INI.
 *
 * Three guarantees are pinned here:
 *
 *  1. **Fail closed.** When the OS protection call fails, the secret must not
 *     be written in the clear. On a platform that HAS an OS backend the
 *     fallback is "write nothing" (the caller keeps the value it already
 *     has); on a platform that has none the value is stored marked as
 *     "plain:" — a documented platform limit, never a silent downgrade.
 *
 *  2. **Two namespaces, two confidentiality levels.** A *preference* is stored
 *     in the clear; a *secret* (mail password, AI provider API key) is stored
 *     through the OS vault. A caller that mistakenly uses the preference API
 *     for a secret still gets an encrypted value, and the plaintext copy is
 *     retired — the leak cannot be reintroduced by a call site.
 *
 *  3. **Legacy migration is complete.** A pre-vault configuration must end up
 *     with no plaintext secret left in the file, and a key inventory must
 *     survive a blob the application cannot decrypt.
 */
class TestSecretProtection : public QObject {
    Q_OBJECT

private slots:
    void vaultFailsClosedWhenProtectionIsUnavailable();
    void vaultClassifiesEveryStoredForm();
    void secretKeyNeverReachesThePlaintextNamespace();
    void legacyPlaintextSecretsAreMigratedOnLoad();
    void licenseInventorySurvivesAnUnreadableBlob();
    void anUnreadableSecretIsNotReplacedByAPlaintextFallback();
    void anEmptySecretRemovesTheStoredValue();
    void aFailedProtectionKeepsTheStoredValue();
};

namespace {

/// The raw text of the INI file — the artefact an attacker would read.
QString readRawIni(const QString &path)
{
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly | QIODevice::Text)) return {};
    return QString::fromUtf8(file.readAll());
}

} // namespace

void TestSecretProtection::vaultFailsClosedWhenProtectionIsUnavailable()
{
    // The decision itself is exposed so it can be tested — a DPAPI failure
    // cannot be provoked from a test process.
    const QString fallback = SecretVault::protectionFailureFallback(QStringLiteral("s3cret"));
#ifdef Q_OS_WIN
    // This platform HAS an OS backend: a failure must produce "write nothing",
    // never a plaintext value.
    QVERIFY2(fallback.isEmpty(),
             qPrintable(QStringLiteral("the protection failure fallback must be empty, got: %1")
                            .arg(fallback)));
#else
    // No OS backend at all: the value is stored marked as explicitly
    // unprotected (the documented platform limit).
    QVERIFY(fallback.startsWith(QStringLiteral("plain:")));
    QCOMPARE(SecretVault::decrypt(fallback), QStringLiteral("s3cret"));
#endif

    // An empty secret is never turned into a blob.
    QVERIFY(SecretVault::encrypt(QString()).isEmpty());
    QVERIFY(SecretVault::protectionFailureFallback(QString()).isEmpty());

    // A successful encryption is always a protected blob, never a plaintext
    // marker — whatever the platform.
    const QString sealed = SecretVault::encrypt(QStringLiteral("s3cret"));
    QVERIFY(!sealed.isEmpty());
    QCOMPARE(SecretVault::protectionOf(sealed), SecretVault::Protection::Protected);
    QVERIFY(!SecretVault::isUnprotected(sealed));
}

void TestSecretProtection::vaultClassifiesEveryStoredForm()
{
    QCOMPARE(SecretVault::protectionOf(QString()), SecretVault::Protection::Empty);
    QCOMPARE(SecretVault::protectionOf(QStringLiteral("enc1:AAAA")),
             SecretVault::Protection::Protected);
    QCOMPARE(SecretVault::protectionOf(QStringLiteral("plain:value")),
             SecretVault::Protection::PlaintextMarked);
    QCOMPARE(SecretVault::protectionOf(QStringLiteral("legacy-value")),
             SecretVault::Protection::LegacyPlaintext);

    QVERIFY(SecretVault::isUnprotected(QStringLiteral("plain:value")));
    QVERIFY(SecretVault::isUnprotected(QStringLiteral("legacy-value")));
    QVERIFY(!SecretVault::isUnprotected(QStringLiteral("enc1:AAAA")));

    // A "plain:" marker is stripped on the way out; a legacy value passes
    // through unchanged so old configurations keep working.
    QCOMPARE(SecretVault::decrypt(QStringLiteral("plain:value")), QStringLiteral("value"));
    QCOMPARE(SecretVault::decrypt(QStringLiteral("legacy-value")), QStringLiteral("legacy-value"));
    QVERIFY(SecretVault::decrypt(QString()).isEmpty());
}

void TestSecretProtection::secretKeyNeverReachesThePlaintextNamespace()
{
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    const QString iniPath = dir.path() + QStringLiteral("/users.ini");

    QSettings settings(iniPath, QSettings::IniFormat);
    UserSettingsStore store;
    store.attachSettings(&settings);

    // A caller that mistakenly uses the *preference* API for a secret key
    // must still get an encrypted value on disk.
    QVERIFY(UserSettingsStore::isSecretKey(QStringLiteral("ai_api_key")));
    QVERIFY(UserSettingsStore::isSecretKey(QStringLiteral("mail_password")));
    QVERIFY(!UserSettingsStore::isSecretKey(QStringLiteral("theme")));

    store.setUserPref(QStringLiteral("alice"), QStringLiteral("ai_api_key"),
                      QStringLiteral("sk-plaintext-leak"));
    settings.sync();

    const QString raw = readRawIni(iniPath);
    QVERIFY(!raw.isEmpty());
    QVERIFY2(!raw.contains(QStringLiteral("sk-plaintext-leak")),
             qPrintable(QStringLiteral("the API key reached the INI in the clear:\n%1").arg(raw)));

    // …and the value is still readable through both APIs.
    QCOMPARE(store.getUserPref(QStringLiteral("alice"), QStringLiteral("ai_api_key")),
             QStringLiteral("sk-plaintext-leak"));
    QCOMPARE(store.getUserSecret(QStringLiteral("alice"), QStringLiteral("ai_api_key")),
             QStringLiteral("sk-plaintext-leak"));

    // A real preference stays in the clear — it is not a secret.
    store.setUserPref(QStringLiteral("alice"), QStringLiteral("theme"), QStringLiteral("dark"));
    settings.sync();
    QVERIFY(readRawIni(iniPath).contains(QStringLiteral("dark")));
}

void TestSecretProtection::legacyPlaintextSecretsAreMigratedOnLoad()
{
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    const QString iniPath = dir.path() + QStringLiteral("/users.ini");

    // A configuration written before the vault existed: the API key and the
    // SMTP password sit in the file in the clear.
    {
        QSettings settings(iniPath, QSettings::IniFormat);
        settings.setValue(QStringLiteral("User_alice/pref_ai_api_key"),
                          QStringLiteral("sk-legacy-leak"));
        settings.setValue(QStringLiteral("SMTP/sender_password"),
                          QStringLiteral("legacy-smtp-secret"));
        settings.setValue(QStringLiteral("User_alice/pref_theme"), QStringLiteral("dark"));
        settings.sync();
    }
    QVERIFY(readRawIni(iniPath).contains(QStringLiteral("sk-legacy-leak")));

    QSettings settings(iniPath, QSettings::IniFormat);
    UserSettingsStore store;
    store.attachSettings(&settings);
    const int migrated = store.migrateLegacySecrets();
    settings.sync();

    QCOMPARE(migrated, 2);
    const QString raw = readRawIni(iniPath);
    QVERIFY2(!raw.contains(QStringLiteral("sk-legacy-leak")),
             qPrintable(QStringLiteral("the legacy API key was not sealed:\n%1").arg(raw)));
    QVERIFY2(!raw.contains(QStringLiteral("legacy-smtp-secret")),
             qPrintable(QStringLiteral("the legacy SMTP password was not sealed:\n%1").arg(raw)));

    // The values survive the migration, and the plain preference is untouched.
    QCOMPARE(store.getUserSecret(QStringLiteral("alice"), QStringLiteral("ai_api_key")),
             QStringLiteral("sk-legacy-leak"));
    QCOMPARE(store.smtpSenderPassword(), QStringLiteral("legacy-smtp-secret"));
    QCOMPARE(store.getUserPref(QStringLiteral("alice"), QStringLiteral("theme")),
             QStringLiteral("dark"));

    // A second run has nothing left to migrate (the migration is idempotent).
    QCOMPARE(store.migrateLegacySecrets(), 0);
}

void TestSecretProtection::licenseInventorySurvivesAnUnreadableBlob()
{
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    const QString iniPath = dir.path() + QStringLiteral("/licenses.ini");
    const QString unreadable = QStringLiteral("enc1:bm90LWEtcHJvdGVjdGVkLWJsb2I=");

    QSettings settings(iniPath, QSettings::IniFormat);
    LicenseKeyLedger ledger;
    ledger.attachSettings(&settings);

    // One key the application can read, one it cannot (another user or
    // machine, a tampered file).
    const QString key = ledger.generate();
    settings.setValue(QStringLiteral("LicenseKeys/keys"),
                      QStringList{SecretVault::encrypt(key), unreadable});
    settings.sync();

    ledger.load();
    QVERIFY(ledger.contains(key));

    ledger.save();
    settings.sync();

    // The unreadable blob is preserved verbatim: a save must never destroy a
    // key it merely failed to read.
    const QStringList stored = settings.value(QStringLiteral("LicenseKeys/keys")).toStringList();
    QVERIFY2(stored.contains(unreadable),
             qPrintable(QStringLiteral("the unreadable license blob was dropped: %1")
                            .arg(stored.join(QStringLiteral(", ")))));
    QVERIFY2(!stored.contains(key),
             qPrintable(QStringLiteral("a license key was written in the clear")));
    QCOMPARE(ledger.available(), QStringList{key});

    // When the protection call fails, the ledger keeps the previously
    // protected blob — the inventory must not lose the key, and the key must
    // never be written in the clear.
    SecretVault::setProtectionFailureForTesting(true);
    ledger.save();
    settings.sync();
    SecretVault::setProtectionFailureForTesting(false);

    const QStringList afterFailure = settings.value(QStringLiteral("LicenseKeys/keys")).toStringList();
    QCOMPARE(afterFailure.size(), 2);
    QVERIFY2(!afterFailure.contains(key),
             qPrintable(QStringLiteral("a license key was written in the clear")));
    QVERIFY2(afterFailure.contains(unreadable),
             qPrintable(QStringLiteral("the unreadable license blob was dropped")));
    QCOMPARE(ledger.available(), QStringList{key});

    // A key that was never protected has no previous blob to fall back on:
    // it is not written in the clear either — it has to be issued again.
    LicenseKeyLedger fresh;
    fresh.attachSettings(&settings);
    const QString freshKey = fresh.generate();
    SecretVault::setProtectionFailureForTesting(true);
    fresh.save();
    settings.sync();
    SecretVault::setProtectionFailureForTesting(false);
    const QStringList afterFreshFailure =
        settings.value(QStringLiteral("LicenseKeys/keys")).toStringList();
    QVERIFY2(!afterFreshFailure.contains(freshKey),
             qPrintable(QStringLiteral("an unprotected license key was written in the clear")));
}

void TestSecretProtection::anUnreadableSecretIsNotReplacedByAPlaintextFallback()
{
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    const QString iniPath = dir.path() + QStringLiteral("/users.ini");

    // A blob that cannot be decrypted (other user/machine, tampered file).
    // The legacy plaintext copy of the same key must NOT take its place:
    // resurrecting an unprotected value would be a silent downgrade.
    {
        QSettings settings(iniPath, QSettings::IniFormat);
        settings.setValue(QStringLiteral("User_alice/secret_ai_api_key"),
                          QStringLiteral("enc1:bm90LWEtcHJvdGVjdGVkLWJsb2I="));
        settings.setValue(QStringLiteral("User_alice/pref_ai_api_key"),
                          QStringLiteral("stale-plaintext"));
        settings.sync();
    }

    QSettings settings(iniPath, QSettings::IniFormat);
    UserSettingsStore store;
    store.attachSettings(&settings);

    QCOMPARE(store.getUserSecret(QStringLiteral("alice"), QStringLiteral("ai_api_key"),
                                 QStringLiteral("fallback")),
             QStringLiteral("fallback"));
    // The preference API reads the same (protected) namespace.
    QCOMPARE(store.getUserPref(QStringLiteral("alice"), QStringLiteral("ai_api_key"),
                                QStringLiteral("fallback")),
             QStringLiteral("fallback"));
}

void TestSecretProtection::anEmptySecretRemovesTheStoredValue()
{
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    const QString iniPath = dir.path() + QStringLiteral("/users.ini");

    QSettings settings(iniPath, QSettings::IniFormat);
    UserSettingsStore store;
    store.attachSettings(&settings);

    store.setUserSecret(QStringLiteral("alice"), QStringLiteral("ai_api_key"),
                        QStringLiteral("sk-value"));
    QCOMPARE(store.getUserSecret(QStringLiteral("alice"), QStringLiteral("ai_api_key")),
             QStringLiteral("sk-value"));

    // An empty value clears the secret instead of storing an empty blob.
    store.setUserSecret(QStringLiteral("alice"), QStringLiteral("ai_api_key"), QString());
    QCOMPARE(store.getUserSecret(QStringLiteral("alice"), QStringLiteral("ai_api_key"),
                                 QStringLiteral("gone")),
             QStringLiteral("gone"));

    // The same for the SMTP credentials: an empty password clears the entry.
    store.setSmtpCredentials(QStringLiteral("bot@example.com"), QStringLiteral("secret"));
    QCOMPARE(store.smtpSenderPassword(), QStringLiteral("secret"));
    store.setSmtpCredentials(QStringLiteral("bot@example.com"), QString());
    QVERIFY(store.smtpSenderPassword().isEmpty());
    QVERIFY(!readRawIni(iniPath).contains(QStringLiteral("secret")));
}

void TestSecretProtection::aFailedProtectionKeepsTheStoredValue()
{
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    const QString iniPath = dir.path() + QStringLiteral("/users.ini");

    QSettings settings(iniPath, QSettings::IniFormat);
    UserSettingsStore store;
    store.attachSettings(&settings);

    // A healthy system first: the value is protected and readable.
    store.setUserSecret(QStringLiteral("alice"), QStringLiteral("ai_api_key"),
                        QStringLiteral("sk-original"));
    QCOMPARE(store.getUserSecret(QStringLiteral("alice"), QStringLiteral("ai_api_key")),
             QStringLiteral("sk-original"));
    store.setSmtpCredentials(QStringLiteral("bot@example.com"), QStringLiteral("smtp-original"));
    QCOMPARE(store.smtpSenderPassword(), QStringLiteral("smtp-original"));

    // Now the protection call fails (the OS refused to protect the value).
    // The caller must keep the value it already has — writing the new one in
    // the clear would be a silent downgrade of the stored secret.
    SecretVault::setProtectionFailureForTesting(true);
    store.setUserSecret(QStringLiteral("alice"), QStringLiteral("ai_api_key"),
                        QStringLiteral("sk-new-value"));
    QCOMPARE(store.getUserSecret(QStringLiteral("alice"), QStringLiteral("ai_api_key")),
             QStringLiteral("sk-original"));
    QVERIFY2(!readRawIni(iniPath).contains(QStringLiteral("sk-new-value")),
             qPrintable(QStringLiteral("the secret was written in the clear after a "
                                        "failed protection:\n%1")
                            .arg(readRawIni(iniPath))));

    // The same for the SMTP password.
    store.setSmtpCredentials(QStringLiteral("bot@example.com"), QStringLiteral("smtp-new"));
    QCOMPARE(store.smtpSenderPassword(), QStringLiteral("smtp-original"));

    // …and for the migration of a legacy plaintext secret: it stays readable
    // instead of being silently dropped.
    SecretVault::setProtectionFailureForTesting(false);
    {
        QSettings legacy(dir.path() + QStringLiteral("/legacy.ini"), QSettings::IniFormat);
        legacy.setValue(QStringLiteral("User_alice/pref_ai_api_key"),
                        QStringLiteral("sk-legacy"));
        legacy.setValue(QStringLiteral("SMTP/sender_password"),
                        QStringLiteral("legacy-smtp"));
        legacy.sync();
    }
    QSettings legacySettings(dir.path() + QStringLiteral("/legacy.ini"), QSettings::IniFormat);
    UserSettingsStore legacyStore;
    legacyStore.attachSettings(&legacySettings);
    SecretVault::setProtectionFailureForTesting(true);
    QCOMPARE(legacyStore.migrateLegacySecrets(), 0);
    QCOMPARE(legacyStore.getUserSecret(QStringLiteral("alice"), QStringLiteral("ai_api_key")),
             QStringLiteral("sk-legacy"));
    QCOMPARE(legacyStore.smtpSenderPassword(), QStringLiteral("legacy-smtp"));
    SecretVault::setProtectionFailureForTesting(false);

    // An empty legacy entry is simply retired — there is nothing to protect.
    {
        QSettings empty(dir.path() + QStringLiteral("/empty.ini"), QSettings::IniFormat);
        empty.setValue(QStringLiteral("User_alice/pref_ai_api_key"), QString());
        empty.sync();
    }
    QSettings emptySettings(dir.path() + QStringLiteral("/empty.ini"), QSettings::IniFormat);
    UserSettingsStore emptyStore;
    emptyStore.attachSettings(&emptySettings);
    QCOMPARE(emptyStore.migrateLegacySecrets(), 0);
    QVERIFY(!emptySettings.contains(QStringLiteral("User_alice/pref_ai_api_key")));

    // The test seam is off again.
    QVERIFY(!SecretVault::protectionFailsForTesting());
}

QTEST_GUILESS_MAIN(TestSecretProtection)
#include "TestSecretProtection.moc"
