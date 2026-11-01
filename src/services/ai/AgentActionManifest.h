#ifndef AGENTACTIONMANIFEST_H
#define AGENTACTIONMANIFEST_H

#include <QString>
#include <QVariantMap>
#include "IAppPaths.h"
#include "Global.h"

// Loads Config/agent_action_manifest.json, the same source of truth consumed
// by the Python AI server.  This prevents Qt from dispatching stale aliases.
class APP_EXPORT AgentActionManifest final {
public:
    static bool canonicalize(QString &action, QVariantMap &parameters, QString *error, IAppPaths* paths);

    /// Demo fixtures ("sample_data" block) from the manifest; empty map when
    /// the file or the block is missing.
    static QVariantMap sampleData(IAppPaths* paths);

    /// Fills action-specific default inputs from "sample_data" when the caller
    /// did not supply them (agents normally send no file paths, so the desktop
    /// falls back to the packaged demo data).  Never overwrites a parameter the
    /// caller provided:
    ///   viewer.load_2d / ai.run_detection / ai.run_segmentation → "path"   (first image in images_dir)
    ///   viewer.load_3d                                          → "path"   (model_obj)
    ///   viewer.load_dicom                                       → "path"   (dicom_dir)
    ///   ai.video_tracking                                       → "path"   (first video in video_dir)
    ///   reconstruction.load_images                              → "folder" (images_dir)
    static void applySampleDefaults(const QString &action, QVariantMap &parameters,
                                    IAppPaths* paths);
};

#endif // AGENTACTIONMANIFEST_H
