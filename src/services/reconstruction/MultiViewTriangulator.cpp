#include "MultiViewTriangulator.h"
#include <Eigen/Dense>
#include <cmath>

bool MultiViewTriangulator::triangulateTrack(FeatureTrack &track,
                                             const std::vector<cv::Mat> &P,
                                             const std::vector<std::vector<cv::KeyPoint>> &kps,
                                             double maxReprojError)
{
    int n = (int)track.observations.size();
    if (n < 2) return false;

    // Xây ma trận A (2n x 4) cho DLT: mỗi view góp 2 dòng
    Eigen::MatrixXd A(2 * n, 4);
    for (int i = 0; i < n; ++i) {
        int imgIdx = track.observations[i].first;
        int kpIdx  = track.observations[i].second;
        const cv::Mat &Pm = P[imgIdx];
        const cv::Point2f &pt = kps[imgIdx][kpIdx].pt;

        for (int c = 0; c < 4; ++c) {
            A(2*i,   c) = pt.x * Pm.at<double>(2,c) - Pm.at<double>(0,c);
            A(2*i+1, c) = pt.y * Pm.at<double>(2,c) - Pm.at<double>(1,c);
        }
    }

    Eigen::JacobiSVD<Eigen::MatrixXd> svd(A, Eigen::ComputeFullV);
    Eigen::Vector4d X = svd.matrixV().col(3);
    if (std::abs(X(3)) < 1e-9) return false;
    X /= X(3);

    cv::Point3f pt3d((float)X(0), (float)X(1), (float)X(2));

    // Kiểm tra reprojection trên TẤT CẢ view — loại quan sát lỗi (không phải cả track)
    std::vector<std::pair<int,int>> keptObs;
    for (auto &obs : track.observations) {
        int imgIdx = obs.first, kpIdx = obs.second;
        cv::Mat p4 = (cv::Mat_<double>(4,1) << pt3d.x, pt3d.y, pt3d.z, 1.0);
        cv::Mat proj = P[imgIdx] * p4;
        double z = proj.at<double>(2);
        if (z <= 0) continue; // sau lưng camera

        double u = proj.at<double>(0) / z;
        double v = proj.at<double>(1) / z;
        const cv::Point2f &obsPt = kps[imgIdx][kpIdx].pt;
        double err = std::sqrt((u-obsPt.x)*(u-obsPt.x) + (v-obsPt.y)*(v-obsPt.y));
        if (err <= maxReprojError) keptObs.push_back(obs);
    }

    // Cần tối thiểu 2 view hợp lệ để giữ track
    if (keptObs.size() < 2) return false;
    track.observations = keptObs;
    track.point3D = pt3d;
    track.valid = true;
    return true;
}

void MultiViewTriangulator::refinePoint(FeatureTrack &track,
                                        const std::vector<cv::Mat> &P,
                                        const std::vector<std::vector<cv::KeyPoint>> &kps,
                                        int iterations)
{
    // Gauss-Newton tối thiểu hóa tổng reprojection error qua mọi view của track.
    // Pose (P) cố định → chỉ 3 biến (x,y,z) cần tối ưu → rất nhẹ, hội tụ nhanh.
    Eigen::Vector3d X(track.point3D.x, track.point3D.y, track.point3D.z);

    for (int it = 0; it < iterations; ++it) {
        Eigen::MatrixXd J(2 * track.observations.size(), 3);
        Eigen::VectorXd r(2 * track.observations.size());

        for (size_t i = 0; i < track.observations.size(); ++i) {
            int imgIdx = track.observations[i].first;
            int kpIdx  = track.observations[i].second;
            const cv::Mat &Pm = P[imgIdx];
            const cv::Point2f &obsPt = kps[imgIdx][kpIdx].pt;

            double px = Pm.at<double>(0,0)*X(0) + Pm.at<double>(0,1)*X(1) + Pm.at<double>(0,2)*X(2) + Pm.at<double>(0,3);
            double py = Pm.at<double>(1,0)*X(0) + Pm.at<double>(1,1)*X(1) + Pm.at<double>(1,2)*X(2) + Pm.at<double>(1,3);
            double pz = Pm.at<double>(2,0)*X(0) + Pm.at<double>(2,1)*X(1) + Pm.at<double>(2,2)*X(2) + Pm.at<double>(2,3);
            if (std::abs(pz) < 1e-9) pz = 1e-9;

            double u = px / pz, v = py / pz;
            r(2*i)   = u - obsPt.x;
            r(2*i+1) = v - obsPt.y;

            // Đạo hàm riêng của u,v theo X,Y,Z (chain rule qua phép chia phối cảnh)
            for (int c = 0; c < 3; ++c) {
                double dpx = Pm.at<double>(0,c), dpy = Pm.at<double>(1,c), dpz = Pm.at<double>(2,c);
                J(2*i,   c) = (dpx * pz - px * dpz) / (pz*pz);
                J(2*i+1, c) = (dpy * pz - py * dpz) / (pz*pz);
            }
        }

        Eigen::Vector3d delta = (J.transpose()*J).ldlt().solve(-J.transpose()*r);
        if (!delta.allFinite()) break;
        X += delta;
        if (delta.norm() < 1e-6) break;
    }

    track.point3D = cv::Point3f((float)X(0), (float)X(1), (float)X(2));
}

bool MultiViewTriangulator::validateTrack(FeatureTrack &track,
                                          const std::vector<cv::Mat> &P,
                                          const std::vector<std::vector<cv::KeyPoint>> &kps,
                                          double maxReprojError)
{
    if (track.observations.size() < 2)
        return false;

    std::vector<std::pair<int,int>> keptObs;
    keptObs.reserve(track.observations.size());
    cv::Mat point4d = (cv::Mat_<double>(4,1) << track.point3D.x, track.point3D.y,
                       track.point3D.z, 1.0);

    for (const auto &obs : track.observations) {
        int imgIdx = obs.first;
        int kpIdx = obs.second;
        cv::Mat projected = P[imgIdx] * point4d;
        double z = projected.at<double>(2);
        if (z <= 0.0)
            continue;

        double u = projected.at<double>(0) / z;
        double v = projected.at<double>(1) / z;
        const cv::Point2f &observed = kps[imgIdx][kpIdx].pt;
        double dx = u - observed.x;
        double dy = v - observed.y;
        if (std::sqrt(dx * dx + dy * dy) <= maxReprojError)
            keptObs.push_back(obs);
    }

    if (keptObs.size() < 2)
        return false;

    track.observations = keptObs;
    track.valid = true;
    return true;
}
