#include "ReconstructionPipeline.h"

#include "CameraParamsParser.h"
#include "FeatureExtractor.h"
#include "IncrementalSfM.h"
#include "ReconstructionPostProcessor.h"
#include "TrackSfM.h"

#include <QDebug>
#include <QFile>
#include <QFileInfo>
#include <QElapsedTimer>
#include <QVector>
#include <QtConcurrent/QtConcurrent>

#include <opencv2/imgcodecs.hpp>

#include <pcl/console/print.h>
#include <pcl/io/ply_io.h>
#include <pcl/point_cloud.h>
#include <pcl/point_types.h>

#include <numeric>

namespace {
constexpr const char *kReconstructionCacheFileName = "recon_cache_quality_v3.ply";
constexpr const char *kMeshCacheFileName            = "recon_mesh_cache_v3.ply";
constexpr const char *kTexturedMeshFileName         = "temple_final_textured_2M.ply";

using PointT      = pcl::PointXYZRGB;
using PointCloudT = pcl::PointCloud<PointT>;
} // namespace

// ─── Constructor ─────────────────────────────────────────────────────────────

ReconstructionPipeline::ReconstructionPipeline() {
    pcl::console::setVerbosityLevel(pcl::console::L_ERROR);
    K_fallback = (cv::Mat_<double>(3, 3) << 1500.0, 0.0, 300.0,
                  0.0, 1500.0, 250.0,
                  0.0, 0.0, 1.0);
    distCoeffs = cv::Mat::zeros(4, 1, CV_64F);
}

ReconstructionPipeline::~ReconstructionPipeline() {}

void ReconstructionPipeline::setConfig(const ReconstructionConfig &cfg) {
    m_config = cfg;
}

// ─── setImages ───────────────────────────────────────────────────────────────

void ReconstructionPipeline::setImages(const std::vector<QString> &imagePaths) {
    imageFiles.clear();
    images.clear();
    for (const auto &path : imagePaths) {
        cv::Mat img = cv::imread(path.toStdString(), cv::IMREAD_COLOR);
        if (img.empty()) {
            qWarning() << "Cannot read image:" << path;
            // The path is DROPPED together with its image: imageFiles and
            // images must stay index-aligned, because the camera parameters
            // are matched against the images by index. Keeping a path whose
            // image failed to load would shift every following pair.
            continue;
        }
        imageFiles.push_back(path);
        images.push_back(img);
    }
    qDebug() << "Loaded" << images.size() << "images";
    // The camera parameters are matched against the (possibly new) image
    // list; the previous match is stale.
    cameraParamsMatched = false;
}

// ─── Camera parameters ↔ images ─────────────────────────────────────────────
//
// The ground-truth camera file names its images; the loaded image list comes
// from the file dialog and is in no particular order. Binding the poses by
// POSITION therefore assigns every pose to the wrong image as soon as the
// two orders differ — silently, and with geometrically wrong output.
//
// The match is done by the image's base name. When the file names cannot be
// matched (a different naming convention, a subset), the pipeline says so
// loudly and falls back to the positional order instead of pretending the
// poses are the ground truth.

bool ReconstructionPipeline::matchCameraParamsToImages() {
    cameraParamsMatched = false;
    if (!hasGroundTruthParams || camParams.empty() || images.empty()) return false;
    if (camParams.size() < images.size()) {
        qWarning() << "Reconstruction: the camera file describes"
                   << camParams.size() << "image(s) but" << images.size()
                   << "were loaded — the ground-truth poses are not used.";
        return false;
    }

    std::vector<CameraParams> ordered;
    ordered.reserve(images.size());
    int matched = 0;
    for (const auto &imagePath : imageFiles) {
        const QString wanted = QFileInfo(imagePath).fileName();
        const auto found = std::find_if(camParams.begin(), camParams.end(),
                                         [&](const CameraParams &p) {
                                             return p.imageName == wanted;
                                         });
        if (found == camParams.end()) {
            qWarning() << "Reconstruction: no camera parameters for image"
                       << wanted << "— the ground-truth poses are not used.";
            camParams.clear();
            hasGroundTruthParams = false;
            return false;
        }
        ordered.push_back(*found);
        ++matched;
    }
    camParams = ordered;
    cameraParamsMatched = true;
    qDebug() << "Reconstruction: matched" << matched
             << "camera parameter(s) to the images by name.";
    return true;
}

// ─── loadCameraParams ────────────────────────────────────────────────────────
//
//  FORMAT A (generic):   first line = N,  then N lines: imageName K(9) R(9) t(3)
//  FORMAT B (Middlebury): no count header, tokens: [...] imageName K(9) R(9) t(3)

bool ReconstructionPipeline::loadCameraParams(const QString &paramsFilePath) {
    hasGroundTruthParams = CameraParamsParser::loadFromFile(paramsFilePath, camParams);
    return hasGroundTruthParams;
}

void ReconstructionPipeline::extractFeatures(int idx) {
    FeatureExtractor::extract(images[idx], m_config, keypoints[idx], descriptors[idx]);
}

// ─── Cache paths ─────────────────────────────────────────────────────────────

void ReconstructionPipeline::computeCachePaths(QString &cachePath,
                                               QString &meshCachePath) const {
    cachePath.clear();
    meshCachePath.clear();
    if (!imageFiles.empty()) {
        QString dir = QFileInfo(imageFiles[0]).absolutePath();
        cachePath     = dir + "/" + kReconstructionCacheFileName;
        meshCachePath = dir + "/" + kMeshCacheFileName;
    }
}

// ─── Cache branch ────────────────────────────────────────────────────────────
//
// Trả về true nếu cache đã được dùng (kể cả khi load thất bại — khi đó
// caller tính lại từ đầu).

bool ReconstructionPipeline::loadFromCache(const QString &cachePath,
                                           const QString &meshCachePath) {
    qDebug() << "Reconstruction: Found cache at" << cachePath;
    PointCloudT::Ptr cloud(new PointCloudT);
    if (pcl::io::loadPLYFile<PointT>(cachePath.toStdString(), *cloud) == -1
        || cloud->empty()) {
        return false;
    }

    points3D.reserve(cloud->size());
    colors.reserve(cloud->size());
    for (const auto &p : cloud->points) {
        points3D.emplace_back(p.x, p.y, p.z);
        colors.emplace_back(p.b, p.g, p.r);
    }
    qDebug() << "Reconstruction: Loaded" << points3D.size() << "pts from cache.";

    // Re-texture point cloud bằng best-view
    ReconstructionPostProcessor::textureFromImages(points3D, colors, images, camParams);

    // Lưu lại cache đã texture best-view
    {
        PointCloudT::Ptr textured(new PointCloudT);
        textured->resize(points3D.size());
        for (size_t i=0;i<points3D.size();++i){
            auto &p = textured->points[i];
            p.x=points3D[i].x; p.y=points3D[i].y; p.z=points3D[i].z;
            p.b=colors[i][0]; p.g=colors[i][1]; p.r=colors[i][2];
        }
        pcl::io::savePLYFileBinary(cachePath.toStdString(), *textured);
        qDebug() << "Reconstruction: Saved TEXTURED cache to" << cachePath;
    }

    // Nếu mesh cache đã tồn tại → load trực tiếp, skip Poisson
    if (!meshCachePath.isEmpty() && QFile::exists(meshCachePath)) {
        qDebug() << "Reconstruction: Found MESH cache at" << meshCachePath
                 << "— skipping Poisson.";
        // Mesh đã được lưu sẵn, chỉ cần re-texture nếu muốn
        // (mesh file đã textured, không cần làm gì thêm)
    } else {
        // Mesh cache chưa có → Poisson lần đầu, sau đó lưu cache
        qDebug() << "Reconstruction: No mesh cache — running Poisson...";
        auto mesh = ReconstructionPostProcessor::poissonMeshing(points3D, colors, 10);
        ReconstructionPostProcessor::textureMeshFromImages(
            mesh, images, camParams,
            QFileInfo(imageFiles[0]).absolutePath() + "/" + kTexturedMeshFileName);
        // Lưu mesh cache
        if (!meshCachePath.isEmpty()) {
            pcl::io::savePLYFileBinary(meshCachePath.toStdString(), mesh);
            qDebug() << "Reconstruction: Saved MESH cache to" << meshCachePath;
        }
    }
    return true;
}

// ─── Feature extraction ──────────────────────────────────────────────────────

bool ReconstructionPipeline::extractAllFeatures() {
    keypoints.resize(images.size());
    descriptors.resize(images.size());
    qDebug() << "Extracting features from" << images.size() << "images in parallel...";
    QElapsedTimer timer; timer.start();
    QVector<int> indices(images.size());
    std::iota(indices.begin(), indices.end(), 0);
    QtConcurrent::blockingMap(indices, [this](int idx) {
        if (cancelRequested())
            return; // hủy giữa chừng: bỏ luôn các ảnh còn lại
        extractFeatures(idx);
    });
    if (abortIfCancelled()) {
        qDebug() << "Feature extraction cancelled after" << timer.elapsed() << "ms";
        return false;
    }
    qDebug() << "Feature extraction completed in" << timer.elapsed() << "ms";
    for (size_t i = 0; i < images.size(); ++i)
        qDebug() << " Image" << i << ":" << keypoints[i].size() << "pts";

    size_t totalKeypoints = 0, featurelessImages = 0;
    for (const auto& kp : keypoints) {
        totalKeypoints += kp.size();
        if (kp.empty()) ++featurelessImages;
    }
    if (totalKeypoints == 0) {
        m_lastError = QStringLiteral("SIFT detected no features in any image — "
                                      "images may be blurry, too dark or have uniform texture.");
        qWarning() << m_lastError;
        return false;
    }
    if (featurelessImages > 0)
        qWarning() << featurelessImages << "image(s) produced no features (blurry/low texture?)";
    return true;
}

// ─── Reconstruction branch dispatch ──────────────────────────────────────────

bool ReconstructionPipeline::runSfMBranch() {
    // The ground-truth branch only runs when the poses are actually bound to
    // the images they belong to.
    matchCameraParamsToImages();
    if (hasGroundTruthParams && cameraParamsMatched
        && (int)camParams.size() >= (int)images.size()) {
        TrackSfM stage(images, keypoints, descriptors, m_config, camParams,
                       points3D, colors, m_lastError);
        return stage.run();
    }
    IncrementalSfM stage(images, keypoints, descriptors, m_config,
                         K_fallback, distCoeffs, points3D, colors, m_lastError);
    return stage.run();
}

// ─── Post-processing + export ────────────────────────────────────────────────

void ReconstructionPipeline::finalizeReconstruction(const QString &cachePath,
                                                    const QString &meshCachePath) {
    // Chuỗi hậu kỳ rất nặng (MLS 1M điểm → texture → Poisson) — kiểm hủy
    // giữa các bước; bước đang chạy dở vẫn phải hoàn tất (thư viện ngoài
    // không interruptible), nhưng không bắt đầu bước kế tiếp khi đã hủy.
    if (cancelRequested())
        return;

    // filterFarOutliers không cần nữa vì vòm sinh ra từ Poisson, đã trim trong poissonMeshing
    ReconstructionPostProcessor::densifyPointCloudMLS(points3D, colors, 1000000);

    // 1. Texture point cloud 988k bằng best-view
    ReconstructionPostProcessor::textureFromImages(points3D, colors, images, camParams);

    if (cancelRequested())
        return;

    // 2. Lưu cache 988k cho Qt - KHÔNG lấy mesh 3.2M làm cache
    if (!cachePath.isEmpty() && !points3D.empty()) {
        PointCloudT::Ptr cloud(new PointCloudT);
        cloud->resize(points3D.size());
        for (size_t i=0;i<points3D.size();++i){
            auto &p = cloud->points[i];
            p.x=points3D[i].x; p.y=points3D[i].y; p.z=points3D[i].z;
            p.b=colors[i][0]; p.g=colors[i][1]; p.r=colors[i][2];
        }
        pcl::io::savePLYFileBinary(cachePath.toStdString(), *cloud);
        qDebug() << "Reconstruction: Saved 988k TEXTURED cache to" << cachePath;
    }

    if (cancelRequested())
        return; // hủy trước Poisson: bỏ mesh 3.2M + texture mesh

    // 3. Poisson + texture mesh -> ra file temple_final_textured_2M.ply riêng (đã trim vòm)
    auto mesh = ReconstructionPostProcessor::poissonMeshing(points3D, colors, 10);

    if (cancelRequested())
        return; // Poisson đã chạy xong nhưng hủy → bỏ texture mesh + lưu mesh cache

    ReconstructionPostProcessor::textureMeshFromImages(
        mesh, images, camParams,
        imageFiles.empty() ? QString()
                           : QFileInfo(imageFiles[0]).absolutePath() + "/" + kTexturedMeshFileName);

    // 4. Lưu mesh cache để lần sau skip Poisson
    if (!meshCachePath.isEmpty()) {
        pcl::io::savePLYFileBinary(meshCachePath.toStdString(), mesh);
        qDebug() << "Reconstruction: Saved MESH cache to" << meshCachePath;
    }
}

// ─── reconstruct (public) ────────────────────────────────────────────────────

bool ReconstructionPipeline::reconstruct(const std::function<bool()> &shouldCancel) {
    m_shouldCancel = shouldCancel;
    const bool ok = reconstructImpl();
    m_shouldCancel = nullptr; // không giữ callback sống ngoài một lần chạy
    return ok;
}

bool ReconstructionPipeline::abortIfCancelled() {
    if (!cancelRequested())
        return false;
    m_lastError = QStringLiteral("Reconstruction cancelled.");
    return true;
}

bool ReconstructionPipeline::reconstructImpl() {
    m_lastError.clear();

    // Checkpoint 0: hủy đã được yêu cầu trước khi bắt đầu.
    if (abortIfCancelled())
        return false;

    if (images.size() < 2) {
        m_lastError = QStringLiteral("Only %1 image(s) loaded — at least 2 are required.")
                          .arg(images.size());
        qWarning() << "Need at least 2 images!";
        return false;
    }
    points3D.clear(); colors.clear();

    QString cachePath;
    QString meshCachePath;
    computeCachePaths(cachePath, meshCachePath);

    // ---- NHÁNH CACHE ----
    if (!cachePath.isEmpty() && QFile::exists(cachePath)) {
        if (loadFromCache(cachePath, meshCachePath))
            return !points3D.empty();
        // Load cache thất bại → tính lại từ đầu.
    }

    // ---- TÍNH LẠI TỪ ĐẦU ----
    if (!extractAllFeatures())
        return false;

    if (!runSfMBranch() || points3D.empty()) {
        if (m_lastError.isEmpty())
            m_lastError = QStringLiteral("Reconstruction produced no 3D points (%1).")
                              .arg(hasGroundTruthParams
                                       ? QStringLiteral("ground-truth track triangulation")
                                       : QStringLiteral("estimated-pose incremental SfM"));
        qWarning() << "Reconstruction failed:" << m_lastError;
        return false;
    }

    // Checkpoint: SfM xong — hủy trước khi bước vào lọc + hậu kỳ nặng.
    if (abortIfCancelled())
        return false;

    qDebug() << "Raw points:" << points3D.size();
    processPointCloud();

    if (points3D.empty()) {
        m_lastError = QStringLiteral(
            "All points were removed by the cleanup filters (SOR/ROR/voxel) — "
            "try lowering the density setting or disabling auto-clean.");
        qWarning() << "Reconstruction failed:" << m_lastError;
        return false;
    }

    // Checkpoint: trước chuỗi hậu kỳ (MLS densify → texture → Poisson mesh).
    if (abortIfCancelled())
        return false;

    finalizeReconstruction(cachePath, meshCachePath);

    // finalizeReconstruction tự thoát sớm giữa chuỗi khi bị hủy.
    if (abortIfCancelled())
        return false;
    return !points3D.empty();
}

// ─── Post-processing delegate ────────────────────────────────────────────────

void ReconstructionPipeline::processPointCloud() {
    ReconstructionPostProcessor::processPointCloud(m_config.filter, points3D, colors);
}

// ─── Accessors ───────────────────────────────────────────────────────────────

const std::vector<cv::Point3f>& ReconstructionPipeline::getPointCloud()  const { return points3D; }
const std::vector<cv::Vec3b>&   ReconstructionPipeline::getPointColors() const { return colors; }

std::shared_ptr<const PointCloudSnapshot> ReconstructionPipeline::buildSnapshot() const {
    // The ABI-neutral copy of the current cloud. Points and colours are
    // copied together so a consumer can never observe two different
    // reconstruction generations.
    auto snapshot = std::make_shared<PointCloudSnapshot>();

    snapshot->points.reserve(points3D.size());
    for (const auto& p : points3D)
        snapshot->points.push_back({p.x, p.y, p.z});

    snapshot->colors.reserve(colors.size());
    for (const auto& c : colors) {
        // OpenCV is BGR, PointColor is {r, g, b}: the channels have to be
        // swapped here, or the rendered cloud shows red and blue inverted.
        snapshot->colors.push_back({c[2], c[1], c[0]});
    }

    return snapshot;
}
