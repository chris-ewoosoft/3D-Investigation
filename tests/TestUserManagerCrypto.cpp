#include <QtTest>
#include <QTemporaryDir>
#include <QSettings>
#include <QCryptographicHash>
#include <QFile>

#include "UserManager.h"
#include "SecretVault.h"

/**
 * @brief Regression tests for UserManager password hashing and SecretVault.
 *
 * Covers the P0 security contract:
 *  - fresh hashes are salted PBKDF2 (never bare SHA-256),
 *  - legacy plaintext-hash accounts transparently upgrade at login,
 *  - wrong passwords are rejected for both formats,
 *  - SecretVault round-trips and never stores unprotected secrets verbatim.
 */
class TestUserManagerCrypto : public QObject {
    Q_OBJECT

private slots:
    void freshAdminHashUsesPbkdf2();
    void legacyHashIsTransparentlyUpgraded();
    void changePasswordFlowRejectsWrongOldPassword();
    void secretVaultRoundtrip();
    void secretVaultLegacyPlaintextPassthrough();
    void licenseKeysStoredEncrypted();

private:
    static QString legacySha256Hex(const QString &password) {
        return QString::fromLatin1(
            QCryptographicHash::hash(password.toUtf8(), QCryptographicHash::Sha256).toHex());
    }
};

void TestUserManagerCrypto::freshAdminHashUsesPbkdf2()
{
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    QString ini = dir.path() + "/users.ini";

    QString err;
    QVERIFY(UserManager::instance()->loadConfig(ini));
    QVERIFY(UserManager::instance()->login("admin", "1", err));
    QVERIFY(err.isEmpty());

    err.clear();
    const QString stored = UserManager::instance()->userInfo("admin").passwordHash;
    QVERIFY(stored.startsWith("pbkdf2$100000$"));
    QVERIFY(!UserManager::instance()->login("admin", "wrong-password", err));
    QVERIFY(!err.isEmpty());
    UserManager::instance()->logout();
}


void TestUserManagerCrypto::legacyHashIsTransparentlyUpgraded()
{
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    QString ini = dir.path() + "/users.ini";
    {
        QSettings seed(ini, QSettings::IniFormat);
        seed.setValue("Users/usernames", QStringList{"legacy"});
        seed.setValue("User_legacy/password_hash", legacySha256Hex("LegacyPass1!"));
        seed.sync();
    }

    QString err;
    QVERIFY(UserManager::instance()->loadConfig(ini));
    QVERIFY(!UserManager::instance()->login("legacy", "wrong1", err));
    QVERIFY(!err.isEmpty());

    err.clear();
    QVERIFY(UserManager::instance()->login("legacy", "LegacyPass1!", err));
    QSettings stored(ini, QSettings::IniFormat);
    QVERIFY(stored.value("User_legacy/password_hash").toString().startsWith("pbkdf2$"));
}

void TestUserManagerCrypto::changePasswordFlowRejectsWrongOldPassword()
{
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    QString ini = dir.path() + "/users.ini";
    QString err;
    QVERIFY(UserManager::instance()->loadConfig(ini));

    UserInfo info;
    info.username = "p1";
    info.email    = "p1@example.com";
    info.role     = UserRole::User;
    QVERIFY(UserManager::instance()->addUser(info, "Secret1!", err));

    err.clear();
    QVERIFY(!UserManager::instance()->changePassword("p1", "not-the-old", "NewPass1!", err));
    QVERIFY(!err.isEmpty());

    err.clear();
    QVERIFY(UserManager::instance()->changePassword("p1", "Secret1!", "NewPass1!", err));
    QVERIFY(err.isEmpty());

    err.clear();
    QVERIFY(!UserManager::instance()->login("p1", "Secret1!", err));
    err.clear();
    QVERIFY(UserManager::instance()->login("p1", "NewPass1!", err));
}

void TestUserManagerCrypto::secretVaultRoundtrip()
{
    const QString secret = "app-password-示例-🔐";
    const QString stored = SecretVault::encrypt(secret);
    // Never store the raw secret verbatim.
    QVERIFY(!stored.contains(secret));
    QVERIFY(stored.startsWith("enc1:") || stored.startsWith("plain:"));
    QCOMPARE(SecretVault::decrypt(stored), secret);
    QVERIFY(SecretVault::decrypt("").isEmpty());
}

void TestUserManagerCrypto::secretVaultLegacyPlaintextPassthrough()
{
    // Values written before the vault existed are passed through so old
    // Config.ini files keep working until the next save migrates them.
    QCOMPARE(SecretVault::decrypt("old-plaintext"), QLatin1String("old-plaintext"));
    QCOMPARE(SecretVault::decrypt("plain:abc"), QLatin1String("abc"));
    QVERIFY(SecretVault::decrypt("").isEmpty());
}

void TestUserManagerCrypto::licenseKeysStoredEncrypted()
{
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    const QString ini = dir.path() + "/users.ini";

    // Seed the legacy plaintext format: key list + plaintext key in paths.
    {
        QSettings seed(ini, QSettings::IniFormat);
        seed.setValue("LicenseKeys/keys", QStringList{"LEGACY-KEY-XYZ"});
        seed.setValue("LicenseKeys/LEGACY-KEY-XYZ_used", true);
        seed.setValue("LicenseKeys/LEGACY-KEY-XYZ_user", "admin");
        seed.sync();
    }

    QVERIFY(UserManager::instance()->loadConfig(ini));

    // The legacy key survives the migration in memory...
    QVERIFY(UserManager::instance()->availableLicenseKeys().contains("LEGACY-KEY-XYZ"));

    // ...and new keys work through the normal generation flow.
    const QString fresh = UserManager::instance()->generateLicenseKey();
    QVERIFY(!fresh.isEmpty());
    QVERIFY(UserManager::instance()->availableLicenseKeys().contains(fresh));
    UserManager::instance()->saveConfig();

    // The persisted INI must contain no plaintext keys — values are DPAPI
    // blobs and paths are hashed.
    QFile f(ini);
    QVERIFY(f.open(QIODevice::ReadOnly));
    const QString content = QString::fromUtf8(f.readAll());
    f.close();
    QVERIFY(!content.contains("LEGACY-KEY-XYZ"));
    QVERIFY(!content.contains(fresh));
    QVERIFY(content.contains("enc1:"));
}

QTEST_MAIN(TestUserManagerCrypto)
#include "TestUserManagerCrypto.moc"
