#include "ReconstructionPipeline.h"
#include "ReconstructionPostProcessor.h"
#include "ReconstructionService.h"
#include "FeatureMatcher.h"
#include "Triangulator.h"
#include "SyntheticSceneFixture.h"
#include <QElapsedTimer>
#include <QFileInfo>
#include <QSignalSpy>
#include <QTemporaryDir>
#include <QThread>
#include <QtTest>

#include <pcl/conversions.h>
#include <pcl/point_types.h>

#include <cmath>

using namespace synth;

class TestReconstructionPipeline : public QObject {
  Q_OBJECT

private slots:
  void testInitialState() {
    ReconstructionPipeline pipeline;
    QCOMPARE(pipeline.getImages().size(), 0);
    QCOMPARE(pipeline.getPointCloud().size(), 0);
    QCOMPARE(pipeline.getPointColors().size(), 0);
  }

  void testSetConfig() {
    ReconstructionPipeline pipeline;
    ReconstructionConfig config;
    config.sift.nfeatures = 1000;
    config.filter.sorMeanK = 40;

    pipeline.setConfig(config);

    QCOMPARE(pipeline.config().sift.nfeatures, 1000);
    QCOMPARE(pipeline.config().filter.sorMeanK, 40);
  }

  // ─── Error-path contract (orchestrator diagnostics) ──────────────────────

  void reconstructWithoutImagesFailsWithReason() {
    ReconstructionPipeline pipeline;
    QVERIFY(!pipeline.reconstruct());
    // Chẩn đoán cụ thể, không phải thông báo chung.
    QVERIFY(pipeline.errorString().contains("at least 2"));
  }

  void reconstructWithOneImageFails() {
    ReconstructionPipeline pipeline;
    // Đường dẫn không tồn tại — không ảnh nào load được.
    pipeline.setImages({QStringLiteral("F:/nonexistent/img0.jpg")});
    // The path of an image that could not be loaded is DROPPED together with
    // the image: the path list and the image list have to stay
    // index-aligned, because the camera parameters are matched against the
    // images by index. A path whose image failed to load would shift every
    // following pair.
    QCOMPARE(pipeline.getImages().size(), 0);
    QVERIFY(!pipeline.reconstruct());
    QVERIFY(pipeline.errorString().contains("at least 2"));
  }

  void unreadableImageDoesNotShiftTheFollowingImages() {
    ReconstructionPipeline pipeline;
    // A readable image, an unreadable one, a readable one: the unreadable
    // one must not leave a hole in the list.
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    const QString good = dir.path() + QStringLiteral("/good.png");
    cv::Mat sample(8, 8, CV_8UC3, cv::Scalar(10, 20, 30));
    QVERIFY(cv::imwrite(good.toStdString(), sample));

    pipeline.setImages({good, QStringLiteral("F:/nonexistent/missing.png"), good});
    QCOMPARE(pipeline.getImages().size(), 2);
    QVERIFY(!pipeline.getImages()[0].endsWith(QStringLiteral("missing.png")));
    QVERIFY(!pipeline.getImages()[1].endsWith(QStringLiteral("missing.png")));
  }

  void loadCameraParamsMissingFileFails() {
    ReconstructionPipeline pipeline;
    QVERIFY(!pipeline.loadCameraParams(QStringLiteral("F:/nonexistent/params.txt")));
  }

  void processPointCloudOnEmptyCloudIsNoop() {
    ReconstructionPipeline pipeline;
    pipeline.processPointCloud(); // không được crash trên cloud rỗng
    QCOMPARE(pipeline.getPointCloud().size(), 0);
  }

  // ─── FeatureMatcher::filterByFundamental contract ────────────────────────

  void fundamentalFilterPassesThroughSmallMatchSets() {
    // < 8 matches → trả về nguyên input (không đủ để fit F).
    std::vector<cv::DMatch> matches(3);
    matches[0] = cv::DMatch(0, 0, 0.1f);
    matches[1] = cv::DMatch(1, 1, 0.2f);
    matches[2] = cv::DMatch(2, 2, 0.3f);
    std::vector<cv::KeyPoint> kp1(3), kp2(3);
    for (int i = 0; i < 3; ++i) {
      kp1[i] = cv::KeyPoint(float(i), float(i), 1.0f);
      kp2[i] = cv::KeyPoint(float(i) + 0.5f, float(i) + 0.5f, 1.0f);
    }

    auto out = FeatureMatcher::filterByFundamental(matches, kp1, kp2, 1.0);
    QCOMPARE(out.size(), matches.size());
  }

  void fundamentalFilterRejectsNonPositiveThreshold() {
    std::vector<cv::DMatch> matches(10);
    std::vector<cv::KeyPoint> kp1(10), kp2(10);
    for (int i = 0; i < 10; ++i) {
      matches[i] = cv::DMatch(i, i, 0.1f);
      kp1[i] = cv::KeyPoint(float(i * 10), float(i * 10), 1.0f);
      kp2[i] = cv::KeyPoint(float(i * 10 + 1), float(i * 10 + 1), 1.0f);
    }

    // threshold <= 0 → filter bị vô hiệu, trả về nguyên input.
    auto out = FeatureMatcher::filterByFundamental(matches, kp1, kp2, 0.0);
    QCOMPARE(out.size(), matches.size());
  }
  // ─── Cooperative cancellation (pipeline) ─────────────────────────────────

  void reconstructCancelsAtFirstCheckpoint() {
    ReconstructionPipeline pipeline;
    int checks = 0;
    const bool ok = pipeline.reconstruct([&checks] {
      ++checks;
      return true; // yêu cầu hủy ngay từ checkpoint đầu
    });
    QVERIFY(!ok);
    QVERIFY(checks >= 1);
    QVERIFY(pipeline.errorString().contains(QStringLiteral("ancelled")));
  }

  void reconstructWithNonCancellingPredicateRunsNormally() {
    ReconstructionPipeline pipeline;
    // Predicate không bao giờ hủy → luồng xử lý không đổi (validation vẫn chạy).
    const bool ok = pipeline.reconstruct([] { return false; });
    QVERIFY(!ok);
    QVERIFY(pipeline.errorString().contains(QStringLiteral("at least 2")));
  }

  // ─── Service: stopReconstruction() cooperative + state reset ─────────────

  void serviceStopReconstructionCompletesAsyncAndResetsState() {
    ReconstructionService service;
    QSignalSpy spy(&service, &IReconstructionService::reconstructFinished);

    // Ảnh không tồn tại → pipeline kết thúc nhanh; stopReconstruction() có
    // thể tới trước hoặc sau khi pipeline bắt đầu — kết cục bắt buộc là
    // reconstructFinished(false) và service trở về idle (không kẹt m_running,
    // không block UI, không terminate).
    service.setImages({QStringLiteral("F:/nonexistent/a.jpg"),
                       QStringLiteral("F:/nonexistent/b.jpg")});

    QVERIFY(service.reconstruct());
    QVERIFY(service.isRunning());
    service.stopReconstruction(); // non-blocking — hủy ở checkpoint kế tiếp

    QVERIFY(spy.wait(10000));
    QVERIFY(spy.count() >= 1);
    QCOMPARE(spy.takeFirst().at(0).toBool(), false);
    QVERIFY(!service.isRunning());

    // State được reset sạch: chạy lại được ngay (bug cũ: terminate() bỏ qua
    // đường finished → m_running kẹt true vĩnh viễn).
    QVERIFY(service.reconstruct());
    QVERIFY(spy.wait(10000));
    QVERIFY(spy.count() >= 1);
    QCOMPARE(spy.takeFirst().at(0).toBool(), false);
    QVERIFY(!service.isRunning());
  }
  // ─── End-to-end với fixture ảnh tổng hợp (ground-truth poses) ────────────

  void syntheticSceneSfMProducesPoints() {
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    SyntheticScene scene;
    QVERIFY(writeSyntheticScene(dir.path(), scene));
    QCOMPARE(scene.imageFiles.size(), 3);

    ReconstructionPipeline pipeline;
    std::vector<QString> paths;
    for (const QString &f : scene.imageFiles)
      paths.emplace_back(f);
    pipeline.setImages(paths);
    QCOMPARE(pipeline.getImages().size(), size_t(3));
    QVERIFY(pipeline.loadCameraParams(scene.paramsFile));

    // Hủy cooperative ngay khi cloud đầu tiên xuất hiện → bỏ qua hậu kỳ nặng
    // (MLS 1M + Poisson) nhưng vẫn kiểm chứng trọn chuỗi
    // extract → match → fundamental → track → triangulate.
    const bool ok = pipeline.reconstruct(
        [&pipeline] { return !pipeline.getPointCloud().empty(); });

    QVERIFY2(!ok, "phải kết thúc ở nhánh cancelled");
    QVERIFY2(pipeline.errorString().contains(QStringLiteral("ancelled")),
             qPrintable(pipeline.errorString()));

    const auto cloud = pipeline.getPointCloud();
    QVERIFY2(cloud.size() >= 50,
             qPrintable(QStringLiteral("chỉ có %1 điểm — SfM chain không chạy đúng")
                            .arg(cloud.size())));

    // Điểm phải nằm quanh 2 board (Z≈0 và Z≈-8) trong khung nhìn frame.
    // Tam giác từ track hiếm khi có outlier (parallax nhỏ → điểm xa) nên chỉ
    // yêu cầu ≥90% điểm nằm trong hộp bao của scene.
    size_t inBounds = 0;
    for (const auto &p : cloud) {
      if (p.z > -9.5f && p.z < 0.5f && std::abs(p.x) < 25.0f && std::abs(p.y) < 25.0f)
        ++inBounds;
    }
    qInfo() << "in-bounds points:" << inBounds << "/" << cloud.size();
    QVERIFY2(inBounds * 100 >= cloud.size() * 90, "quá nhiều điểm lạc khỏi scene");
  }

  // Nhánh ESTIMATED pose (IncrementalSfM): KHÔNG nạp params — intrinsics do
  // PoseEstimator::estimateIntrinsics() suy ra (khớp kFocalLength của fixture),
  // base pair chọn bằng Essential Matrix, các view còn lại resection bằng PnP.
  // Dừng ở checkpoint sau SfM để bỏ qua hậu kỳ nặng.
  void syntheticSceneEstimatedPoseProducesPoints() {
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    SyntheticScene scene;
    QVERIFY(writeSyntheticScene(dir.path(), scene));

    ReconstructionPipeline pipeline;
    std::vector<QString> paths;
    for (const QString &f : scene.imageFiles)
      paths.emplace_back(f);
    pipeline.setImages(paths);

    const bool ok = pipeline.reconstruct(
        [&pipeline] { return !pipeline.getPointCloud().empty(); });

    QVERIFY2(!ok, "phải kết thúc ở nhánh cancelled");
    QVERIFY2(pipeline.errorString().contains(QStringLiteral("ancelled")),
             qPrintable(pipeline.errorString()));
    QVERIFY2(pipeline.getPointCloud().size() >= 50,
             qPrintable(QStringLiteral("chỉ có %1 điểm — estimated-pose SfM không chạy đúng")
                            .arg(pipeline.getPointCloud().size())));
  }

  // ─── ReconstructionPostProcessor::filterFarOutliers contract ─────────────

  void farOutlierFilterDropsDistantPointsAndKeepsColorsAligned() {
    std::vector<cv::Point3f> pts;
    std::vector<cv::Vec3b> cols;
    // Cụm 100 điểm dày trong hộp 1 đơn vị.
    for (int i = 0; i < 100; ++i) {
      pts.emplace_back(float(i % 10) * 0.1f, float((i / 10) % 10) * 0.1f, 0.0f);
      cols.emplace_back(cv::Vec3b(1, 2, 3));
    }
    // 3 outlier rất xa, màu khác để kiểm tra căn chỉnh colors.
    for (int i = 0; i < 3; ++i) {
      pts.emplace_back(100.0f + float(i), 0.0f, 0.0f);
      cols.emplace_back(cv::Vec3b(9, 9, 9));
    }

    ReconstructionPostProcessor::filterFarOutliers(pts, cols, 2.5f);

    QCOMPARE(cols.size(), pts.size());
    QVERIFY(pts.size() >= 100); // giữ nguyên cụm
    QVERIFY(pts.size() < 103);  // loại outlier
    for (const auto &p : pts)
      QVERIFY(p.x < 50.0f);
    for (const auto &c : cols)
      QCOMPARE(int(c[0]), 1);

    // Cloud rỗng → no-op, không crash.
    std::vector<cv::Point3f> emptyPts;
    std::vector<cv::Vec3b> emptyCols;
    ReconstructionPostProcessor::filterFarOutliers(emptyPts, emptyCols, 2.5f);
    QVERIFY(emptyPts.empty());
  }

  // ─── Post-processing: 0 means "this filter is off" ───────────────────────
  // The config defaults are all non-zero, so 0 is unambiguous. The old code
  // replaced it with a default, which made the "auto-clean off" preference a
  // no-op: the filters ran anyway.
  void postProcessingTreatsZeroAsFilterDisabled() {
    const std::vector<cv::Point3f> seed{{0.f, 0.f, 0.f}, {0.001f, 0.f, 0.f},
                                       {0.f, 0.001f, 0.f}};

    FilterConfig disabled;
    disabled.rorMinNeighborsTrack = 0;
    disabled.sorMeanKTrack = 0;
    disabled.voxelLeafSizeTrack = 0.0001f; // giữ 3 điểm ở 3 voxel khác nhau
    std::vector<cv::Point3f> pts = seed;
    std::vector<cv::Vec3b> cols(pts.size(), cv::Vec3b(4, 5, 6));

    ReconstructionPostProcessor::processPointCloud(disabled, pts, cols);
    QCOMPARE(pts.size(), seed.size());
    QCOMPARE(cols.size(), pts.size());

    // The same cloud with ROR switched on at an impossible neighbour count
    // loses every point — proof that the run above really had the filter off
    // and was not silently using a default. The cloud has to be big enough for
    // the adaptive radius estimate (it needs 16+ points), which is itself part
    // of the filter's contract.
    std::vector<cv::Point3f> pts2;
    for (int i = 0; i < 24; ++i)
      pts2.emplace_back(float(i % 4) * 0.001f, float(i / 4) * 0.001f, 0.0f);
    std::vector<cv::Vec3b> cols2(pts2.size(), cv::Vec3b(4, 5, 6));
    FilterConfig enabled = disabled;
    enabled.rorMinNeighborsTrack = 1000;
    ReconstructionPostProcessor::processPointCloud(enabled, pts2, cols2);
    QVERIFY(pts2.empty());
    QCOMPARE(cols2.size(), pts2.size());
  }

  void postProcessingIgnoresAnEmptyCloud() {
    std::vector<cv::Point3f> pts;
    std::vector<cv::Vec3b> cols;
    FilterConfig filter;
    ReconstructionPostProcessor::processPointCloud(filter, pts, cols);
    QVERIFY(pts.empty());
    QVERIFY(cols.empty());
  }

  // ─── ReconstructionPostProcessor::projectWithP contract ──────────────────
  void projectionUsesTheMatrixAndRejectsPointsBehindTheCamera() {
    CameraParams cam;
    cam.P = (cv::Mat_<double>(3, 4) << 100.0, 0.0, 50.0, 0.0,
                                       0.0, 100.0, 50.0, 0.0,
                                       0.0,   0.0,  1.0, 0.0);
    cv::Point2f uv;
    QVERIFY(ReconstructionPostProcessor::projectWithP({0.f, 0.f, 2.f}, cam, uv));
    QVERIFY(std::abs(uv.x - 50.0f) < 1e-3f);
    QVERIFY(std::abs(uv.y - 50.0f) < 1e-3f);

    // w <= 0: behind (or on) the camera plane — refusing is what keeps a
    // negative depth from producing a mirrored pixel.
    QVERIFY(!ReconstructionPostProcessor::projectWithP({0.f, 0.f, -1.f}, cam, uv));

    // A missing matrix is refused instead of read out of bounds.
    CameraParams noMatrix;
    QVERIFY(!ReconstructionPostProcessor::projectWithP({0.f, 0.f, 1.f}, noMatrix, uv));
  }

  // ─── Post-processing: densify / Poisson meshing / texturing ───────────────

  // The densifier interpolates along the edges to the KNN neighbours with a
  // fixed RNG seed: the same cloud must always densify to the same result,
  // the originals must survive and the new points must stay on the original
  // segment (plus the 0.5 mm noise).
  void densifyGrowsTheCloudDeterministically() {
    std::vector<cv::Point3f> pts;
    std::vector<cv::Vec3b> cols;
    for (int i = 0; i < 20; ++i) {
      pts.emplace_back(float(i) * 0.01f, 0.f, 0.f);
      cols.emplace_back(10, 20, 30);
    }
    const std::vector<cv::Point3f> original = pts;

    ReconstructionPostProcessor::densifyPointCloudMLS(pts, cols, 200);

    // The cloud grew, the colours stayed in sync, and the target was reached.
    QVERIFY(pts.size() > original.size());
    QCOMPARE(pts.size(), 200u);
    QVERIFY(pts.size() <= 200u);
    QCOMPARE(cols.size(), pts.size());
    // The originals survive, in their original order (the densifier interleaves
    // the interpolated points between them, it does not append them).
    size_t cursor = 0;
    for (const auto &o : original) {
      bool found = false;
      for (; cursor < pts.size(); ++cursor) {
        if (pts[cursor].x == o.x && pts[cursor].y == o.y && pts[cursor].z == o.z) {
          found = true;
          ++cursor;
          break;
        }
      }
      QVERIFY2(found, "an original point was dropped by the densifier");
    }
    // The interpolated points stay inside the original range (+ the noise).
    for (const auto &p : pts) {
      QVERIFY(p.x >= -0.005f);
      QVERIFY(p.x <= 0.19f + 0.005f);
    }

    // Deterministic (fixed seed): the same input gives the same output.
    std::vector<cv::Point3f> again = original;
    std::vector<cv::Vec3b> againCols(again.size(), cv::Vec3b(10, 20, 30));
    ReconstructionPostProcessor::densifyPointCloudMLS(again, againCols, 200);
    QCOMPARE(again.size(), pts.size());
    for (size_t i = 0; i < pts.size(); ++i) QCOMPARE(again[i].x, pts[i].x);
  }

  void densifyOnAnEmptyCloudIsANoop() {
    std::vector<cv::Point3f> pts;
    std::vector<cv::Vec3b> cols;
    ReconstructionPostProcessor::densifyPointCloudMLS(pts, cols, 100);
    QVERIFY(pts.empty());
    QVERIFY(cols.empty());
  }

  // The Poisson stage trims the "wings" of the reconstructed surface back to
  // the bounding box of the input cloud. Trimming replaces the vertex list,
  // so the faces have to be remapped together with it — a face that lost a
  // vertex has to be dropped. Replacing the vertices alone leaves faces
  // pointing at vertices that no longer exist: a corrupt mesh.
  void poissonMeshFacesNeverReferenceARemovedVertex() {
    // An arc of a large circle: Poisson closes the arc into a big spherical
    // surface, and the trim cuts away everything that sticks out of the arc's
    // (small) bounding box — so the trim really removes vertices here.
    std::vector<cv::Point3f> pts;
    std::vector<cv::Vec3b> cols;
    for (int i = 0; i <= 40; ++i) {
      const float angle = float(i) * 0.05f; // ~2.3 rad of arc
      pts.emplace_back(std::cos(angle), std::sin(angle), 0.f);
      cols.emplace_back(200, 100, 50);
    }

    const pcl::PolygonMesh mesh =
        ReconstructionPostProcessor::poissonMeshing(pts, cols, 5);

    pcl::PointCloud<pcl::PointXYZRGB> cloud;
    pcl::fromPCLPointCloud2(mesh.cloud, cloud);
    QVERIFY2(!cloud.empty(), "Poisson produced no vertices at all");

    for (const auto &poly : mesh.polygons) {
      for (const auto index : poly.vertices) {
        QVERIFY2(index >= 0, "a face references a negative vertex index");
        QVERIFY2(static_cast<size_t>(index) < cloud.size(),
                 "a face references a vertex that the trim removed");
      }
    }
  }

  // The texturing takes the colour of the best view: the sample closest to
  // the image centre. A point that projects outside the image, and a black
  // background pixel (the templeRing), are not hits — the colour of such a
  // point must stay as it was.
  void texturingUsesTheBestViewAndKeepsUnprojectablePoints() {
    cv::Mat image(32, 32, CV_8UC3, cv::Scalar(40, 80, 120));
    cv::Mat black(32, 32, CV_8UC3, cv::Scalar(0, 0, 0));
    std::vector<cv::Mat> images{image, black};
    std::vector<CameraParams> cams(2);
    for (auto &cam : cams)
      // u = 16·x, v = 16·y, w = 1 → the point (1,1,0) lands on (16,16).
      cam.P = (cv::Mat_<double>(3, 4) << 16, 0, 0, 0, 0, 16, 0, 0, 0, 0, 0, 1);

    std::vector<cv::Point3f> pts{{1.f, 1.f, 0.f}, {10.f, 10.f, 0.f}};
    std::vector<cv::Vec3b> cols{{0, 0, 0}, {7, 8, 9}};

    ReconstructionPostProcessor::textureFromImages(pts, cols, images, cams);

    // The point that projects into the image took its colour …
    QCOMPARE(int(cols[0][0]), 40);
    QCOMPARE(int(cols[0][1]), 80);
    QCOMPARE(int(cols[0][2]), 120);
    // … the one that projects outside kept the colour it had.
    QCOMPARE(int(cols[1][0]), 7);
    QCOMPARE(int(cols[1][1]), 8);
    QCOMPARE(int(cols[1][2]), 9);
  }

  void texturingSkipsTheBlackBackground() {
    cv::Mat black(32, 32, CV_8UC3, cv::Scalar(0, 0, 0));
    std::vector<cv::Mat> images{black};
    std::vector<CameraParams> cams(1);
    cams[0].P = (cv::Mat_<double>(3, 4) << 16, 0, 0, 0, 0, 16, 0, 0, 0, 0, 0, 1);

    std::vector<cv::Point3f> pts{{1.f, 1.f, 0.f}};
    std::vector<cv::Vec3b> cols{{5, 6, 7}};

    ReconstructionPostProcessor::textureFromImages(pts, cols, images, cams);

    // A black pixel is the templeRing background, not a surface: it must not
    // overwrite the colour of the point.
    QCOMPARE(int(cols[0][0]), 5);
    QCOMPARE(int(cols[0][1]), 6);
    QCOMPARE(int(cols[0][2]), 7);
  }

  void texturingTheMeshColoursTheVerticesAndWritesTheFile() {
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    const QString output = dir.filePath("textured.ply");

    pcl::PointCloud<pcl::PointXYZRGB> cloud;
    cloud.resize(2);
    cloud[0].x = 1.f; cloud[0].y = 1.f; cloud[0].z = 0.f;
    cloud[1].x = 1.f; cloud[1].y = 1.f; cloud[1].z = 0.f;
    pcl::PolygonMesh mesh;
    pcl::toPCLPointCloud2(cloud, mesh.cloud);

    cv::Mat image(32, 32, CV_8UC3, cv::Scalar(40, 80, 120));
    std::vector<cv::Mat> images{image};
    std::vector<CameraParams> cams(1);
    cams[0].P = (cv::Mat_<double>(3, 4) << 16, 0, 0, 0, 0, 16, 0, 0, 0, 0, 0, 1);

    // An empty output path must not write anything …
    ReconstructionPostProcessor::textureMeshFromImages(mesh, images, cams, QString());
    QVERIFY(!QFileInfo::exists(output));

    // … and with a path the mesh is written.
    ReconstructionPostProcessor::textureMeshFromImages(mesh, images, cams, output);
    pcl::PointCloud<pcl::PointXYZRGB> after;
    pcl::fromPCLPointCloud2(mesh.cloud, after);
    QCOMPARE(int(after[0].b), 40);
    QCOMPARE(int(after[0].g), 80);
    QCOMPARE(int(after[0].r), 120);
    QVERIFY(QFileInfo::exists(output));
    QVERIFY(QFileInfo(output).size() > 0);
  }

  // ─── ReconstructionService: configuration + the synchronous core ─────────
  void serviceConfigureMapsDensityAndAutoClean() {
    ReconstructionService service;
    // Called through the interface, which is how every plugin uses it — the
    // concrete class is not part of the plugin-facing ABI.
    IReconstructionService &iface = service;
    // Every density level must be accepted (each maps to its own leaf size),
    // an out-of-range level falls back to the documented default, and the
    // call never starts a run or publishes an image list.
    for (int level = 0; level <= 3; ++level)
      iface.configure(level, 2000, true);
    iface.configure(42, 2000, true);
    iface.configure(1, 2000, false); // "auto clean off" → SOR/ROR disabled
    QVERIFY(!service.isRunning());
    QVERIFY(service.getImageList().isEmpty());
  }

  void serviceRunPipelineWithoutImagesFailsCleanly() {
    ReconstructionService service;
    // runPipeline() is the synchronous core the worker calls. With no images
    // the pipeline fails, and the failure comes back as a value — the try/catch
    // around it exists so an OpenCV/PCL exception can never reach
    // std::terminate on the worker thread.
    QVERIFY(!service.runPipeline());
    QVERIFY(!service.isRunning());
    QVERIFY(!service.hasResult());
    QCOMPARE(service.pointCount(), size_t(0));
    // No result yet: the snapshot query must return empty (early-out path),
    // and post-processing an empty cloud must be a no-op, not a crash.
    QVERIFY(!service.pointCloudSnapshot());
    service.processPointCloud();
  }

  // The ABI-neutral snapshot the service publishes: the points and the
  // colours of ONE generation, with the BGR→RGB swap applied. It lives next
  // to the data it converts, so it can be measured here (cheap) instead of
  // only after a completed reconstruction.
  void theSnapshotCarriesThePointsAndTheSwappedColours() {
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    SyntheticScene scene;
    QVERIFY(writeSyntheticScene(dir.path(), scene));

    ReconstructionPipeline pipeline;
    std::vector<QString> paths;
    for (const QString &f : scene.imageFiles)
      paths.emplace_back(f);
    pipeline.setImages(paths);
    QVERIFY(pipeline.loadCameraParams(scene.paramsFile));

    // Cancel as soon as the first cloud appears: the SfM chain is verified
    // here, the heavy post-processing is not.
    QVERIFY(!pipeline.reconstruct(
        [&pipeline] { return !pipeline.getPointCloud().empty(); }));

    const auto snapshot = pipeline.buildSnapshot();
    QVERIFY(snapshot);
    QVERIFY(!snapshot->points.empty());
    // Points and colours come from the same generation.
    QCOMPARE(snapshot->points.size(), pipeline.getPointCloud().size());
    QCOMPARE(snapshot->colors.size(), pipeline.getPointColors().size());

    // The colour channels are swapped: OpenCV is BGR, PointColor is {r,g,b}.
    // A rendered cloud with red and blue inverted is a visible defect, so
    // the swap is pinned here.
    for (size_t i = 0; i < snapshot->colors.size(); ++i) {
        const cv::Vec3b& src = pipeline.getPointColors()[i];
        QCOMPARE(snapshot->colors[i].r, static_cast<unsigned char>(src[2]));
        QCOMPARE(snapshot->colors[i].g, static_cast<unsigned char>(src[1]));
        QCOMPARE(snapshot->colors[i].b, static_cast<unsigned char>(src[0]));
    }
  }

  // ─── Triangulator: the output stays index-aligned with the input ──────────

  void triangulationKeepsTheIndexAlignmentForDegeneratePoints() {
    // Hai camera cùng hướng, cách nhau 1 m. Một điểm ở vô cực theo hướng nhìn
    // chiếu vào CÙNG một pixel ở cả hai ảnh: w của nó bằng 0 nên không
    // triangulate được. Trước đây điểm đó bị BỎ khỏi output, làm mọi điểm phía
    // sau lệch sang match khác trong IncrementalSfM.
    const cv::Mat P0 = (cv::Mat_<double>(3, 4) << 1, 0, 0, 0,
                                                  0, 1, 0, 0,
                                                  0, 0, 1, 0);
    const cv::Mat P1 = (cv::Mat_<double>(3, 4) << 1, 0, 0, -1,
                                                  0, 1, 0,  0,
                                                  0, 0, 1,  0);
    // Cặp đầu triangulate ra (0.5, 0.5, 5); cặp sau là điểm vô cực.
    const std::vector<cv::Point2f> pts0{{0.1f, 0.1f}, {0.0f, 0.0f}};
    const std::vector<cv::Point2f> pts1{{-0.1f, 0.1f}, {0.0f, 0.0f}};

    std::vector<cv::Point3f> out;
    Triangulator::triangulate(P0, P1, pts0, pts1, out);

    // Căn chỉnh: một output cho mỗi input, đúng thứ tự.
    QCOMPARE(out.size(), pts0.size());
    QVERIFY(std::abs(out[0].x - 0.5f) < 1e-3f);
    QVERIFY(std::abs(out[0].y - 0.5f) < 1e-3f);
    QVERIFY(std::abs(out[0].z - 5.0f) < 1e-3f);
    // Điểm degenerate là sentinel NaN, không phải điểm của cặp kế tiếp.
    QVERIFY(std::isnan(out[1].x));
    QVERIFY(std::isnan(out[1].y));
    QVERIFY(std::isnan(out[1].z));
  }

  // Dtor của service khi pipeline đang chạy: hủy cooperative + join + xóa
  // worker/thread theo ownership tường minh (không auto-delete) — không treo,
  // không double-free khi thread vừa finished đúng lúc dtor.
  void serviceDestructorStopsRunningReconstruction() {
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    SyntheticScene scene;
    QVERIFY(writeSyntheticScene(dir.path(), scene));

    QStringList paths;
    for (const QString &f : scene.imageFiles)
      paths << f;

    auto service = std::make_unique<ReconstructionService>();
    service->setImages(paths);
    QVERIFY(service->loadCameraParams(scene.paramsFile));
    QVERIFY(service->reconstruct());
    QVERIFY(service->isRunning());

    QElapsedTimer timer;
    timer.start();
    service.reset(); // dtor: cancel → quit → wait → delete worker/thread
    QVERIFY2(timer.elapsed() < 5000,
             qPrintable(QStringLiteral("dtor mất %1 ms — hủy không cooperative")
                            .arg(timer.elapsed())));
  }
};

QTEST_GUILESS_MAIN(TestReconstructionPipeline)
#include "TestReconstructionPipeline.moc"