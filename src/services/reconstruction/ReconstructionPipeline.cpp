#include "ReconstructionPipeline.h"
#include "CameraParamsParser.h"
#include "FeatureExtractor.h"
#include "FeatureMatcher.h"
#include "PoseEstimator.h"
#include "Triangulator.h"
#include "PointCloudFilter.h"
#include "AppConstants.h"

#include <QDebug>
#include <QFile>
#include <QFileInfo>
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

#include <map>
#include <unordered_map>

namespace {
constexpr const char *kReconstructionCacheFileName = "recon_cache_quality_v2.ply";

std::vector<cv::DMatch> filterMatchesByFundamental(
    const std::vector<cv::DMatch> &matches,
    const std::vector<cv::KeyPoint> &keypoints1,
    const std::vector<cv::KeyPoint> &keypoints2,
    double ransacThreshold)
{
    if (matches.size() < 8 || ransacThreshold <= 0.0)
        return matches;

    std::vector<cv::Point2f> points1;
    std::vector<cv::Point2f> points2;
    points1.reserve(matches.size());
    points2.reserve(matches.size());
    for (const auto &match : matches) {
        points1.push_back(keypoints1[match.queryIdx].pt);
        points2.push_back(keypoints2[match.trainIdx].pt);
    }

    cv::Mat inlierMask;
    cv::Mat fundamental = cv::findFundamentalMat(
        points1, points2, cv::FM_RANSAC, ransacThreshold, 0.995, inlierMask);
    if (fundamental.empty() || inlierMask.empty())
        return std::vector<cv::DMatch>();

    std::vector<cv::DMatch> inliers;
    inliers.reserve(matches.size());
    for (int i = 0; i < inlierMask.rows; ++i) {
        if (inlierMask.at<uchar>(i))
            inliers.push_back(matches[i]);
    }

    return inliers.size() >= 8 ? inliers : std::vector<cv::DMatch>();
}
}

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
    hasGroundTruthParams = CameraParamsParser::loadFromFile(paramsFilePath, camParams);
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


// ─── processPointCloud ───────────────────────────────────────────────────────

void ReconstructionPipeline::processPointCloud() {
    if (points3D.empty()) return;
    qDebug() << "Post-processing. Initial points:" << points3D.size();

    const auto &f = m_config.filter;
    if (m_usedTrackBasedGroundTruth) {
        PointCloudFilter::statisticalOutlier(points3D, colors, f.sorMeanKTrack, f.sorStdDevMulTrack);
        if (points3D.empty()) { qWarning() << "No points after SOR."; return; }
        PointCloudFilter::radiusOutlier(points3D, colors, f.rorRadiusTrack, f.rorMinNeighborsTrack);
        if (points3D.empty()) { qWarning() << "No points after ROR."; return; }
        PointCloudFilter::voxelGrid(points3D, colors, f.voxelLeafSizeTrack);
    } else {
        PointCloudFilter::statisticalOutlier(points3D, colors, f.sorMeanK, f.sorStdDevMul);
        if (points3D.empty()) { qWarning() << "No points after SOR."; return; }
        
        // ROR relies on absolute distance thresholds which fails heavily on estimated 
        // pose clouds where scale is arbitrary (due to recovering pose from Essential matrix).
        // Therefore, we skip ROR for estimated pose. SOR + VoxelGrid is sufficient.
        // PointCloudFilter::radiusOutlier(points3D, colors, f.rorRadius, f.rorMinNeighbors);

        PointCloudFilter::voxelGrid(points3D, colors, f.voxelLeafSize);
    }
    qDebug() << "After post-processing:" << points3D.size() << "points";
}

// ─── reconstructWithGroundTruth ──────────────────────────────────────────────

bool ReconstructionPipeline::reconstructWithGroundTruth() {
    qDebug() << "=== Mode: Track-based Multi-View Triangulation (ground-truth poses) ===";
    int N = (int)images.size();

    TrackBuilder builder;
    for (int i = 0; i < N; ++i)
        for (int k = 0; k < (int)keypoints[i].size(); ++k)
            builder.registerObs(i, k);

    const int WINDOW = m_config.searchWindow;
    std::mutex mtx;
    std::vector<std::pair<int,int>> pairs;
    for (int i = 0; i < N; ++i)
        for (int j = i + 1; j < std::min(N, i + WINDOW + 1); ++j)
            pairs.push_back({i, j});

    cv::parallel_for_(cv::Range(0, (int)pairs.size()), [&](const cv::Range &range) {
        for (int r = range.start; r < range.end; ++r) {
            int i = pairs[r].first, j = pairs[r].second;
            std::vector<cv::DMatch> matches;
            matchFeatures(i, j, matches);
            matches = filterMatchesByFundamental(matches, keypoints[i], keypoints[j],
                                                 m_config.pairFundamentalRansacThreshold);
            if ((int)matches.size() < m_config.minMatches) continue;
            std::lock_guard<std::mutex> lock(mtx);
            for (const auto &m : matches)
                builder.addMatch(i, m.queryIdx, j, m.trainIdx);
        }
    });

    auto tracks = builder.buildTracks(/*minObservations=*/2);   // ★ 2 thay vì 3
    qDebug() << "  Built" << tracks.size() << "multi-view tracks";

    MultiViewTriangulator::Params triParams;
    triParams.maxReprojError           = 2.5;   // ★ nới từ 1.2
    triParams.minTriangulationAngleDeg = 1.0;
    triParams.minObservations          = 2;

    points3D.clear(); colors.clear();
    std::mutex resultMtx;
    std::atomic<int> rejectedAngle{0}, rejectedTotal{0};

    cv::parallel_for_(cv::Range(0, (int)tracks.size()), [&](const cv::Range &range) {
        std::vector<cv::Point3f> lp; std::vector<cv::Vec3b> lc;
        for (int t = range.start; t < range.end; ++t) {
            FeatureTrack track = tracks[t];
            if (!MultiViewTriangulator::triangulateTrack(track, camParams, keypoints, triParams)) {
                rejectedTotal++;
                continue;
            }
            MultiViewTriangulator::refinePoint(track, camParams, keypoints);

            int imgIdx = track.observations[0].first, kpIdx = track.observations[0].second;
            cv::Point2f kp = keypoints[imgIdx][kpIdx].pt;
            int x = cvRound(kp.x), y = cvRound(kp.y);
            cv::Vec3b col(128,128,128);
            if (x >= 0 && y >= 0 && x < images[imgIdx].cols && y < images[imgIdx].rows)
                col = images[imgIdx].at<cv::Vec3b>(y, x);

            lp.push_back(track.point3D);
            lc.push_back(col);
        }
        std::lock_guard<std::mutex> lock(resultMtx);
        points3D.insert(points3D.end(), lp.begin(), lp.end());
        colors.insert(colors.end(), lc.begin(), lc.end());
    });

    qDebug() << "  Rejected tracks:" << rejectedTotal.load() << "/" << tracks.size();
    m_usedTrackBasedGroundTruth = true;  // ★ để processPointCloud() chọn đúng filter profile
    qDebug() << "=== Ground-truth track-based points:" << points3D.size() << "===";
    return !points3D.empty();
}

// ─── reconstructWithEstimatedPose ────────────────────────────────────────────

bool ReconstructionPipeline::reconstructWithEstimatedPose() {
    qDebug() << "=== Mode: ESTIMATED pose (incremental SfM via PnP resectioning) ===";
    int N = (int)images.size();

    K_fallback = PoseEstimator::estimateIntrinsics(images[0]);
    qDebug() << "  K: fx=" << K_fallback.at<double>(0,0)
             << "  cx=" << K_fallback.at<double>(0,2)
             << "  cy=" << K_fallback.at<double>(1,2);

    // ── Match cache: tránh chạy lại FLANN kNN cho cùng một cặp ảnh trong lúc
    //    tìm base pair và trong mỗi vòng lặp incremental (nguyên nhân chính
    //    khiến pipeline này chạy ~14 phút cho 47 ảnh trước đây).
    std::map<std::pair<int,int>, std::vector<cv::DMatch>> matchCache;
    auto matchFeaturesCached = [&](int a, int b, std::vector<cv::DMatch> &out) {
        auto it = matchCache.find({a, b});
        if (it != matchCache.end()) { out = it->second; return; }
        auto itRev = matchCache.find({b, a});
        if (itRev != matchCache.end()) {
            out.clear();
            out.reserve(itRev->second.size());
            for (const auto &m : itRev->second) {
                cv::DMatch sw = m;
                std::swap(sw.queryIdx, sw.trainIdx);
                out.push_back(sw);
            }
            matchCache[{a, b}] = out;
            return;
        }
        matchFeatures(a, b, out);
        out = filterMatchesByFundamental(out, keypoints[a], keypoints[b], m_config.pairFundamentalRansacThreshold);
        matchCache[{a, b}] = out;
    };

    // ── obsIndex: (imageIdx, keypointIdx) → index vào points3D. Cho biết,
    //    với bất kỳ ảnh nào, keypoint nào của nó đã tương ứng với một điểm 3D
    //    đã dựng — nền tảng để resection bằng PnP.
    std::unordered_map<int64_t, int> obsIndex;
    auto obsKey = [](int img, int kp) -> int64_t {
        return (int64_t)img * 100000 + kp;
    };

    int    best_i = 0, best_j = 1;
    size_t bestInliers = 0;

    qDebug() << "  Finding base pair (window=5)...";
    for (int i = 0; i < std::min(N-1, 20); ++i) {
        for (int jj = i+1; jj <= std::min(N-1, i+5); ++jj) {
            std::vector<cv::DMatch> tmp;
            matchFeaturesCached(i, jj, tmp);
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
                matchFeaturesCached(i, jj, tmp);
                if (tmp.size() > bestMatch) { bestMatch = tmp.size(); best_i = i; best_j = jj; }
            }
        }
        std::vector<cv::DMatch> tmp2; matchFeaturesCached(best_i, best_j, tmp2);
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

    // Base pair pose (2-view — bắt buộc dùng Essential Matrix vì chưa có điểm 3D nào)
    std::vector<cv::DMatch> baseMatches;
    matchFeaturesCached(best_i, best_j, baseMatches);
    std::vector<cv::Point2f> bp1, bp2;
    for (const auto &m : baseMatches) {
        bp1.push_back(keypoints[best_i][m.queryIdx].pt);
        bp2.push_back(keypoints[best_j][m.trainIdx].pt);
    }
    cv::Mat R_base, t_base;
    if (!estimatePoseFromMatches(bp1, bp2, R_base, t_base)) {
        qWarning() << "estimatePoseFromMatches failed."; return false;
    }

    cv::Mat P0 = K_fallback * cv::Mat::eye(3, 4, CV_64F);
    cv::Mat RT0; cv::hconcat(R_base, t_base, RT0);
    cv::Mat P1 = K_fallback * RT0;

    std::vector<cv::Point3f> basePts;
    doTriangulate(P0, P1, bp1, bp2, basePts);

    // Cheirality (depth dương ở CẢ HAI camera) + reprojection filter, thay cho
    // khoảng world-Z toàn cục cũ. World-Z chỉ đúng khi mọi camera vẫn nhìn gần
    // trùng hướng camera gốc — sai với baseline rộng / quét orbital, và là một
    // nguyên nhân khiến cloud gần như bị xóa sạch sau SOR/ROR ở chuỗi ảnh dài.
    points3D.clear(); colors.clear(); obsIndex.clear();
    for (size_t k = 0; k < basePts.size() && k < baseMatches.size(); ++k) {
        const auto &pt = basePts[k];
        cv::Mat p4 = (cv::Mat_<double>(4,1) << pt.x, pt.y, pt.z, 1.0);
        double depth0 = cv::Mat(P0.row(2) * p4).at<double>(0);
        double depth1 = cv::Mat(P1.row(2) * p4).at<double>(0);
        if (depth0 <= 0.0 || depth1 <= 0.0) continue;
        if (computeReprojectionError(P0, pt, bp1[k]) > m_config.reprojectionErrorMax) continue;
        if (computeReprojectionError(P1, pt, bp2[k]) > m_config.reprojectionErrorMax) continue;

        int newIdx = (int)points3D.size();
        points3D.push_back(pt);
        cv::Point2f kp = keypoints[best_i][baseMatches[k].queryIdx].pt;
        int x = cvRound(kp.x), y = cvRound(kp.y);
        cv::Vec3b col(128, 128, 128);
        if (x >= 0 && y >= 0 && x < images[best_i].cols && y < images[best_i].rows)
            col = images[best_i].at<cv::Vec3b>(y, x);
        colors.push_back(col);

        obsIndex[obsKey(best_i, baseMatches[k].queryIdx)] = newIdx;
        obsIndex[obsKey(best_j, baseMatches[k].trainIdx)] = newIdx;
    }
    qDebug() << "  Base triangulated:" << points3D.size();
    if (points3D.empty()) { qWarning() << "Base pair produced no valid points."; return false; }

    // Incremental SfM — resection từng camera mới bằng solvePnPRansac dựa trên
    // cấu trúc 3D ĐÃ CÓ, thay vì chain pose từ Essential Matrix (xem giải
    // thích ở đầu message: recoverPose() chỉ cho translation scale-đơn-vị,
    // chain trực tiếp làm scale trôi/méo qua từng bước).
    struct PoseInfo { int imgIdx; cv::Mat P, R, t; };
    std::vector<PoseInfo> knownPoses;
    knownPoses.push_back({best_i, P0.clone(), cv::Mat::eye(3,3,CV_64F), cv::Mat::zeros(3,1,CV_64F)});
    knownPoses.push_back({best_j, P1.clone(), R_base.clone(), t_base.clone()});
    std::set<int> processed = {best_i, best_j};

    int consecutiveFailures = 0;
    for (int iter = 0; iter < N-2; ++iter) {
        auto findNextCamera = [&](bool limitWindow) {
            int bNew = -1;
            int bRef = -1;
            std::vector<cv::Point3f> bObjPts;
            std::vector<cv::Point2f> bImgPts;
            std::vector<int> bObjIndices;
            std::vector<int> bImgKpIndices;

            for (int idx = 0; idx < N; ++idx) {
                if (processed.count(idx)) continue;

                std::vector<cv::Point3f> objPts;
                std::vector<cv::Point2f> imgPts;
                std::vector<int> objIndices;
                std::vector<int> imgKpIndices;
                std::map<int, int> refMatchCount;
                std::set<int> usedKeypoints;

                for (const auto &kp : knownPoses) {
                    int refImg = kp.imgIdx;
                    int dist = std::abs(idx - refImg);
                    int ringDist = std::min(dist, N - dist);
                    if (limitWindow && ringDist > m_config.searchWindow) continue;

                    std::vector<cv::DMatch> matches;
                    matchFeaturesCached(idx, refImg, matches);
                    refMatchCount[refImg] = matches.size();

                    for (const auto &m : matches) {
                        if (usedKeypoints.count(m.queryIdx)) continue;
                        auto it = obsIndex.find(obsKey(refImg, m.trainIdx));
                        if (it != obsIndex.end()) {
                            objPts.push_back(points3D[it->second]);
                            imgPts.push_back(keypoints[idx][m.queryIdx].pt);
                            objIndices.push_back(it->second);
                            imgKpIndices.push_back(m.queryIdx);
                            usedKeypoints.insert(m.queryIdx);
                        }
                    }
                }

                if (objPts.size() > bObjPts.size()) {
                    bNew = idx;
                    bObjPts = std::move(objPts);
                    bImgPts = std::move(imgPts);
                    bObjIndices = std::move(objIndices);
                    bImgKpIndices = std::move(imgKpIndices);

                    int maxMatches = 0;
                    for (const auto &pair : refMatchCount) {
                        if (pair.second > maxMatches) {
                            maxMatches = pair.second;
                            bRef = pair.first;
                        }
                    }
                }
            }
            return std::make_tuple(bNew, bRef, bObjPts, bImgPts, bObjIndices, bImgKpIndices);
        };

        int bestNew, bestRef;
        std::vector<cv::Point3f> bestObjPts;
        std::vector<cv::Point2f> bestImgPts;
        std::vector<int> bestObjIndices;
        std::vector<int> bestImgKpIndices;

        std::tie(bestNew, bestRef, bestObjPts, bestImgPts, bestObjIndices, bestImgKpIndices) = findNextCamera(true);
        if (bestNew < 0 || (int)bestObjPts.size() < AppConstants::Reconstruction::MIN_POINTS_FOR_PNP) {
            std::tie(bestNew, bestRef, bestObjPts, bestImgPts, bestObjIndices, bestImgKpIndices) = findNextCamera(false);
        }

        if (bestNew < 0 || (int)bestObjPts.size() < AppConstants::Reconstruction::MIN_POINTS_FOR_PNP) {
            qDebug() << "  No more images can be resectioned. Stopping.";
            break;
        }

        cv::Mat rvec, tvec;
        std::vector<int> pnpInliers;
        bool pnpOk = cv::solvePnPRansac(
            bestObjPts, bestImgPts, K_fallback, distCoeffs,
            rvec, tvec, false, 1000,
            8.0f,
            0.999, pnpInliers, cv::SOLVEPNP_EPNP);

        if (!pnpOk || (int)pnpInliers.size() < AppConstants::Reconstruction::MIN_POINTS_FOR_PNP) {
            qDebug() << "  PnP failed for image" << bestNew
                     << "(inliers=" << pnpInliers.size() << "/" << bestObjPts.size() << "). Skipping.";
            processed.insert(bestNew);
            if (++consecutiveFailures >= 5) {
                qWarning() << "  5 consecutive resectioning failures — stopping early.";
                break;
            }
            continue;
        }
        consecutiveFailures = 0;

        std::vector<cv::Point3f> inlierObj; std::vector<cv::Point2f> inlierImg;
        inlierObj.reserve(pnpInliers.size()); inlierImg.reserve(pnpInliers.size());
        for (int idx : pnpInliers) { 
            inlierObj.push_back(bestObjPts[idx]); 
            inlierImg.push_back(bestImgPts[idx]); 
            // Cập nhật obsIndex để ghi nhận rằng camera mới này đang nhìn thấy các điểm 3D cũ.
            // Điều này cực kỳ quan trọng để liên kết các camera tiếp theo!
            obsIndex[obsKey(bestNew, bestImgKpIndices[idx])] = bestObjIndices[idx];
        }
        cv::solvePnPRefineLM(inlierObj, inlierImg, K_fallback, distCoeffs, rvec, tvec);

        cv::Mat R_abs; cv::Rodrigues(rvec, R_abs);
        cv::Mat t_abs = tvec;
        cv::Mat RT_n; cv::hconcat(R_abs, t_abs, RT_n);
        cv::Mat P_new = K_fallback * RT_n;

        knownPoses.push_back({bestNew, P_new.clone(), R_abs.clone(), t_abs.clone()});
        processed.insert(bestNew);

        std::vector<cv::DMatch> triMatches;
        matchFeaturesCached(bestNew, bestRef, triMatches);

        cv::Mat P_ref;
        for (const auto &kp : knownPoses) if (kp.imgIdx == bestRef) { P_ref = kp.P; break; }

        std::vector<cv::Point2f> pNew, pRef;
        std::vector<cv::DMatch>  newObsMatches;
        for (const auto &m : triMatches) {
            if (obsIndex.count(obsKey(bestRef, m.trainIdx))) continue; // đã có điểm 3D rồi
            pNew.push_back(keypoints[bestNew][m.queryIdx].pt);
            pRef.push_back(keypoints[bestRef][m.trainIdx].pt);
            newObsMatches.push_back(m);
        }

        std::vector<cv::Point3f> newPts;
        doTriangulate(P_ref, P_new, pRef, pNew, newPts);

        int added = 0;
        for (size_t k = 0; k < newPts.size() && k < newObsMatches.size(); ++k) {
            const auto &pt = newPts[k];
            cv::Mat p4 = (cv::Mat_<double>(4,1) << pt.x, pt.y, pt.z, 1.0);
            double depthRef = cv::Mat(P_ref.row(2) * p4).at<double>(0);
            double depthNew = cv::Mat(P_new.row(2) * p4).at<double>(0);
            if (depthRef <= 0.0 || depthNew <= 0.0) continue;
            if (computeReprojectionError(P_ref, pt, pRef[k]) > m_config.reprojectionErrorMax) continue;
            if (computeReprojectionError(P_new, pt, pNew[k]) > m_config.reprojectionErrorMax) continue;

            int newIdx = (int)points3D.size();
            points3D.push_back(pt);
            cv::Point2f kp2 = keypoints[bestNew][newObsMatches[k].queryIdx].pt;
            int x = cvRound(kp2.x), y = cvRound(kp2.y);
            cv::Vec3b col(128, 128, 128);
            if (x >= 0 && y >= 0 && x < images[bestNew].cols && y < images[bestNew].rows)
                col = images[bestNew].at<cv::Vec3b>(y, x);
            colors.push_back(col);

            obsIndex[obsKey(bestNew, newObsMatches[k].queryIdx)] = newIdx;
            obsIndex[obsKey(bestRef,  newObsMatches[k].trainIdx)] = newIdx;
            ++added;
        }

        qDebug() << "  Image" << bestNew << "resectioned (ref=" << bestRef
                 << " inliers=" << pnpInliers.size() << "/" << bestObjPts.size() << ")"
                 << "added" << added << "pts. Total:" << points3D.size();
    }

    qDebug() << "=== Estimated pose (PnP) raw points:" << points3D.size()
             << "| cameras resectioned:" << processed.size() << "/" << N << "===";
    if ((int)processed.size() < N / 2) {
        qWarning() << "Only resectioned" << processed.size() << "/" << N
                   << "images — result may be incomplete. Check image overlap/order.";
    }
    return !points3D.empty();
}

// ─── reconstruct (public) ────────────────────────────────────────────────────

bool ReconstructionPipeline::reconstruct() {
    if (images.size() < 2) { qWarning() << "Need at least 2 images!"; return false; }
    points3D.clear(); colors.clear();
    m_usedTrackBasedGroundTruth = false;   // ★ reset mỗi lần chạy lại

    // Cache check
    QString cachePath;
    if (!imageFiles.empty()) {
        cachePath = QFileInfo(imageFiles[0]).absolutePath() + "/" + kReconstructionCacheFileName;
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
