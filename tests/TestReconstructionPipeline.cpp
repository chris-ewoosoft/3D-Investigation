#include <QtTest>
#include "ReconstructionPipeline.h"

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
};

QTEST_MAIN(TestReconstructionPipeline)
#include "TestReconstructionPipeline.moc"
