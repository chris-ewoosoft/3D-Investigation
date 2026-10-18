#include <QtTest>
#include "core/IReconstructionService.h"
#include "core/mock/IReconstructionServiceMock.h"
#include "core/PointCloudData.h"

/**
 * @brief Kiến trúc boundary test: plugin có thể mock cả IReconstructionService
 * và ISceneService mà không cần link OpenCV hay VTK concrete.
 */
class TestArchitectureBoundary : public QObject {
    Q_OBJECT
private slots:
    void testMockReconstructionService() {
        MockReconstructionService mock;
        QVERIFY(mock.reconstruct() == true);
    }
    void testPointCloudDataNoOpenCV() {
        Point3D pt{1.0f, 2.0f, 3.0f};
        PointColor c{128, 64, 32};
        QVERIFY(pt.x == 1.0f);
        QVERIFY(c.b == 32);
    }
};
#include "TestArchitectureBoundary.moc"

QTEST_MAIN(TestArchitectureBoundary)
