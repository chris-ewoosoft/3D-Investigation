#include "AgentActionManifest.h"

#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include "AppConfig.h"

bool AgentActionManifest::canonicalize(QString &action, QVariantMap &parameters, QString *error, IAppPaths* paths) {
    const QString root = paths ? paths->projectRootDir() : QString();
    const QString path = root + "/Config/agent_action_manifest.json";
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) {
        if (error) *error = "Unable to load agent action manifest";
        return false;
    }
    const QJsonArray actions = QJsonDocument::fromJson(file.readAll()).object().value("actions").toArray();
    QJsonObject definition;
    for (const QJsonValue &value : actions) {
        const QJsonObject candidate = value.toObject();
        bool isAlias = false;
        for (const QJsonValue &alias : candidate.value("aliases").toArray()) {
            isAlias |= alias.toString() == action;
        }
        if (candidate.value("id").toString() == action || isAlias) {
            definition = candidate;
            action = candidate.value("id").toString();
            break;
        }
    }
    if (definition.isEmpty()) {
        if (error) *error = "Unsupported agent action: " + action;
        return false;
    }
    const QJsonObject fields = definition.value("parameters").toObject();
    for (auto it = fields.begin(); it != fields.end(); ++it) {
        const QJsonObject field = it.value().toObject();
        const QVariant value = parameters.value(it.key());
        if (field.value("required").toBool() && (!value.isValid() || value.toString().isEmpty())) {
            if (error) *error = action + " requires " + it.key();
            return false;
        }
        const QJsonArray allowed = field.value("enum").toArray();
        if (value.isValid() && !allowed.isEmpty()) {
            bool matches = false;
            for (const QJsonValue &allowedValue : allowed) matches |= allowedValue.toString() == value.toString();
            if (!matches) {
                if (error) *error = action + "." + it.key() + " has an invalid value";
                return false;
            }
        }
    }
    parameters["action"] = action;
    return true;
}

// Legacy overload for bootstrap / pre-registry usage (deprecated, prefer IAppPaths overload).
bool AgentActionManifest::canonicalize(QString &action, QVariantMap &parameters, QString *error) {
    return canonicalize(action, parameters, error, &AppConfig::instance());
}

namespace {
const QStringList kImageFilters{"*.png", "*.jpg", "*.jpeg", "*.bmp"};
const QStringList kVideoFilters{"*.mp4", "*.avi", "*.mkv"};
}

QVariantMap AgentActionManifest::sampleData(IAppPaths* paths) {
    if (!paths) return {};
    const QString path = paths->projectRootDir() + "/Config/agent_action_manifest.json";
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) return {};
    return QJsonDocument::fromJson(file.readAll())
        .object().value("sample_data").toObject().toVariantMap();
}

void AgentActionManifest::applySampleDefaults(const QString &action, QVariantMap &parameters,
                                              IAppPaths* paths) {
    if (!paths) return;
    const QVariantMap samples = sampleData(paths);
    if (samples.isEmpty()) return;

    const QDir root(paths->projectRootDir());
    const QString imagesDir = samples.value("images_dir").toString();

    const auto fillExact = [&](const QString &key, const QString &relative) {
        if (relative.isEmpty() || parameters.contains(key)) return;
        const QString resolved = root.absoluteFilePath(relative);
        if (QFileInfo::exists(resolved)) parameters.insert(key, resolved);
    };
    const auto fillFirstFile = [&](const QString &key, const QString &relativeDir,
                                   const QStringList &filters) {
        if (relativeDir.isEmpty() || parameters.contains(key)) return;
        const QDir dir(root.filePath(relativeDir));
        const QStringList files = dir.entryList(filters, QDir::Files, QDir::Name);
        if (!files.isEmpty()) parameters.insert(key, dir.absoluteFilePath(files.first()));
    };

    if (action == QLatin1String("viewer.load_2d") ||
        action == QLatin1String("ai.run_detection") ||
        action == QLatin1String("ai.run_segmentation")) {
        fillFirstFile("path", imagesDir, kImageFilters);
    } else if (action == QLatin1String("viewer.load_3d")) {
        fillExact("path", samples.value("model_obj").toString());
    } else if (action == QLatin1String("viewer.load_dicom")) {
        fillExact("path", samples.value("dicom_dir").toString());
    } else if (action == QLatin1String("ai.video_tracking")) {
        fillFirstFile("path", samples.value("video_dir").toString(), kVideoFilters);
    } else if (action == QLatin1String("reconstruction.load_images")) {
        fillExact("folder", imagesDir);
    }
}
