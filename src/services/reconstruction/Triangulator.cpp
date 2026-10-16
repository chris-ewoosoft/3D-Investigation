#include "Triangulator.h"
#include <opencv2/calib3d.hpp>
#include <cmath>

void Triangulator::triangulate(const cv::Mat &P0, const cv::Mat &P1,
                                const std::vector<cv::Point2f> &pts0,
                                const std::vector<cv::Point2f> &pts1,
                                std::vector<cv::Point3f> &outPts)
{
    cv::Mat pts4D;
    cv::triangulatePoints(P0, P1, pts0, pts1, pts4D);
    outPts.clear();
    outPts.reserve(pts4D.cols);
    if (pts4D.type() != CV_64F) pts4D.convertTo(pts4D, CV_64F);
    for (int i = 0; i < pts4D.cols; ++i) {
        double w = pts4D.at<double>(3, i);
        if (std::abs(w) < 1e-9) continue;
        outPts.push_back(cv::Point3f(
            (float)(pts4D.at<double>(0, i) / w),
            (float)(pts4D.at<double>(1, i) / w),
            (float)(pts4D.at<double>(2, i) / w)));
    }
}
