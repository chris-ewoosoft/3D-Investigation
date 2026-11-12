#include "FeatureExtractor.h"
#include <opencv2/imgproc.hpp>

void FeatureExtractor::extract(const cv::Mat &img, const ReconstructionConfig &config,
                               std::vector<cv::KeyPoint> &keypoints, cv::Mat &descriptors)
{
    cv::Mat gray;
    cv::cvtColor(img, gray, cv::COLOR_BGR2GRAY);
    cv::Ptr<cv::CLAHE> clahe = cv::createCLAHE(2.0, cv::Size(8, 8));
    clahe->apply(gray, gray);

    // Create detector per-call for thread safety
    cv::Ptr<cv::Feature2D> localDetector = cv::SIFT::create(
        config.sift.nfeatures,
        config.sift.nOctaveLayers,
        config.sift.contrastThreshold,
        config.sift.edgeThreshold,
        config.sift.sigma
    );
    localDetector->detectAndCompute(gray, cv::noArray(), keypoints, descriptors);
}
