#include "SfmStage.h"
#include "FeatureMatcher.h"

SfmStage::SfmStage(const std::vector<cv::Mat> &images,
                   const std::vector<std::vector<cv::KeyPoint>> &keypoints,
                   const std::vector<cv::Mat> &descriptors,
                   const ReconstructionConfig &config,
                   std::vector<cv::Point3f> &points3D,
                   std::vector<cv::Vec3b> &colors,
                   QString &lastError)
    : images(images)
    , keypoints(keypoints)
    , descriptors(descriptors)
    , m_config(config)
    , points3D(points3D)
    , colors(colors)
    , m_lastError(lastError)
{
}

void SfmStage::matchFeatures(int idx1, int idx2,
                             std::vector<cv::DMatch> &goodMatches) {
    FeatureMatcher::match(descriptors[idx1], descriptors[idx2], m_config, goodMatches);
}
