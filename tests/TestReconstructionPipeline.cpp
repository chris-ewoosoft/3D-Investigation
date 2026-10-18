#include "ReconstructionPipeline.h"
#include "ReconstructionPostProcessor.h"
#include "ReconstructionService.h"
#include "FeatureMatcher.h"
#include "SyntheticSceneFixture.h"
#include <QElapsedTimer>
#include <QSignalSpy>
#include <QTemporaryDir>
#include <QtTest>

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
    QCOMPARE(pipeline.getImages().size(), 1); // danh sách input vẫn ghi lại
    QVERIFY(!pipeline.reconstruct());
    QVERIFY(pipeline.errorString().contains("at least 2"));
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