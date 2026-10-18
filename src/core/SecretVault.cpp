#include "SecretVault.h"

#include <QByteArray>
#include <QDebug>

#ifdef Q_OS_WIN
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <wincrypt.h>
#endif

namespace {
constexpr const char *kEncodedPrefix = "enc1:";
constexpr const char *kPlainPrefix = "plain:";
}

QString SecretVault::encrypt(const QString &plainValue)
{
    if (plainValue.isEmpty()) return {};

#ifdef Q_OS_WIN
    // DPAPI CurrentUser scope: the blob is bound to this user profile on this
    // machine, so the INI file alone is useless when copied elsewhere.
    const std::wstring wideValue = plainValue.toStdWString();
    DATA_BLOB input;
    input.pbData = reinterpret_cast<BYTE *>(const_cast<wchar_t *>(wideValue.data()));
    input.cbData = static_cast<DWORD>(wideValue.size() * sizeof(wchar_t));

    DATA_BLOB output;
    if (CryptProtectData(&input, L"3D-Reconstruction secret", nullptr, nullptr,
                         nullptr, CRYPTPROTECT_UI_FORBIDDEN, &output) && output.pbData) {
        const QByteArray blob(reinterpret_cast<const char *>(output.pbData),
                              static_cast<int>(output.cbData));
        LocalFree(output.pbData);
        return QString::fromLatin1(kEncodedPrefix) + blob.toBase64();
    }
    qWarning() << "SecretVault: CryptProtectData failed, error =" << GetLastError();
#else
    qWarning() << "SecretVault: no OS protection available on this platform; "
                  "storing value marked as unencrypted plaintext.";
#endif
    return QString::fromLatin1(kPlainPrefix) + plainValue;
}

QString SecretVault::decrypt(const QString &storedValue)
{
    if (storedValue.isEmpty()) return {};

    if (!storedValue.startsWith(QString::fromLatin1(kEncodedPrefix))) {
        if (storedValue.startsWith(QString::fromLatin1(kPlainPrefix)))
            return storedValue.mid(QString::fromLatin1(kPlainPrefix).size());
        // Legacy value written before the vault existed: return as-is so old
        // Config.ini files keep working until the next save migrates them.
        return storedValue;
    }

#ifdef Q_OS_WIN
    const QByteArray blob = QByteArray::fromBase64(
        storedValue.mid(QString::fromLatin1(kEncodedPrefix).size()).toLatin1());
    DATA_BLOB input;
    input.pbData = reinterpret_cast<BYTE *>(const_cast<char *>(blob.constData()));
    input.cbData = static_cast<DWORD>(blob.size());

    DATA_BLOB output;
    if (CryptUnprotectData(&input, nullptr, nullptr, nullptr, nullptr,
                           CRYPTPROTECT_UI_FORBIDDEN, &output) && output.pbData) {
        const wchar_t *wideData = reinterpret_cast<const wchar_t *>(output.pbData);
        const QString result = QString::fromWCharArray(
            wideData, static_cast<qsizetype>(output.cbData / sizeof(wchar_t)));
        LocalFree(output.pbData);
        return result;
    }
    // Blob unreadable (different user/machine): report empty rather than
    // surfacing garbage as if it were valid data.
    qWarning() << "SecretVault: CryptUnprotectData failed, error =" << GetLastError();
    return {};
#else
    return {};
#endif
}
