#ifndef TRACKSFM_H
#define TRACKSFM_H

#include "SfmStage.h"

/**
 * @brief Reconstruction branch A: track-based multi-view triangulation using
 *        ground-truth camera poses (from CameraParamsParser).
 *
 * Flow: register all (image, keypoint) observations → windowed pairwise
 * matching (fundamental-matrix filtered) → union-find tracks → per-track
 * multi-view triangulation + refinement → points/colors out.
 */
class TrackSfM : public SfmStage {
public:
    TrackSfM(const std::vector<cv::Mat> &images,
             const std::vector<std::vector<cv::KeyPoint>> &keypoints,
             const std::vector<cv::Mat> &descriptors,
             const ReconstructionConfig &config,
             const std::vector<CameraParams> &camParams,
             std::vector<cv::Point3f> &points3D,
             std::vector<cv::Vec3b> &colors,
             QString &lastError);

    /** Runs the branch. Returns false (with m_lastError set) on 0-point result. */
    bool run();

private:
    const std::vector<CameraParams> &camParams;
};

#endif // TRACKSFM_H
