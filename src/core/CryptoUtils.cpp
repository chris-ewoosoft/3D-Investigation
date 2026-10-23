#include "CryptoUtils.h"

#include <QCryptographicHash>
#include <QRandomGenerator>

#include <openssl/evp.h>

namespace {

// Password storage format: "pbkdf2$<iterations>$<salt-hex>$<hash-hex>".
// Legacy values are bare SHA-256 hex strings and are transparently upgraded
// the next time the user proves the password at login.
constexpr const char *kPbkdf2Prefix = "pbkdf2$";
constexpr int kPbkdf2Iterations = 100000;
constexpr int kSaltBytes = 16;
constexpr char kHashPrefixShell = '$';

/// The largest work factor a stored hash may ask for. The value comes from
/// the credential file, so a tampered file must not be able to turn a login
/// into a denial of service (the work happens while the user-manager mutex is
/// held). The current value is far below the ceiling.
constexpr int kMaxPbkdf2Iterations = 1000000;

/// The verification is never inlined: the AppCore unity build would fold it
/// into its caller and the coverage exporter would then report the
/// verification line as never executed (see tests/README.md, "Unity build vs.
/// measurability").
#if defined(_MSC_VER)
#  define CRYPTO_NOINLINE __declspec(noinline)
#else
#  define CRYPTO_NOINLINE
#endif

/// PBKDF2-HMAC-SHA256 over OpenSSL's implementation. The hand-written variant
/// was ~25x slower (about 1.2 s per hash in pure Qt), which froze the GUI
/// thread during every login; OpenSSL does the same work in ~50 ms.
///
/// @return the derived key, or an empty array when the derivation failed.
QByteArray pbkdf2HmacSha256(const QByteArray &password, const QByteArray &salt,
                             int iterations)
{
    QByteArray derived(32, Qt::Uninitialized);
    const int result = PKCS5_PBKDF2_HMAC(password.constData(), password.size(),
                                         reinterpret_cast<const unsigned char *>(salt.constData()),
                                         salt.size(), iterations, EVP_sha256(),
                                         derived.size(),
                                         reinterpret_cast<unsigned char *>(derived.data()));
    if (result != 1) return {};
    return derived;
}

} // namespace

namespace CryptoUtils {

QString hashPassword(const QString &password)
{
    quint32 words[kSaltBytes / 4];
    QRandomGenerator::system()->generate(words, words + kSaltBytes / 4);
    const QByteArray salt(reinterpret_cast<const char *>(words), sizeof(words));
    const QByteArray digest = pbkdf2HmacSha256(password.toUtf8(), salt, kPbkdf2Iterations);
    if (digest.isEmpty()) return {};
    return QString::fromLatin1(kPbkdf2Prefix) + QString::number(kPbkdf2Iterations)
           + kHashPrefixShell + QString::fromLatin1(salt.toHex())
           + kHashPrefixShell + QString::fromLatin1(digest.toHex());
}

CRYPTO_NOINLINE bool verifyPassword(const QString &password, const QString &storedHash)
{
    // The two credential formats end in the same comparison, but they are
    // kept in separate branches: the compiler would otherwise fold the two
    // returns into one block of code, and the coverage exporter would then
    // report the modern-format comparison as never executed.
    bool matches = false;
    if (storedHash.startsWith(QString::fromLatin1(kPbkdf2Prefix))) {
        const QStringList parts =
            storedHash.mid(sizeof(kPbkdf2Prefix) - 1).split(kHashPrefixShell);
        if (parts.size() != 3) return false;
        bool okIterations = false;
        const int iterations = parts[0].toInt(&okIterations);
        const QByteArray salt = QByteArray::fromHex(parts[1].toLatin1());
        const QByteArray expected = QByteArray::fromHex(parts[2].toLatin1());
        if (!okIterations || iterations <= 0 || iterations > kMaxPbkdf2Iterations
            || salt.size() < kSaltBytes / 2 || expected.isEmpty())
            return false;
        const QByteArray candidate = pbkdf2HmacSha256(password.toUtf8(), salt, iterations);
        if (!candidate.isEmpty())
            matches = constantTimeEquals(expected, candidate);
    } else {
        // Legacy single-pass SHA-256 format; login() upgrades to PBKDF2
        // after this helper accepts the password. The comparison is constant
        // time as well: the legacy format is still in the field, and a timing
        // oracle would work just as well on it.
        matches = constantTimeEquals(sha256Hex(password).toLatin1(), storedHash.toLatin1());
    }
    return matches;
}

bool isModernPasswordHash(const QString &storedHash)
{
    return storedHash.startsWith(QString::fromLatin1(kPbkdf2Prefix));
}

QString sha256Hex(const QString &value)
{
    return QString::fromLatin1(QCryptographicHash::hash(
        value.toUtf8(), QCryptographicHash::Sha256).toHex());
}

bool constantTimeEquals(const QByteArray &a, const QByteArray &b)
{
    if (a.size() != b.size()) return false;
    // The accumulator is volatile so the compiler cannot rewrite the loop
    // into an early-exit memcmp: the comparison must not leak where the
    // first difference is.
    volatile uint8_t diff = 0;
    for (int i = 0; i < a.size(); ++i)
        diff |= uint8_t(a[i]) ^ uint8_t(b[i]);
    return diff == 0;
}

} // namespace CryptoUtils
