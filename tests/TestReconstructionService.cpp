#include <QtTest>

#include <QElapsedTimer>
#include <QTemporaryDir>
#include <QThread>

#include "ReconstructionService.h"
#include "SyntheticSceneFixture.h"

#include <opencv2/core.hpp>

/**
 * @brief The SERVICE half of the reconstruction contract.
 *
 * The pipeline itself is covered by TestReconstructionPipeline (fast) and
 * TestReconstructionFinalize (slow). This executable covers the *service*:
 * the boundary the UI actually talks to.
 *
 * The two properties that matter here:
 *
 * 1. **The UI never waits for the worker.** The worker holds the pipeline
 *    lock for the WHOLE reconstruction, so a query that takes that lock
 *    freezes the GUI thread until the run ends, and a mutator that waits for
 *    it does the same. The service therefore publishes the result and
 *    refuses a change instead of blocking.
 *
 * 2. **The published result is complete.** After a successful run the
 *    snapshot, its colours and the point count all come from the SAME run.
 *
 * It is a separate executable on purpose: the second property needs a
 * completed reconstruction, and the heavy post-processing (MLS densify to
 * 1M points + Poisson depth 10) leaves several gigabytes behind. Running it
 * in the same process as the other reconstruction tests would push the
 * second run over the per-function timeout — and a test that only passes on
 * a fast machine is not a test.
 */
class TestReconstructionService : public QObject {
    Q_OBJECT

private slots:
    void serviceImageListStaysReadableDuringReconstruction();
    void serviceQueriesStayNonBlockingDuringReconstruction();
    void serviceMutatorsDoNotWaitForTheRunningPipeline();
    void servicePublishesNothingWhileTheRunIsInFlight();
    void theServicePublishesTheResultOfTheCompletedRun();

private:
    /// The coverage exporter cannot host the heavy reconstruction: its
    /// instrumentation multiplies every allocation, and the MLS densify to
    /// 1M points plus the Poisson meshing exceed the exporter's 30-minute
    /// per-test budget (the run times out). The coverage gate therefore sets
    /// this variable and the completed-run case skips — the published-state
    /// contract is still measured by the other cases here and by
    /// TestReconstructionPipeline (which owns the snapshot conversion), and
    /// the completed run is proven by the normal CTest run, where no exporter
    /// is involved.
    static bool heavyRunDisabledByTheExporter()
    {
        return !qEnvironmentVariableIsEmpty("RECONSTRUCTION_NO_HEAVY_RUN");
    }
};

namespace {

/// The synthetic scene the reconstruction runs on.
struct SceneFixture {
    QStringList paths;
    QString     paramsFile;
};

SceneFixture writeScene(const QString& dirPath)
{
    SceneFixture fixture;
    synth::SyntheticScene scene;
    if (!synth::writeSyntheticScene(dirPath, scene))
        return fixture;
    for (const QString& f : scene.imageFiles)
        fixture.paths << f;
    fixture.paramsFile = scene.paramsFile;
    return fixture;
}

} // namespace

  // The published image list must stay readable while the worker holds the
  // pipeline lock for the whole reconstruction. ReconstructionPlugin calls
  // getImageList() right after starting a run; if the query took the pipeline
  // lock it would freeze the UI for the entire reconstruction.
void TestReconstructionService::serviceImageListStaysReadableDuringReconstruction()
{
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    const SceneFixture scene = writeScene(dir.path());
    QCOMPARE(scene.paths.size(), 3);

    ReconstructionService service;
    service.setImages(scene.paths);
    QVERIFY(service.loadCameraParams(scene.paramsFile));
    QVERIFY(service.reconstruct());
    QVERIFY(service.isRunning());

    // Give the worker time to take the pipeline lock (held for the whole run).
    // Before the published lock was split out, the next call blocked until the
    // reconstruction ended.
    QThread::msleep(100);

    QElapsedTimer timer;
    timer.start();
    const QStringList during = service.getImageList();
    const qint64 elapsed = timer.elapsed();

    QVERIFY2(elapsed < 500,
             qPrintable(QStringLiteral("getImageList() blocked %1 ms while "
                                       "reconstructing")
                            .arg(elapsed)));
    QCOMPARE(during.size(), scene.paths.size());
    // The query returned while the run is still in flight — it did not wait
    // for the worker to finish.
    QVERIFY(service.isRunning());

    service.stopReconstruction();
}

void TestReconstructionService::serviceQueriesStayNonBlockingDuringReconstruction()
{
    // EVERY UI-facing query must answer while the worker holds the pipeline
    // lock for the whole run — not just getImageList(). The others used to
    // take the pipeline lock, so a UI that asked for the snapshot or the
    // point count during a reconstruction froze until the run ended.
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    const SceneFixture scene = writeScene(dir.path());
    QCOMPARE(scene.paths.size(), 3);

    ReconstructionService service;
    service.setImages(scene.paths);
    QVERIFY(service.loadCameraParams(scene.paramsFile));
    QVERIFY(service.reconstruct());
    QVERIFY(service.isRunning());

    // Give the worker time to take the pipeline lock (held for the whole run).
    QThread::msleep(100);
    QVERIFY(service.isRunning());

    QElapsedTimer timer;
    timer.start();
    const QStringList images = service.getImageList();
    const auto snapshot = service.pointCloudSnapshot();
    const size_t points = service.pointCount();
    const bool hasResult = service.hasResult();
    const qint64 elapsed = timer.elapsed();

    QVERIFY2(elapsed < 500,
             qPrintable(QStringLiteral("the UI queries blocked %1 ms while "
                                       "reconstructing")
                            .arg(elapsed)));
    QCOMPARE(images.size(), scene.paths.size());
    // No result yet: the run is still in flight, and the published state
    // says so instead of the UI having to ask the pipeline.
    QVERIFY(!snapshot);
    QCOMPARE(points, size_t(0));
    QVERIFY(!hasResult);
    QVERIFY(service.isRunning());

    service.stopReconstruction();
}

void TestReconstructionService::serviceMutatorsDoNotWaitForTheRunningPipeline()
{
    // The mutators must REFUSE the change while a run is in flight, not wait
    // for the run to release the pipeline lock. Waiting is what froze the UI
    // when the user loaded new images or re-applied the settings mid-run.
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    const SceneFixture scene = writeScene(dir.path());
    QCOMPARE(scene.paths.size(), 3);

    ReconstructionService service;
    service.setImages(scene.paths);
    QVERIFY(service.loadCameraParams(scene.paramsFile));
    QVERIFY(service.reconstruct());
    QVERIFY(service.isRunning());

    QThread::msleep(100);
    QVERIFY(service.isRunning());

    QElapsedTimer timer;
    timer.start();
    service.setImages(QStringList{QStringLiteral("other.png")});
    service.configure(2, 500, true);
    service.processPointCloud();
    const qint64 elapsed = timer.elapsed();

    QVERIFY2(elapsed < 500,
             qPrintable(QStringLiteral("a mutator blocked %1 ms while "
                                       "reconstructing")
                            .arg(elapsed)));
    // The change was refused, not applied behind the worker's back.
    QCOMPARE(service.getImageList().size(), scene.paths.size());
    QVERIFY(service.isRunning());

    service.stopReconstruction();
}

void TestReconstructionService::servicePublishesNothingWhileTheRunIsInFlight()
{
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    const SceneFixture scene = writeScene(dir.path());
    QCOMPARE(scene.paths.size(), 3);

    ReconstructionService service;
    service.setImages(scene.paths);
    QVERIFY(service.loadCameraParams(scene.paramsFile));
    QVERIFY(service.reconstruct());
    QVERIFY(service.isRunning());

    QThread::msleep(100);
    QVERIFY(service.isRunning());

    // The previous result is no longer current: the published state says
    // "no result" instead of the UI having to ask the pipeline.
    QVERIFY(!service.pointCloudSnapshot());
    QCOMPARE(service.pointCount(), size_t(0));
    QVERIFY(!service.hasResult());

    service.stopReconstruction();
}

void TestReconstructionService::theServicePublishesTheResultOfTheCompletedRun()
{
    if (heavyRunDisabledByTheExporter())
        QSKIP("the coverage exporter cannot host the heavy reconstruction");

    // The published result must be complete: the snapshot, its colours and
    // the point count all come from the SAME run, and the UI reads them
    // without ever touching the pipeline.
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    const SceneFixture scene = writeScene(dir.path());
    QCOMPARE(scene.paths.size(), 3);

    ReconstructionService service;
    service.setImages(scene.paths);
    QVERIFY(service.loadCameraParams(scene.paramsFile));
    QVERIFY(service.reconstruct());

    // Wait for the run to finish. The service clears its "running" flag in
    // the queued teardown, so the event loop MUST run here — a plain sleep
    // would wait for a flag that only the event loop can clear.
    QElapsedTimer timer;
    timer.start();
    while (service.isRunning() && timer.elapsed() < 600000) {
        QCoreApplication::processEvents(QEventLoop::AllEvents, 50);
        QThread::msleep(20);
    }
    QVERIFY2(!service.isRunning(), "the reconstruction did not finish in time");

    const auto snapshot = service.pointCloudSnapshot();
    QVERIFY(snapshot);
    QVERIFY2(!snapshot->points.empty(), "the published snapshot is empty");
    QCOMPARE(snapshot->points.size(), snapshot->colors.size());
    // The point count is published WITH the snapshot — the UI never has to
    // ask the pipeline for it.
    QCOMPARE(service.pointCount(), snapshot->points.size());
    QVERIFY(service.hasResult());
}

QTEST_GUILESS_MAIN(TestReconstructionService)
#include "TestReconstructionService.moc"
