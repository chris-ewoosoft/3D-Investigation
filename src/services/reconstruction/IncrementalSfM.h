#ifndef INCREMENTALSFM_H
#define INCREMENTALSFM_H

#include "SfmStage.h"

/**
 * @brief Reconstruction branch B: incremental SfM with estimated poses.
 *
 * Flow: scan image pairs (windowed) for the best Essential-Matrix base pair
 * → triangulate the base pair (cheirality + reprojection filtered) →
 * repeatedly resection the next camera by PnP against the existing 3D
 * structure → triangulate new points against the best reference camera.
 *
 * Camera intrinsics are estimated (or taken from the K passed in by the
 * pipeline) and stored back into m_K for downstream reprojection work.
 */
class IncrementalSfM : public SfmStage {
public:
    IncrementalSfM(const std::vector<cv::Mat> &images,
                   const std::vector<std::vector<cv::KeyPoint>> &keypoints,
                   const std::vector<cv::Mat> &descriptors,
                   const ReconstructionConfig &config,
                   cv::Mat &K,
                   cv::Mat &distCoeffs,
                   std::vector<cv::Point3f> &points3D,
                   std::vector<cv::Vec3b> &colors,
                   QString &lastError);

    /** Runs the branch. Returns false (with m_lastError set) on failure. */
    bool run();

private:
    // Camera model (owned by the pipeline, mutated: intrinsics re-estimated in run())
    cv::Mat &m_K;
    cv::Mat &m_distCoeffs;

    bool estimatePoseFromMatches(const std::vector<cv::Point2f> &pts1,
                                 const std::vector<cv::Point2f> &pts2,
                                 cv::Mat &R, cv::Mat &t);
    void doTriangulate(const cv::Mat &P0, const cv::Mat &P1,
                       const std::vector<cv::Point2f> &pts0,
                       const std::vector<cv::Point2f> &pts1,
                       std::vector<cv::Point3f> &outPts);
    double computeReprojectionError(const cv::Mat &P, const cv::Point3f &pt3d,
                                    const cv::Point2f &pt2d);
};

#endif // INCREMENTALSFM_H
