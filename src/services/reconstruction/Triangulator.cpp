#include "Triangulator.h"
#include <opencv2/calib3d.hpp>
#include <cmath>
#include <limits>

void Triangulator::triangulate(const cv::Mat &P0, const cv::Mat &P1,
                                const std::vector<cv::Point2f> &pts0,
                                const std::vector<cv::Point2f> &pts1,
                                std::vector<cv::Point3f> &outPts)
{
    cv::Mat pts4D;
    cv::triangulatePoints(P0, P1, pts0, pts1, pts4D);
    if (pts4D.type() != CV_64F) pts4D.convertTo(pts4D, CV_64F);

    // Index alignment is guaranteed by construction: every correspondence gets
    // an output slot. A degenerate triangulation (w ≈ 0 — the point lies at
    // infinity) stays NaN instead of being dropped; dropping it shifted every
    // following point onto the wrong match in IncrementalSfM, silently and
    // only for the pairs that happened to contain such a point. Callers skip
    // non-finite points (see IncrementalSfM's cheirality test).
    const float nan = std::numeric_limits<float>::quiet_NaN();
    outPts.assign(static_cast<size_t>(pts4D.cols), cv::Point3f(nan, nan, nan));
    for (int i = 0; i < pts4D.cols; ++i) {
        double w = pts4D.at<double>(3, i);
        // w == 0, or a NaN w, keeps the NaN slot filled above (NaN is not
        // < 1e-9 either, so it falls through to the divide and stays NaN).
        if (std::abs(w) < 1e-9) continue;
        outPts[i] = cv::Point3f(
            (float)(pts4D.at<double>(0, i) / w),
            (float)(pts4D.at<double>(1, i) / w),
            (float)(pts4D.at<double>(2, i) / w));
    }
}
