#include "ReconstructionPipeline.h"
#include "FeatureExtractor.h"
#include "FeatureMatcher.h"
#include "PoseEstimator.h"
#include "Triangulator.h"
#include "PointCloudFilter.h"
#include "AppConstants.h"

#include <QDebug>
#include <QFile>
#include <QFileInfo>
#include <QIODevice>
#include <QRegularExpression>
#include <QTextStream>
#include <QElapsedTimer>
#include <QVector>
#include <QtConcurrent/QtConcurrent>

#include <opencv2/calib3d.hpp>
#include <opencv2/imgcodecs.hpp>

#include <pcl/console/print.h>
#include <pcl/io/ply_io.h>

#include <algorithm>
#include <cmath>
#include <mutex>
#include <numeric>
#include <set>

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
    imageFiles = imagePaths;
    images.clear();
    for (const auto &path : imagePaths) {
        cv::Mat img = cv::imread(path.toStdString(), cv::IMREAD_COLOR);
        if (!img.empty()) images.push_back(img);
        else qWarning() << "Cannot read image:" << path;
    }
    qDebug() << "Loaded" << images.size() << "images";
}

// ─── loadCameraParams ────────────────────────────────────────────────────────
//
//  FORMAT A (generic):   first line = N,  then N lines: imageName K(9) R(9) t(3)
//  FORMAT B (Middlebury): no count header, tokens: [...] imageName K(9) R(9) t(3)

bool ReconstructionPipeline::loadCameraParams(const QString &paramsFilePath) {
    QFile file(paramsFilePath);
    if (!file.open(QIODevice::ReadOnly | QIODevice::Text)) {
        qWarning() << "Cannot open params file:" << paramsFilePath;
        return false;
    }

    camParams.clear();
    QTextStream in(&file);
    QStringList allLines;
    while (!in.atEnd()) {
        QString line = in.readLine().trimmed();
        if (!line.isEmpty()) allLines.append(line);
    }
    file.close();

    if (allLines.isEmpty()) { qWarning() << "Params file is empty."; return false; }

    bool isFormatA = false;
    int  startLine = 0;
    {
        bool ok = false;
        int  cnt = allLines[0].toInt(&ok);
        if (ok && cnt > 0 && cnt < allLines.size()) { isFormatA = true; startLine = 1; }
    }
    qDebug() << "loadCameraParams: format ="
             << (isFormatA ? "A (count header)" : "B (Middlebury/no header)")
             << " startLine=" << startLine;

    for (int li = startLine; li < allLines.size(); ++li) {
        QStringList tok = allLines[li].split(QRegularExpression("\\s+"), Qt::SkipEmptyParts);

        int nameIdx = -1;
        for (int ti = 0; ti < (int)tok.size(); ++ti) {
            if (tok[ti].contains('.') && !tok[ti].startsWith('-') && !tok[ti].startsWith('+')) {
                if (tok.size() - ti - 1 >= 21) { nameIdx = ti; break; }
            }
        }
        if (nameIdx < 0) {
            qWarning() << "Skipping line" << li << "— imageName not found:" << allLines[li].left(60);
            continue;
        }

        CameraParams cp;
        cp.imageName = tok[nameIdx];
        cp.K = cv::Mat(3, 3, CV_64F);
        for (int r = 0; r < 3; ++r)
            for (int c = 0; c < 3; ++c)
                cp.K.at<double>(r, c) = tok[nameIdx + 1 + r * 3 + c].toDouble();
        cp.R = cv::Mat(3, 3, CV_64F);
        for (int r = 0; r < 3; ++r)
            for (int c = 0; c < 3; ++c)
                cp.R.at<double>(r, c) = tok[nameIdx + 10 + r * 3 + c].toDouble();
        cp.t = cv::Mat(3, 1, CV_64F);
        for (int r = 0; r < 3; ++r)
            cp.t.at<double>(r, 0) = tok[nameIdx + 19 + r].toDouble();

        cv::Mat RT; cv::hconcat(cp.R, cp.t, RT);
        cp.P = cp.K * RT;
        camParams.push_back(cp);
    }

    hasGroundTruthParams = !camParams.empty();
    qDebug() << "Loaded" << camParams.size() << "camera params";
    return hasGroundTruthParams;
}

// ─── Private helpers (thin wrappers to keep call-sites minimal) ──────────────

void ReconstructionPipeline::extractFeatures(int idx) {
    FeatureExtractor::extract(images[idx], m_config, keypoints[idx], descriptors[idx]);
}

void ReconstructionPipeline::matchFeatures(int idx1, int idx2,
                                           std::vector<cv::DMatch> &goodMatches) {
    FeatureMatcher::match(descriptors[idx1], descriptors[idx2], m_config, goodMatches);
}

bool ReconstructionPipeline::estimatePoseFromMatches(const std::vector<cv::Point2f> &pts1,
                                                     const std::vector<cv::Point2f> &pts2,
                                                     cv::Mat &R, cv::Mat &t) {
    return PoseEstimator::estimatePose(pts1, pts2, K_fallback, R, t,
                                       AppConstants::Reconstruction::MIN_INLIERS_FOR_POSE);
}

void ReconstructionPipeline::doTriangulate(const cv::Mat &P0, const cv::Mat &P1,
                                           const std::vector<cv::Point2f> &pts0,
                                           const std::vector<cv::Point2f> &pts1,
                                           std::vector<cv::Point3f> &outPts) {
    Triangulator::triangulate(P0, P1, pts0, pts1, outPts);
}

double ReconstructionPipeline::computeReprojectionError(const cv::Mat &P,
                                                         const cv::Point3f &pt3d,
                                                         const cv::Point2f &pt2d) {
    return PoseEstimator::reprojectionError(P, pt3d, pt2d);
}

void ReconstructionPipeline::filterOutliersByDensity(float radius, int minNeighbors) {
    PointCloudFilter::densityFilter(points3D, colors, radius, minNeighbors);
}

// ─── processPointCloud ───────────────────────────────────────────────────────

void ReconstructionPipeline::processPointCloud() {
    if (points3D.empty()) return;
    qDebug() << "Post-processing. Initial points:" << points3D.size();
    PointCloudFilter::statisticalOutlier(points3D, colors,
                                         m_config.filter.sorMeanK,
                                         m_config.filter.sorStdDevMul);
    if (points3D.empty()) { qWarning() << "No points after SOR."; return; }
    PointCloudFilter::radiusOutlier(points3D, colors,
                                    m_config.filter.rorRadius,
                                    m_config.filter.rorMinNeighbors);
    if (points3D.empty()) { qWarning() << "No points after ROR."; return; }
    PointCloudFilter::voxelGrid(points3D, colors, m_config.filter.voxelLeafSize);
    qDebug() << "After post-processing:" << points3D.size() << "points";
}

// ─── PCL filter public accessors ─────────────────────────────────────────────

void ReconstructionPipeline::statisticalOutlierFilter(float meanK, float stdDevMulThresh) {
    PointCloudFilter::statisticalOutlier(points3D, colors, meanK, stdDevMulThresh);
}

void ReconstructionPipeline::radiusOutlierFilter(float radius, int minNeighbors) {
    PointCloudFilter::radiusOutlier(points3D, colors, radius, minNeighbors);
}

void ReconstructionPipeline::voxelGridDownsample(float leafSize) {
    PointCloudFilter::voxelGrid(points3D, colors, leafSize);
}

// ─── reconstructWithGroundTruth ──────────────────────────────────────────────

bool ReconstructionPipeline::reconstructWithGroundTruth() {
    qDebug() << "=== Mode: GROUND-TRUTH camera params ===";
    int N = (int)images.size();

    double maxBaseline = 0.0;
    for (int i = 0; i < (int)camParams.size(); ++i)
        for (int j = i + 1; j < (int)camParams.size(); ++j) {
            cv::Mat Ci = -camParams[i].R.t() * camParams[i].t;
            cv::Mat Cj = -camParams[j].R.t() * camParams[j].t;
            cv::Mat d  = Ci - Cj;
            maxBaseline = std::max(maxBaseline, std::sqrt(d.dot(d)));
        }
    double depthLimit = (maxBaseline > 1e-6)
        ? maxBaseline * AppConstants::Reconstruction::DEPTH_LIMIT_MULTIPLIER : 1e9;
    qDebug() << "  maxBaseline=" << maxBaseline << "  depthLimit=" << depthLimit;

    const int WINDOW = m_config.searchWindow;
    std::vector<std::pair<int, int>> pairs;
    for (int i = 0; i < N; ++i)
        for (int j = i + 1; j < std::min(N, i + WINDOW + 1); ++j)
            pairs.push_back({i, j});

    std::mutex mtx;
    cv::parallel_for_(cv::Range(0, (int)pairs.size()), [&](const cv::Range &range) {
        std::vector<cv::Point3f> lp; std::vector<cv::Vec3b> lc;
        for (int r = range.start; r < range.end; ++r) {
            int i = pairs[r].first, j = pairs[r].second;
            std::vector<cv::DMatch> matches;
            matchFeatures(i, j, matches);
            if ((int)matches.size() < m_config.minMatches) continue;

            std::vector<cv::Point2f> pi, pj;
            for (const auto &m : matches) {
                pi.push_back(keypoints[i][m.queryIdx].pt);
                pj.push_back(keypoints[j][m.trainIdx].pt);
            }
            std::vector<cv::Point3f> newPts;
            doTriangulate(camParams[i].P, camParams[j].P, pi, pj, newPts);

            int kept = 0;
            for (size_t k = 0; k < newPts.size() && k < matches.size(); ++k) {
                const cv::Point3f &pt = newPts[k];
                double zi = camParams[i].R.at<double>(2,0)*pt.x +
                            camParams[i].R.at<double>(2,1)*pt.y +
                            camParams[i].R.at<double>(2,2)*pt.z +
                            camParams[i].t.at<double>(2);
                double zj = camParams[j].R.at<double>(2,0)*pt.x +
                            camParams[j].R.at<double>(2,1)*pt.y +
                            camParams[j].R.at<double>(2,2)*pt.z +
                            camParams[j].t.at<double>(2);
                if (zi <= 0 || zj <= 0 || zi > depthLimit || zj > depthLimit) continue;
                if (computeReprojectionError(camParams[i].P, pt, pi[k]) > m_config.reprojectionErrorMax) continue;
                if (computeReprojectionError(camParams[j].P, pt, pj[k]) > m_config.reprojectionErrorMax) continue;

                cv::Point2f kp = keypoints[i][matches[k].queryIdx].pt;
                int x = cvRound(kp.x), y = cvRound(kp.y);
                cv::Vec3b col(128, 128, 128);
                if (x >= 0 && y >= 0 && x < images[i].cols && y < images[i].rows)
                    col = images[i].at<cv::Vec3b>(y, x);
                lp.push_back(pt); lc.push_back(col); ++kept;
            }
            if (kept > 0)
                qDebug() << "  Pair" << i << "-" << j
                         << "match=" << matches.size() << "kept=" << kept;
        }
        std::lock_guard<std::mutex> lock(mtx);
        points3D.insert(points3D.end(), lp.begin(), lp.end());
        colors.insert(colors.end(), lc.begin(), lc.end());
    });

    qDebug() << "=== Ground-truth raw points:" << points3D.size() << "===";
    return !points3D.empty();
}

// ─── reconstructWithEstimatedPose ────────────────────────────────────────────

bool ReconstructionPipeline::reconstructWithEstimatedPose() {
    qDebug() << "=== Mode: ESTIMATED pose ===";
    int N = (int)images.size();

    K_fallback = PoseEstimator::estimateIntrinsics(images[0]);
    qDebug() << "  K: fx=" << K_fallback.at<double>(0,0)
             << "  cx=" << K_fallback.at<double>(0,2)
             << "  cy=" << K_fallback.at<double>(1,2);

    int    best_i = 0, best_j = 1;
    size_t bestInliers = 0;

    qDebug() << "  Finding base pair (window=5)...";
    for (int i = 0; i < std::min(N-1, 20); ++i) {
        for (int jj = i+1; jj <= std::min(N-1, i+5); ++jj) {
            std::vector<cv::DMatch> tmp;
            matchFeatures(i, jj, tmp);
            if ((int)tmp.size() < 30) continue;

            std::vector<cv::Point2f> p1, p2;
            for (const auto &m : tmp) {
                p1.push_back(keypoints[i][m.queryIdx].pt);
                p2.push_back(keypoints[jj][m.trainIdx].pt);
            }
            cv::Mat R_t, t_t, mask;
            cv::Mat E = cv::findEssentialMat(p1, p2, K_fallback, cv::RANSAC, 0.999, 1.0, mask);
            if (E.empty()) continue;
            int inl = cv::recoverPose(E, p1, p2, K_fallback, R_t, t_t, mask);
            double ratio = (double)inl / tmp.size();
            if (inl > (int)bestInliers &&
                inl >= AppConstants::Reconstruction::MIN_INLIERS_FOR_ESTIMATED_POSE &&
                ratio > 0.5)
            {
                bestInliers = inl; best_i = i; best_j = jj;
            }
        }
    }
    qDebug() << "  Base pair:" << best_i << "-" << best_j << " inliers=" << bestInliers;

    if (bestInliers < (size_t)m_config.minMatches) {
        qWarning() << "No good base pair found! Trying wider window...";
        size_t bestMatch = 0;
        for (int i = 0; i < N-1; ++i) {
            for (int jj = i+1; jj <= std::min(N-1, i+3); ++jj) {
                std::vector<cv::DMatch> tmp;
                matchFeatures(i, jj, tmp);
                if (tmp.size() > bestMatch) { bestMatch = tmp.size(); best_i = i; best_j = jj; }
            }
        }
        std::vector<cv::DMatch> tmp2; matchFeatures(best_i, best_j, tmp2);
        std::vector<cv::Point2f> p1, p2;
        for (const auto &m : tmp2) {
            p1.push_back(keypoints[best_i][m.queryIdx].pt);
            p2.push_back(keypoints[best_j][m.trainIdx].pt);
        }
        cv::Mat Rt, tt, mask2;
        cv::Mat E2 = cv::findEssentialMat(p1, p2, K_fallback, cv::RANSAC, 0.999, 1.0, mask2);
        if (!E2.empty()) bestInliers = cv::recoverPose(E2, p1, p2, K_fallback, Rt, tt, mask2);
        if (bestInliers < 10) { qWarning() << "Base pair failed → return false."; return false; }
    }

    // Base pair pose
    std::vector<cv::DMatch> baseMatches;
    matchFeatures(best_i, best_j, baseMatches);
    std::vector<cv::Point2f> bp1, bp2;
    for (const auto &m : baseMatches) {
        bp1.push_back(keypoints[best_i][m.queryIdx].pt);
        bp2.push_back(keypoints[best_j][m.trainIdx].pt);
    }
    cv::Mat R_base, t_base;
    if (!estimatePoseFromMatches(bp1, bp2, R_base, t_base)) {
        qWarning() << "estimatePoseFromMatches failed."; return false;
    }

    // Triangulate base
    cv::Mat P0 = K_fallback * cv::Mat::eye(3, 4, CV_64F);
    cv::Mat RT0; cv::hconcat(R_base, t_base, RT0);
    cv::Mat P1 = K_fallback * RT0;

    std::vector<cv::Point3f> basePts;
    doTriangulate(P0, P1, bp1, bp2, basePts);

    std::vector<float> zvec;
    for (const auto &p : basePts) if (p.z > 0) zvec.push_back(p.z);
    if (zvec.empty()) { qWarning() << "No positive depth!"; return false; }
    std::nth_element(zvec.begin(), zvec.begin() + zvec.size()/2, zvec.end());
    float zMed = zvec[zvec.size()/2];
    float zMin = zMed * AppConstants::Reconstruction::DEPTH_MIN_MEDIAN_RATIO;
    float zMax = zMed * AppConstants::Reconstruction::DEPTH_MAX_MEDIAN_RATIO;
    qDebug() << "  zMedian=" << zMed << "  range=[" << zMin << "," << zMax << "]";

    points3D.clear(); colors.clear();
    for (size_t k = 0; k < basePts.size() && k < baseMatches.size(); ++k) {
        const auto &pt = basePts[k];
        if (pt.z < zMin || pt.z > zMax) continue;
        if (computeReprojectionError(P0, pt, bp1[k]) > m_config.reprojectionErrorMax) continue;
        if (computeReprojectionError(P1, pt, bp2[k]) > m_config.reprojectionErrorMax) continue;
        points3D.push_back(pt);
        cv::Point2f kp = keypoints[best_i][baseMatches[k].queryIdx].pt;
        int x = cvRound(kp.x), y = cvRound(kp.y);
        cv::Vec3b col(128, 128, 128);
        if (x >= 0 && y >= 0 && x < images[best_i].cols && y < images[best_i].rows)
            col = images[best_i].at<cv::Vec3b>(y, x);
        colors.push_back(col);
    }
    qDebug() << "  Base triangulated:" << points3D.size();

    // Incremental SfM
    struct PoseInfo { int imgIdx; cv::Mat P, R, t; };
    std::vector<PoseInfo> knownPoses;
    knownPoses.push_back({best_i, P0.clone(), cv::Mat::eye(3,3,CV_64F), cv::Mat::zeros(3,1,CV_64F)});
    knownPoses.push_back({best_j, P1.clone(), R_base.clone(), t_base.clone()});
    std::set<int> processed = {best_i, best_j};

    for (int iter = 0; iter < N-2; ++iter) {
        int bestNew = -1, bestRef = -1, bestCnt = 0;
        for (int idx = 0; idx < N; ++idx) {
            if (processed.count(idx)) continue;
            for (int pi = 0; pi < (int)knownPoses.size(); ++pi) {
                if (std::abs(idx - knownPoses[pi].imgIdx) > 8) continue;
                std::vector<cv::DMatch> tmp;
                matchFeatures(idx, knownPoses[pi].imgIdx, tmp);
                if ((int)tmp.size() > bestCnt) { bestCnt = tmp.size(); bestNew = idx; bestRef = pi; }
            }
        }
        if (bestNew < 0 || bestCnt < m_config.minMatches) {
            bestCnt = 0;
            for (int idx = 0; idx < N; ++idx) {
                if (processed.count(idx)) continue;
                for (int pi = 0; pi < (int)knownPoses.size(); ++pi) {
                    std::vector<cv::DMatch> tmp;
                    matchFeatures(idx, knownPoses[pi].imgIdx, tmp);
                    if ((int)tmp.size() > bestCnt) { bestCnt = tmp.size(); bestNew = idx; bestRef = pi; }
                }
            }
        }
        if (bestNew < 0 || bestCnt < m_config.minMatches) { qDebug() << "  No more images to add."; break; }

        int refImg = knownPoses[bestRef].imgIdx;
        std::vector<cv::DMatch> matches;
        matchFeatures(bestNew, refImg, matches);
        std::vector<cv::Point2f> pNew, pRef;
        for (const auto &m : matches) {
            pNew.push_back(keypoints[bestNew][m.queryIdx].pt);
            pRef.push_back(keypoints[refImg][m.trainIdx].pt);
        }

        cv::Mat R_rel, t_rel;
        if (!estimatePoseFromMatches(pNew, pRef, R_rel, t_rel)) {
            qDebug() << "  Skipping image" << bestNew;
            processed.insert(bestNew); continue;
        }

        cv::Mat R_abs = knownPoses[bestRef].R * R_rel;
        cv::Mat t_abs = knownPoses[bestRef].R * t_rel + knownPoses[bestRef].t;
        cv::Mat RT_n; cv::hconcat(R_abs, t_abs, RT_n);
        cv::Mat P_new = K_fallback * RT_n;
        knownPoses.push_back({bestNew, P_new, R_abs.clone(), t_abs.clone()});
        processed.insert(bestNew);

        std::vector<cv::Point3f> newPts;
        doTriangulate(knownPoses[bestRef].P, P_new, pRef, pNew, newPts);
        int added = 0;
        for (size_t k = 0; k < newPts.size() && k < matches.size(); ++k) {
            const auto &pt = newPts[k];
            if (pt.z < zMin || pt.z > zMax) continue;
            if (computeReprojectionError(knownPoses[bestRef].P, pt, pRef[k]) > m_config.reprojectionErrorMax) continue;
            if (computeReprojectionError(P_new, pt, pNew[k]) > m_config.reprojectionErrorMax) continue;
            points3D.push_back(pt);
            cv::Point2f kp = keypoints[bestNew][matches[k].queryIdx].pt;
            int x = cvRound(kp.x), y = cvRound(kp.y);
            cv::Vec3b col(128, 128, 128);
            if (x >= 0 && y >= 0 && x < images[bestNew].cols && y < images[bestNew].rows)
                col = images[bestNew].at<cv::Vec3b>(y, x);
            colors.push_back(col); ++added;
        }
        qDebug() << "  Image" << bestNew << "(ref=" << refImg << ")"
                 << "added" << added << "pts. Total:" << points3D.size();
    }

    qDebug() << "=== Estimated raw points:" << points3D.size() << "===";
    return !points3D.empty();
}

// ─── reconstruct (public) ────────────────────────────────────────────────────

bool ReconstructionPipeline::reconstruct() {
    if (images.size() < 2) { qWarning() << "Need at least 2 images!"; return false; }
    points3D.clear(); colors.clear();

    // Cache check
    QString cachePath;
    if (!imageFiles.empty()) {
        cachePath = QFileInfo(imageFiles[0]).absolutePath() + "/recon_cache.ply";
        if (QFile::exists(cachePath)) {
            qDebug() << "Reconstruction: Found cache at" << cachePath;
            PointCloudT::Ptr cloud(new PointCloudT);
            if (pcl::io::loadPLYFile<PointT>(cachePath.toStdString(), *cloud) != -1) {
                points3D.reserve(cloud->size());
                colors.reserve(cloud->size());
                for (const auto &p : cloud->points) {
                    points3D.push_back(cv::Point3f(p.x, p.y, p.z));
                    colors.push_back(cv::Vec3b(p.b, p.g, p.r));
                }
                qDebug() << "Reconstruction: Loaded" << points3D.size() << "pts from cache.";
                return !points3D.empty();
            }
        }
    }

    // Feature extraction (parallel)
    keypoints.resize(images.size());
    descriptors.resize(images.size());
    qDebug() << "Extracting features from" << images.size() << "images in parallel...";
    QElapsedTimer timer; timer.start();
    QVector<int> indices(images.size());
    std::iota(indices.begin(), indices.end(), 0);
    QtConcurrent::blockingMap(indices, [this](int idx) { extractFeatures(idx); });
    qDebug() << "Feature extraction completed in" << timer.elapsed() << "ms";
    for (size_t i = 0; i < images.size(); ++i)
        qDebug() << "  Image" << i << ":" << keypoints[i].size() << "pts";

    bool ok = false;
    if (hasGroundTruthParams && (int)camParams.size() >= (int)images.size())
        ok = reconstructWithGroundTruth();
    else
        ok = reconstructWithEstimatedPose();

    if (!ok || points3D.empty()) { qWarning() << "Reconstruction failed."; return false; }

    qDebug() << "Raw points:" << points3D.size();
    processPointCloud();

    // Save cache
    if (!cachePath.isEmpty() && !points3D.empty()) {
        PointCloudT::Ptr cloud(new PointCloudT);
        cloud->resize(points3D.size());
        for (size_t i = 0; i < points3D.size(); ++i) {
            auto &p = cloud->points[i];
            p.x = points3D[i].x; p.y = points3D[i].y; p.z = points3D[i].z;
            p.b = colors[i][0]; p.g = colors[i][1]; p.r = colors[i][2];
        }
        pcl::io::savePLYFileBinary(cachePath.toStdString(), *cloud);
        qDebug() << "Reconstruction: Saved cache to" << cachePath;
    }

    return !points3D.empty();
}

// ─── Accessors ───────────────────────────────────────────────────────────────

std::vector<cv::Point3f> ReconstructionPipeline::getPointCloud()  const { return points3D; }
std::vector<cv::Vec3b>   ReconstructionPipeline::getPointColors() const { return colors; }
