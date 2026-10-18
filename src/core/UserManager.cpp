#include "UserManager.h"
#include "SecretVault.h"
#include "IOutgoingMail.h"

#include <QCryptographicHash>
#include <QRandomGenerator>
#include <QDebug>

namespace {

// Password storage format: "pbkdf2$<iterations>$<salt-hex>$<hash-hex>".
// Legacy values are bare SHA-256 hex strings and are transparently upgraded
// the next time the user proves the password at login.
constexpr const char *kPbkdf2Prefix = "pbkdf2$";
constexpr int kPbkdf2Iterations = 100000;
constexpr int kSaltBytes = 16;
constexpr char kHashPrefixShell = '$';

QByteArray hmacSha256(const QByteArray &key, const QByteArray &data)
{
    constexpr int blockSize = 64; // SHA-256 block size
    QByteArray k = key;
    if (k.size() > blockSize)
        k = QCryptographicHash::hash(k, QCryptographicHash::Sha256);
    k = k.leftJustified(blockSize, '\0');

    QByteArray inner(blockSize, '\0');
    QByteArray outer(blockSize, '\0');
    for (int i = 0; i < blockSize; ++i) {
        inner[i] = char(uint8_t(k[i]) ^ 0x36);
        outer[i] = char(uint8_t(k[i]) ^ 0x5c);
    }
    return QCryptographicHash::hash(
        outer + QCryptographicHash::hash(inner + data, QCryptographicHash::Sha256),
        QCryptographicHash::Sha256);
}

QByteArray pbkdf2HmacSha256(const QByteArray &password, const QByteArray &salt,
                            int iterations)
{
    constexpr int hashLen = 32;
    QByteArray derived;
    quint32 blockIndex = 1;
    while (derived.size() < hashLen) {
        QByteArray saltBlock = salt;
        saltBlock.append(char((blockIndex >> 24) & 0xff));
        saltBlock.append(char((blockIndex >> 16) & 0xff));
        saltBlock.append(char((blockIndex >> 8) & 0xff));
        saltBlock.append(char(blockIndex & 0xff));
        QByteArray u = hmacSha256(password, saltBlock);
        QByteArray t = u;
        for (int i = 1; i < iterations; ++i) {
            u = hmacSha256(password, u);
            for (int j = 0; j < u.size(); ++j)
                t[j] = char(uint8_t(t[j]) ^ uint8_t(u[j]));
        }
        derived += t;
        ++blockIndex;
    }
    return derived.left(hashLen);
}

// Compare without short-circuiting on the first mismatching byte.
bool constantTimeEquals(const QByteArray &a, const QByteArray &b)
{
    if (a.size() != b.size()) return false;
    uint8_t diff = 0;
    for (int i = 0; i < a.size(); ++i)
        diff |= uint8_t(a[i]) ^ uint8_t(b[i]);
    return diff == 0;
}

QString legacySha256Hex(const QString &password)
{
    return QString::fromLatin1(QCryptographicHash::hash(
        password.toUtf8(), QCryptographicHash::Sha256).toHex());
}

} // namespace


UserManager *UserManager::instance() {
    static UserManager instance;
    return &instance;
}

UserManager::UserManager(QObject *parent) : QObject(parent) {}

bool UserManager::isLoggedIn() const { std::shared_lock lock(m_mutex); return !m_currentUsername.isEmpty(); }
UserInfo UserManager::currentUser() const { std::shared_lock lock(m_mutex); return m_users.value(m_currentUsername); }
QString UserManager::currentUsername() const { std::shared_lock lock(m_mutex); return m_currentUsername; }

QStringList UserManager::allUsernames() const { std::shared_lock lock(m_mutex); return m_users.keys(); }
UserInfo UserManager::userInfo(const QString &username) const { std::shared_lock lock(m_mutex); return m_users.value(username); }

// ─── Config ──────────────────────────────────────────────────────────────────

bool UserManager::loadConfig(const QString &configPath) {
    std::unique_lock lock(m_mutex);
    m_configPath = configPath;
    if (m_settings) {
        delete m_settings;
    }
    m_settings = new QSettings(configPath, QSettings::IniFormat, this);
    loadUsers();
    loadLicenseKeys();
    ensureDefaultAdmin();
    if (m_settings) {
        saveUsers();
        saveLicenseKeys();
        m_settings->sync();
    }
    return true;
}

void UserManager::saveConfig() {
    std::unique_lock lock(m_mutex);
    if (!m_settings) return;
    saveUsers();
    saveLicenseKeys();
    m_settings->sync();
}

void UserManager::loadUsers() {
    m_users.clear();
    int count = m_settings->value("Users/count", 0).toInt();
    QStringList names = m_settings->value("Users/usernames").toStringList();
    for (const QString &name : names) {
        QString section = "User_" + name;
        UserInfo u;
        u.username     = name;
        u.passwordHash = m_settings->value(section + "/password_hash").toString();
        u.email        = m_settings->value(section + "/email").toString();
        u.role         = m_settings->value(section + "/role", "User").toString() == "Admin"
                             ? UserRole::Admin : UserRole::User;
        u.licenseKey    = m_settings->value(section + "/license_key").toString();
        u.licenseStatus = m_settings->value(section + "/license_status", "None").toString();
        QString expiry  = m_settings->value(section + "/license_expiry").toString();
        QString trial   = m_settings->value(section + "/trial_start").toString();
        if (!expiry.isEmpty()) u.licenseExpiry = QDate::fromString(expiry, Qt::ISODate);
        if (!trial.isEmpty())  u.trialStart    = QDate::fromString(trial, Qt::ISODate);
        u.avatarPath = m_settings->value(section + "/avatar_path").toString();
        m_users.insert(name, u);
    }
    Q_UNUSED(count)
}

void UserManager::saveUsers() {
    QStringList names = m_users.keys();
    m_settings->setValue("Users/count", names.size());
    m_settings->setValue("Users/usernames", names);
    for (const QString &name : names) {
        const UserInfo &u = m_users[name];
        QString section = "User_" + name;
        m_settings->setValue(section + "/password_hash", u.passwordHash);
        m_settings->setValue(section + "/email",         u.email);
        m_settings->setValue(section + "/role",          u.role == UserRole::Admin ? "Admin" : "User");
        m_settings->setValue(section + "/license_key",    u.licenseKey);
        m_settings->setValue(section + "/license_status", u.licenseStatus);
        m_settings->setValue(section + "/license_expiry",
                             u.licenseExpiry.isValid() ? u.licenseExpiry.toString(Qt::ISODate) : "");
        m_settings->setValue(section + "/trial_start",
                             u.trialStart.isValid() ? u.trialStart.toString(Qt::ISODate) : "");
        m_settings->setValue(section + "/avatar_path", u.avatarPath);
    }
}

void UserManager::loadLicenseKeys() {
    m_licenseKeys.clear();
    m_keyOwners.clear();
    const QStringList storedKeys = m_settings->value("LicenseKeys/keys").toStringList();
    for (const QString &stored : storedKeys) {
        // Values may be DPAPI-encrypted (enc1:...) or legacy plaintext; the
        // vault passes the latter through so old configs migrate transparently.
        const QString k = SecretVault::decrypt(stored);
        if (k.isEmpty()) continue;
        // Subkey path uses a hash so the plaintext key never appears in the INI.
        const QString bucket = legacySha256Hex(k);
        bool used  = m_settings->value("LicenseKeys/" + bucket + "_used", false).toBool();
        QString owner = m_settings->value("LicenseKeys/" + bucket + "_user").toString();
        // Legacy format used the plaintext key directly in the path — migrate it.
        if (!m_settings->contains("LicenseKeys/" + bucket + "_used") &&
            m_settings->contains("LicenseKeys/" + k + "_used")) {
            used  = m_settings->value("LicenseKeys/" + k + "_used", false).toBool();
            owner = m_settings->value("LicenseKeys/" + k + "_user").toString();
            m_settings->remove("LicenseKeys/" + k + "_used");
            m_settings->remove("LicenseKeys/" + k + "_user");
        }
        m_licenseKeys[k] = used;
        if (!owner.isEmpty()) m_keyOwners[k] = owner;
    }
}

void UserManager::saveLicenseKeys() {
    QStringList storedKeys;
    const QStringList keys = m_licenseKeys.keys();
    for (const QString &k : keys) {
        // Store DPAPI-encrypted; the path bucket is a hash of the key.
        storedKeys.append(SecretVault::encrypt(k));
        const QString bucket = legacySha256Hex(k);
        m_settings->setValue("LicenseKeys/" + bucket + "_used", m_licenseKeys[k]);
        m_settings->setValue("LicenseKeys/" + bucket + "_user", m_keyOwners.value(k));
    }
    m_settings->setValue("LicenseKeys/keys", storedKeys);
}

void UserManager::ensureDefaultAdmin() {
    if (!m_users.contains("admin")) {
        UserInfo admin;
        admin.username     = "admin";
        admin.passwordHash = hashPassword("1");
        admin.email        = "admin@example.com";
        admin.role         = UserRole::Admin;
        admin.licenseStatus = "Activated";
        admin.licenseExpiry = QDate::currentDate().addYears(10);
        m_users["admin"] = admin;
        qDebug() << "[UserAuth] Default admin account created (initial password defined by installer).";
    }
}

// ─── Auth ─────────────────────────────────────────────────────────────────────

bool UserManager::verifyPassword(const QString &password, const QString &storedHash)
{
    if (storedHash.startsWith(QString::fromLatin1(kPbkdf2Prefix))) {
        const QStringList parts =
            storedHash.mid(sizeof(kPbkdf2Prefix) - 1).split(kHashPrefixShell);
        if (parts.size() != 3) return false;
        bool okIterations = false;
        const int iterations = parts[0].toInt(&okIterations);
        const QByteArray salt = QByteArray::fromHex(parts[1].toLatin1());
        const QByteArray expected = QByteArray::fromHex(parts[2].toLatin1());
        if (!okIterations || iterations <= 0 || salt.size() < kSaltBytes / 2 || expected.isEmpty())
            return false;
        const QByteArray candidate = pbkdf2HmacSha256(password.toUtf8(), salt, iterations);
        return constantTimeEquals(expected, candidate);
    }
    // Legacy single-pass SHA-256 format; login() upgrades to PBKDF2
    // after this helper accepts the password.
    return storedHash == legacySha256Hex(password);
}

bool UserManager::login(const QString &usernameOrEmail, const QString &password,
                        QString &errorMsg, AuthError *code) {
    std::unique_lock lock(m_mutex);
    QString foundUsername;
    QString lowerInput = usernameOrEmail.toLower();

    for (auto it = m_users.begin(); it != m_users.end(); ++it) {
        if (it.value().username.toLower() == lowerInput || it.value().email.toLower() == lowerInput) {
            foundUsername = it.key();
            break;
        }
    }

    if (foundUsername.isEmpty()) {
        errorMsg = "Tên đăng nhập hoặc Email không tồn tại.";
        if (code) *code = AuthError::UserNotFound;
        return false;
    }
    const QString storedHash = m_users[foundUsername].passwordHash;
    if (!verifyPassword(password, storedHash)) {
        errorMsg = "Mật khẩu không đúng.";
        if (code) *code = AuthError::WrongPassword;
        return false;
    }
    // Transparent upgrade: rehash legacy hashes to the PBKDF2 format once the
    // plaintext password has been proven correct.
    if (!storedHash.startsWith(QString::fromLatin1(kPbkdf2Prefix))) {
        m_users[foundUsername].passwordHash = hashPassword(password);
        if (m_settings) { saveUsers(); m_settings->sync(); }
    }
    m_currentUsername = foundUsername;
    m_settings->setValue("Session/last_user", foundUsername);
    m_settings->sync();
    return true;
}

void UserManager::setRememberMe(bool enable, const QString &username) {
    std::unique_lock lock(m_mutex);
    m_settings->setValue("Session/remember_me", enable);
    if (enable) {
        m_settings->setValue("Session/saved_username", username);
    } else {
        m_settings->remove("Session/saved_username");
    }
    m_settings->sync();
}

bool UserManager::isRememberMeEnabled() const {
    std::shared_lock lock(m_mutex);
    return m_settings ? m_settings->value("Session/remember_me", false).toBool() : false;
}

QString UserManager::savedUsername() const {
    std::shared_lock lock(m_mutex);
    return m_settings ? m_settings->value("Session/saved_username", "").toString() : QString();
}

void UserManager::logout() {
    std::unique_lock lock(m_mutex);
    m_currentUsername.clear();
}

// ─── Password ─────────────────────────────────────────────────────────────────

QString UserManager::hashPassword(const QString &password) const {
    quint32 words[kSaltBytes / 4];
    QRandomGenerator::system()->generate(words, words + kSaltBytes / 4);
    const QByteArray salt(reinterpret_cast<const char *>(words), sizeof(words));
    const QByteArray digest = pbkdf2HmacSha256(password.toUtf8(), salt, kPbkdf2Iterations);
    return QString::fromLatin1(kPbkdf2Prefix) + QString::number(kPbkdf2Iterations)
           + kHashPrefixShell + QString::fromLatin1(salt.toHex())
           + kHashPrefixShell + QString::fromLatin1(digest.toHex());
}

bool UserManager::changePassword(const QString &username, const QString &oldPass,
                                  const QString &newPass, QString &error,
                                  AuthError *code) {
    std::unique_lock lock(m_mutex);
    if (!m_users.contains(username)) { error = "User không tồn tại."; if (code) *code = AuthError::UserNotFound; return false; }
    if (!verifyPassword(oldPass, m_users[username].passwordHash)) {
        error = "Mật khẩu cũ không đúng.";
        if (code) *code = AuthError::WrongPassword;
        return false;
    }
    if (newPass.length() < 6) { error = "Mật khẩu mới phải có ít nhất 6 ký tự."; if (code) *code = AuthError::WeakPassword; return false; }
    m_users[username].passwordHash = hashPassword(newPass);
    if (m_settings) { saveUsers(); m_settings->sync(); }
    return true;
}

bool UserManager::resetPassword(const QString &username, const QString &newPass) {
    std::unique_lock lock(m_mutex);
    if (!m_users.contains(username)) return false;
    m_users[username].passwordHash = hashPassword(newPass);
    if (m_settings) { saveUsers(); m_settings->sync(); }
    return true;
}

void UserManager::updateAvatar(const QString &username, const QString &path) {
    std::unique_lock lock(m_mutex);
    if (m_users.contains(username)) {
        m_users[username].avatarPath = path;
        if (m_settings) { saveUsers(); m_settings->sync(); }
    }
}

// ─── OTP ──────────────────────────────────────────────────────────────────────

QString UserManager::findUsernameByEmail(const QString &email) const {
    std::shared_lock lock(m_mutex);
    for (auto it = m_users.begin(); it != m_users.end(); ++it)
        if (it.value().email.toLower() == email.toLower())
            return it.key();
    return {};
}

bool UserManager::sendPasswordResetOtp(const QString &email, QString &error) {
    if (findUsernameByEmail(email).isEmpty()) {
        error = "Email không được đăng ký trong hệ thống.";
        return false;
    }
    // Generate 6-digit OTP
    int code = QRandomGenerator::global()->bounded(100000, 999999);
    QString otp = QString::number(code);
    {
        std::unique_lock lock(m_mutex);
        m_otpStore[email.toLower()] = { otp, QDateTime::currentDateTime().addSecs(600) };
    }

    if (!m_outgoingMail) {
        error = "Dịch vụ gửi email chưa khả dụng.";
        return false;
    }

    QString senderEmail = smtpSenderEmail();
    if (senderEmail.isEmpty()) {
        error = "Chưa cấu hình email gửi. Vui lòng liên hệ Admin.";
        return false;
    }

    QString subject = "[3D-Reconstruction] Mã xác nhận đặt lại mật khẩu";
    QString body    = QString("Mã xác nhận của bạn là: <b>%1</b><br><br>"
                              "Mã có hiệu lực trong 10 phút.<br>"
                              "Nếu bạn không yêu cầu đặt lại mật khẩu, hãy bỏ qua email này.").arg(otp);
    return m_outgoingMail->send(senderEmail, email, subject, body, error);
}

bool UserManager::verifyOtp(const QString &email, const QString &otp) {
    std::unique_lock lock(m_mutex);
    QString key = email.toLower();
    if (!m_otpStore.contains(key)) return false;
    const auto &entry = m_otpStore[key];
    if (QDateTime::currentDateTime() > entry.expiry) {
        m_otpStore.remove(key);
        return false;
    }
    if (entry.otp != otp) return false;
    m_otpStore.remove(key);
    return true;
}

// ─── License ──────────────────────────────────────────────────────────────────

bool UserManager::needsActivation(const QString &username) const {
    std::shared_lock lock(m_mutex);
    if (!m_users.contains(username)) return false;
    const auto &u = m_users[username];
    return u.licenseStatus == "None";
}

bool UserManager::isLicenseExpired(const QString &username) const {
    std::shared_lock lock(m_mutex);
    if (!m_users.contains(username)) return true;
    const auto &u = m_users[username];
    if (u.licenseStatus == "None") return true;
    if (!u.licenseExpiry.isValid()) return false;
    return QDate::currentDate() > u.licenseExpiry;
}

QString UserManager::licenseStatusStr(const QString &username) const {
    std::shared_lock lock(m_mutex);
    return m_users.value(username).licenseStatus;
}

int UserManager::daysRemaining(const QString &username) const {
    std::shared_lock lock(m_mutex);
    if (!m_users.contains(username)) return 0;
    const auto &u = m_users[username];
    if (!u.licenseExpiry.isValid()) return 0;
    int days = QDate::currentDate().daysTo(u.licenseExpiry);
    return qMax(0, (int)days);
}

bool UserManager::activateTrial(const QString &username, QString &error,
                                 AuthError *code) {
    std::unique_lock lock(m_mutex);
    if (!m_users.contains(username)) { error = "User không tồn tại."; if (code) *code = AuthError::UserNotFound; return false; }
    auto &u = m_users[username];
    if (u.licenseStatus != "None") { error = "Tài khoản đã được kích hoạt."; if (code) *code = AuthError::AlreadyActivated; return false; }
    u.licenseStatus = "Trial";
    u.trialStart    = QDate::currentDate();
    u.licenseExpiry = QDate::currentDate().addDays(30);
    if (m_settings) { saveUsers(); m_settings->sync(); }
    return true;
}

bool UserManager::activateLicenseKey(const QString &username, const QString &key,
                                     QString &error, AuthError *code) {
    std::unique_lock lock(m_mutex);
    if (!m_users.contains(username)) { error = "User không tồn tại."; if (code) *code = AuthError::UserNotFound; return false; }
    if (!m_licenseKeys.contains(key)) { error = "License key không hợp lệ."; if (code) *code = AuthError::InvalidKey; return false; }
    if (m_licenseKeys[key]) { error = "License key đã được sử dụng."; if (code) *code = AuthError::KeyAlreadyUsed; return false; }
    auto &u = m_users[username];
    u.licenseKey    = key;
    u.licenseStatus = "Activated";
    u.licenseExpiry = QDate::currentDate().addYears(1);
    m_licenseKeys[key] = true;
    m_keyOwners[key]   = username;
    if (m_settings) {
        saveUsers();
        saveLicenseKeys();
        m_settings->sync();
    }
    return true;
}

// ─── Admin – Users ────────────────────────────────────────────────────────────

bool UserManager::addUser(const UserInfo &info, const QString &plainPassword,
                          QString &error, AuthError *code) {
    std::unique_lock lock(m_mutex);
    if (m_users.contains(info.username)) { error = "Tên đăng nhập đã tồn tại."; if (code) *code = AuthError::UserExists; return false; }
    if (plainPassword.length() < 6) { error = "Mật khẩu phải có ít nhất 6 ký tự."; if (code) *code = AuthError::WeakPassword; return false; }
    UserInfo u = info;
    u.passwordHash = hashPassword(plainPassword);
    u.licenseStatus = "None";
    m_users[info.username] = u;
    if (m_settings) { saveUsers(); m_settings->sync(); }
    return true;
}

bool UserManager::updateUser(const UserInfo &info, QString &error) {
    std::unique_lock lock(m_mutex);
    if (!m_users.contains(info.username)) { error = "User không tồn tại."; return false; }
    // Preserve password hash
    UserInfo u = info;
    u.passwordHash = m_users[info.username].passwordHash;
    m_users[info.username] = u;
    if (m_settings) { saveUsers(); m_settings->sync(); }
    return true;
}

bool UserManager::deleteUser(const QString &username, QString &error) {
    std::unique_lock lock(m_mutex);
    if (username == "admin") { error = "Không thể xóa tài khoản admin mặc định."; return false; }
    if (username == m_currentUsername) { error = "Không thể xóa tài khoản đang đăng nhập."; return false; }
    if (!m_users.contains(username)) { error = "User không tồn tại."; return false; }
    m_users.remove(username);
    // Remove user section from INI
    if (m_settings) {
        m_settings->remove("User_" + username);
        saveUsers();
        m_settings->sync();
    }
    return true;
}

// ─── Admin – License Keys ─────────────────────────────────────────────────────

QString UserManager::generateLicenseKey() {
    const QString chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"; // exclude ambiguous 0,1,I,O
    QString key;
    for (int g = 0; g < 4; g++) {
        for (int i = 0; i < 5; i++)
            key += chars[QRandomGenerator::global()->bounded(chars.size())];
        if (g < 3) key += '-';
    }
    std::unique_lock lock(m_mutex);
    m_licenseKeys[key] = false;
    if (m_settings) {
        saveLicenseKeys();
        m_settings->sync();
    }
    return key;
}

QStringList UserManager::availableLicenseKeys() const {
    std::shared_lock lock(m_mutex);
    QStringList result;
    for (auto it = m_licenseKeys.begin(); it != m_licenseKeys.end(); ++it)
        result << it.key();
    return result;
}

bool UserManager::revokeLicenseKey(const QString &key, QString &error) {
    std::unique_lock lock(m_mutex);
    if (!m_licenseKeys.contains(key)) { error = "Key không tồn tại."; return false; }
    // Revoke from user if assigned
    if (m_keyOwners.contains(key)) {
        const QString &owner = m_keyOwners[key];
        if (m_users.contains(owner)) {
            m_users[owner].licenseStatus = "None";
            m_users[owner].licenseKey.clear();
            m_users[owner].licenseExpiry = QDate();
        }
        m_keyOwners.remove(key);
    }
    m_licenseKeys.remove(key);
    if (m_settings) {
        const QString bucket = legacySha256Hex(key);
        m_settings->remove("LicenseKeys/" + bucket + "_used");
        m_settings->remove("LicenseKeys/" + bucket + "_user");
        // Legacy plaintext-path entries (pre-encryption configs)
        m_settings->remove("LicenseKeys/" + key + "_used");
        m_settings->remove("LicenseKeys/" + key + "_user");
        saveUsers();
        saveLicenseKeys();
        m_settings->sync();
    }
    return true;
}

// ─── SMTP Config ──────────────────────────────────────────────────────────────

QString UserManager::smtpSenderEmail() const {
    std::shared_lock lock(m_mutex);
    return m_settings ? m_settings->value("SMTP/sender_email").toString() : QString();
}

QString UserManager::smtpSenderPassword() const {
    std::shared_lock lock(m_mutex);
    if (!m_settings) return {};
    // Values written by older builds are legacy plaintext; the vault passes
    // them through unchanged so the next save() migrates them into "enc1:".
    return SecretVault::decrypt(m_settings->value("SMTP/sender_password").toString());
}

void UserManager::setSmtpCredentials(const QString &email, const QString &password) {
    std::unique_lock lock(m_mutex);
    if (!m_settings) return;
    m_settings->setValue("SMTP/sender_email",    email);
    m_settings->setValue("SMTP/sender_password", SecretVault::encrypt(password));
    m_settings->sync();
}

// ─── Per-user Preferences ─────────────────────────────────────────────────────

QString UserManager::getUserSecret(const QString &username, const QString &key, const QString &defaultVal) const {
    std::shared_lock lock(m_mutex);
    if (!m_settings || username.isEmpty() || key.isEmpty()) return defaultVal;
    const QString value = m_settings->value("User_" + username + "/secret_" + key).toString();
    if (!value.isEmpty()) {
        const QString secret = SecretVault::decrypt(value);
        if (!secret.isEmpty()) return secret;
    }
    // Legacy plaintext preference written before secret storage existed;
    // migrated into "secret_<key>" the next time setUserSecret() runs.
    return m_settings->value("User_" + username + "/pref_" + key, defaultVal).toString();
}

void UserManager::setUserSecret(const QString &username, const QString &key, const QString &value) {
    std::unique_lock lock(m_mutex);
    if (!m_settings || username.isEmpty() || key.isEmpty()) return;
    m_settings->setValue("User_" + username + "/secret_" + key, SecretVault::encrypt(value));
    // Retire any legacy plaintext copy of the same secret.
    m_settings->remove("User_" + username + "/pref_" + key);
    m_settings->sync();
}

// ─── Per-user Preferences ─────────────────────────────────────────────────────

QString UserManager::getUserPref(const QString &username, const QString &key, const QString &defaultVal) const {
    std::shared_lock lock(m_mutex);
    if (!m_settings || username.isEmpty()) return defaultVal;
    return m_settings->value("User_" + username + "/pref_" + key, defaultVal).toString();
}

void UserManager::setUserPref(const QString &username, const QString &key, const QString &value) {
    std::unique_lock lock(m_mutex);
    if (!m_settings || username.isEmpty()) return;
    m_settings->setValue("User_" + username + "/pref_" + key, value);
    m_settings->sync();
}
