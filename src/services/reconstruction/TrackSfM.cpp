#include "TrackSfM.h"
#include "FeatureMatcher.h"
#include "FeatureTrack.h"
#include "MultiViewTriangulator.h"

#include <QDebug>

#include <opencv2/calib3d.hpp>

#include <algorithm>
#include <atomic>
#include <mutex>

TrackSfM::TrackSfM(const std::vector<cv::Mat> &images,
                   const std::vector<std::vector<cv::KeyPoint>> &keypoints,
                   const std::vector<cv::Mat> &descriptors,
                   const ReconstructionConfig &config,
                   const std::vector<CameraParams> &camParams,
                   std::vector<cv::Point3f> &points3D,
                   std::vector<cv::Vec3b> &colors,
                   QString &lastError)
    : SfmStage(images, keypoints, descriptors, config, points3D, colors, lastError)
    , camParams(camParams)
{
}

bool TrackSfM::run() {
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
            matches = FeatureMatcher::filterByFundamental(
                matches, keypoints[i], keypoints[j],
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
    qDebug() << "=== Ground-truth track-based points:" << points3D.size() << "===";
    if (points3D.empty()) {
        m_lastError = QStringLiteral(
            "Ground-truth triangulation produced 0 points: built %1 multi-view track(s) "
            "from the image pairs, but all were rejected (reprojection error > %2 px "
            "or triangulation angle too small).")
            .arg(tracks.size()).arg(triParams.maxReprojError);
        return false;
    }
    return true;
}
