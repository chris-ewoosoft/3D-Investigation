#ifndef SFMSTAGE_H
#define SFMSTAGE_H

#include <opencv2/core.hpp>
#include <opencv2/features2d.hpp>
#include <QString>
#include <vector>

#include "ReconstructionConfig.h"

/**
 * @brief Non-owning shared state + shared helpers for the two SfM
 *        reconstruction branches (TrackSfM / IncrementalSfM).
 *
 * Member names intentionally match the legacy ReconstructionPipeline
 * members so the extracted algorithm bodies remain verbatim — the caller
 * (ReconstructionPipeline) owns all storage and outlives the stage.
 */
class SfmStage {
protected:
    SfmStage(const std::vector<cv::Mat> &images,
             const std::vector<std::vector<cv::KeyPoint>> &keypoints,
             const std::vector<cv::Mat> &descriptors,
             const ReconstructionConfig &config,
             std::vector<cv::Point3f> &points3D,
             std::vector<cv::Vec3b> &colors,
             QString &lastError);

    // Inputs (owned by the pipeline)
    const std::vector<cv::Mat>                   &images;
    const std::vector<std::vector<cv::KeyPoint>> &keypoints;
    const std::vector<cv::Mat>                   &descriptors;
    const ReconstructionConfig                   &m_config;

    // Outputs
    std::vector<cv::Point3f> &points3D;
    std::vector<cv::Vec3b>   &colors;
    QString                  &m_lastError;

    // Thin wrapper so call-sites read as matchFeatures(i, j, out)
    void matchFeatures(int idx1, int idx2, std::vector<cv::DMatch> &goodMatches);
};

#endif // SFMSTAGE_H
