#ifndef SYNTHETICSCENEFIXTURE_H
#define SYNTHETICSCENEFIXTURE_H

// ─── Synthetic scene fixture dùng chung cho test reconstruction ─────────────
//
// Sinh 3 ảnh của một scene HAI mặt phẳng không đồng phẳng (board trước Z=0 +
// board sau phía trên Z=-8) từ 3 camera poses biết trước, kèm file params
// FORMAT A (imageName K R t) khớp CHÍNH XÁC cách render:
//
//   texture (u,v) → world:  P = origin + (u/texW)*axisU + (v/texH)*axisV
//   x_img = K [ R*axisU/texW , R*axisV/texH , R*origin + t ] [u,v,1]^T
//
// Nhờ đó ground-truth poses dùng cho TrackSfM trùng khớp với ảnh → test được
// toàn chuỗi extract → match → track → triangulate (+ hậu kỳ) với dữ liệu
// thật thay vì chỉ error-path. Board thứ hai phá tính đồng phẳng để
// findFundamentalMat (RANSAC) có nghiệm xác định.
//
// Tiêu cự được chọn bằng max(w, h) * 1.2 để khớp CHÍNH XÁC
// PoseEstimator::estimateIntrinsics() — nhờ vậy nhánh estimated-pose
// (IncrementalSfM, không cần file params) cũng có intrinsics ground-truth.

#include <QFile>
#include <QString>
#include <QStringList>
#include <QTextStream>

#include <opencv2/core.hpp>
#include <opencv2/imgcodecs.hpp>
#include <opencv2/imgproc.hpp>

#include <vector>

namespace synth {

constexpr int kImageWidth = 640;
constexpr int kImageHeight = 480;
// = max(kImageWidth, kImageHeight) * 1.2 → trùng PoseEstimator::estimateIntrinsics
constexpr double kFocalLength = 768.0;

inline cv::Mat makeRandomTexture(int w, int h, int seed)
{
    cv::Mat tex(h, w, CV_8UC3, cv::Scalar(245, 245, 245));
    cv::RNG rng(seed);
    for (int i = 0; i < 320; ++i) {
        const cv::Scalar color(rng.uniform(0, 256), rng.uniform(0, 256), rng.uniform(0, 256));
        const cv::Point p(rng.uniform(10, w - 10), rng.uniform(10, h - 10));
        switch (rng.uniform(0, 3)) {
        case 0:
            cv::rectangle(tex, p,
                          cv::Point(p.x + rng.uniform(8, 48), p.y + rng.uniform(8, 48)),
                          color, 1 + rng.uniform(0, 5));
            break;
        case 1:
            cv::circle(tex, p, rng.uniform(5, 22), color, 1 + rng.uniform(0, 4));
            break;
        default:
            cv::line(tex, p,
                     cv::Point(p.x + rng.uniform(-45, 45), p.y + rng.uniform(-45, 45)),
                     color, 1 + rng.uniform(0, 5));
            break;
        }
    }
    return tex;
}

struct SynthCamera {
    cv::Matx33d K;
    cv::Matx33d R; // world→cam rotation, hàng = trục camera trong world
    cv::Vec3d t;   // x_cam = R*X + t
};

inline SynthCamera makeCamera(const cv::Vec3d &center, double f, int w, int h)
{
    // Pinhole convention: +Z hướng TỪ camera vào scene (điểm trước camera có
    // z_cam > 0 — MultiViewTriangulator loại bỏ mọi z <= 0).
    const cv::Vec3d zAxis = -cv::normalize(center); // nhìn về gốc toạ độ
    const cv::Vec3d up(0, 1, 0);
    const cv::Vec3d xAxis = cv::normalize(up.cross(zAxis));
    const cv::Vec3d yAxis = zAxis.cross(xAxis);

    SynthCamera cam;
    cam.K = cv::Matx33d(f, 0, w * 0.5, 0, f, h * 0.5, 0, 0, 1);
    cam.R = cv::Matx33d(xAxis[0], xAxis[1], xAxis[2],
                        yAxis[0], yAxis[1], yAxis[2],
                        zAxis[0], zAxis[1], zAxis[2]);
    cam.t = -(cam.R * center);
    return cam;
}

inline cv::Matx33d planeHomography(const SynthCamera &cam,
                                   const cv::Vec3d &origin,
                                   const cv::Vec3d &axisU, const cv::Vec3d &axisV,
                                   int texW, int texH)
{
    const cv::Vec3d c0 = cam.R * (axisU / static_cast<double>(texW));
    const cv::Vec3d c1 = cam.R * (axisV / static_cast<double>(texH));
    const cv::Vec3d c2 = cam.R * origin + cam.t;
    cv::Matx33d H;
    H(0, 0) = c0[0]; H(0, 1) = c1[0]; H(0, 2) = c2[0];
    H(1, 0) = c0[1]; H(1, 1) = c1[1]; H(1, 2) = c2[1];
    H(2, 0) = c0[2]; H(2, 1) = c1[2]; H(2, 2) = c2[2];
    return cam.K * H;
}

struct SyntheticScene {
    QStringList imageFiles;
    QString paramsFile;
};

inline bool writeSyntheticScene(const QString &dirPath, SyntheticScene &scene)
{
    constexpr int W = kImageWidth;
    constexpr int H = kImageHeight;
    const cv::Mat texA = makeRandomTexture(480, 360, 1001); // board trước
    const cv::Mat texB = makeRandomTexture(512, 352, 2002); // board sau

    // Board A (Z=0): X [-10,10], Y [-7.5,7.5]
    const cv::Vec3d aOrigin(-10.0, 7.5, 0.0), aU(20.0, 0.0, 0.0), aV(0.0, -15.0, 0.0);
    // Board B (Z=-8, phía TRÊN A): X [-8,8], Y [9,20] — thấy được từ cả 3 camera
    const cv::Vec3d bOrigin(-8.0, 20.0, -8.0), bU(16.0, 0.0, 0.0), bV(0.0, -11.0, 0.0);

    const std::vector<cv::Vec3d> centers = {
        cv::Vec3d(-8.0, 0.0, 42.0),
        cv::Vec3d(0.0, 0.0, 42.0),
        cv::Vec3d(8.0, 0.0, 42.0)};

    QStringList paramLines;
    paramLines << QString::number(centers.size());

    for (size_t i = 0; i < centers.size(); ++i) {
        const SynthCamera cam = makeCamera(centers[i], kFocalLength, W, H);
        const cv::Matx33d HA = planeHomography(cam, aOrigin, aU, aV, texA.cols, texA.rows);
        const cv::Matx33d HB = planeHomography(cam, bOrigin, bU, bV, texB.cols, texB.rows);

        // Board B xa hơn được vẽ nền; board A (gần) phủ lên trên qua mask.
        cv::Mat viewB, viewA, maskA;
        cv::warpPerspective(texB, viewB, cv::Mat(HB), cv::Size(W, H));
        cv::warpPerspective(texA, viewA, cv::Mat(HA), cv::Size(W, H));
        cv::Mat white(texA.size(), CV_8UC1, cv::Scalar(255));
        cv::warpPerspective(white, maskA, cv::Mat(HA), cv::Size(W, H));

        cv::Mat view = viewB.clone();
        viewA.copyTo(view, maskA);

        const QString imgName = QStringLiteral("view%1.png").arg(i);
        if (!cv::imwrite((dirPath + "/" + imgName).toStdString(), view))
            return false;
        scene.imageFiles << dirPath + "/" + imgName;

        QString line = imgName;
        const auto append3x3 = [&line](const cv::Matx33d &m) {
            for (int r = 0; r < 3; ++r)
                for (int c = 0; c < 3; ++c)
                    line += " " + QString::number(m(r, c), 'g', 17);
        };
        append3x3(cam.K);
        append3x3(cam.R);
        for (int r = 0; r < 3; ++r)
            line += " " + QString::number(cam.t[r], 'g', 17);
        paramLines << line;
    }

    scene.paramsFile = dirPath + "/cameras_params.txt";
    QFile file(scene.paramsFile);
    if (!file.open(QIODevice::WriteOnly | QIODevice::Text))
        return false;
    QTextStream stream(&file);
    for (const QString &line : paramLines)
        stream << line << "\n";
    return true;
}

} // namespace synth

#endif // SYNTHETICSCENEFIXTURE_H
