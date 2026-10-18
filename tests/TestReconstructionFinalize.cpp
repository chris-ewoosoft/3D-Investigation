#include "ReconstructionPipeline.h"
#include "SyntheticSceneFixture.h"

#include <QElapsedTimer>
#include <QFile>
#include <QFileInfo>
#include <QTemporaryDir>
#include <QtTest>

#include <pcl/io/ply_io.h>

using namespace synth;

// ─── Integration test cho giai đoạn HẬU KỲ nặng ─────────────────────────────
//
// Chạy trọn reconstruct() trên fixture ảnh tổng hợp: MLS densify 1M điểm →
// texture point cloud → Poisson depth 10 → texture mesh → ghi 3 artifact.
// Chi phí ~100s (Release) nên tách khỏi TestReconstructionPipeline (vòng lặp
// nhanh) và gắn label "slow": dev có thể chạy `ctest -LE slow` khi cần nhanh,
// CI vẫn chạy mặc định.
class TestReconstructionFinalize : public QObject {
  Q_OBJECT

private slots:
  void fullReconstructWritesTexturedCacheAndMesh() {
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    SyntheticScene scene;
    QVERIFY(writeSyntheticScene(dir.path(), scene));

    std::vector<QString> paths;
    for (const QString &f : scene.imageFiles)
      paths.emplace_back(f);

    ReconstructionPipeline pipeline;
    pipeline.setImages(paths);
    QVERIFY(pipeline.loadCameraParams(scene.paramsFile));

    QElapsedTimer timer;
    timer.start();
    const bool ok = pipeline.reconstruct();
    qInfo() << "full synthetic reconstruct took" << timer.elapsed() << "ms";

    QVERIFY2(ok, qPrintable(pipeline.errorString()));
    QVERIFY(pipeline.getPointCloud().size() > 1000); // đã densify

    // Hậu kỳ phải tạo đủ 3 artifact trên đĩa.
    const QString cachePath = dir.path() + "/recon_cache_quality_v3.ply";
    const QString meshCachePath = dir.path() + "/recon_mesh_cache_v3.ply";
    const QString texturedMeshPath = dir.path() + "/temple_final_textured_2M.ply";
    QVERIFY(QFile::exists(cachePath));
    QVERIFY(QFile::exists(meshCachePath));
    QVERIFY(QFile::exists(texturedMeshPath));
    QVERIFY(QFileInfo(cachePath).size() > 0);
    QVERIFY(QFileInfo(meshCachePath).size() > 0);
    QVERIFY(QFileInfo(texturedMeshPath).size() > 0);

    // Cache PLY phải đọc lại được và chứa point cloud thật (không phải file rỗng).
    pcl::PointCloud<pcl::PointXYZRGB> reloaded;
    QCOMPARE(pcl::io::loadPLYFile(cachePath.toStdString(), reloaded), 0);
    QVERIFY(reloaded.size() > 1000);
  }
};

QTEST_GUILESS_MAIN(TestReconstructionFinalize)
#include "TestReconstructionFinalize.moc"
