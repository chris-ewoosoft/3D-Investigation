#include "CameraParamsParser.h"

#include <QDir>
#include <QFile>
#include <QTemporaryDir>
#include <QTextStream>
#include <QtTest>

class TestCameraParamsParser : public QObject {
    Q_OBJECT

private slots:
    void parsesFormatAFile()
    {
        const QString path = QDir::temp().filePath("camera_params_format_a.txt");
        QFile file(path);
        QVERIFY(file.open(QIODevice::WriteOnly | QIODevice::Text));
        QTextStream out(&file);
        out << "1\n";
        out << "image001.jpg "
            << "1000 0 320 0 1000 240 0 0 1 "
            << "1 0 0 0 1 0 0 0 1 "
            << "1 2 3\n";
        file.close();

        std::vector<CameraParams> params;
        QVERIFY(CameraParamsParser::loadFromFile(path, params));
        QCOMPARE(params.size(), size_t(1));
        QCOMPARE(params[0].imageName, QString("image001.jpg"));
        QCOMPARE(params[0].K.at<double>(0, 0), 1000.0);
        QCOMPARE(params[0].t.at<double>(2, 0), 3.0);
        QCOMPARE(params[0].P.rows, 3);
        QCOMPARE(params[0].P.cols, 4);

        QFile::remove(path);
    }

    void parsesFormatBFileWithoutHeader()
    {
        QTemporaryDir dir;
        QVERIFY(dir.isValid());
        const QString path = dir.filePath("camera_params_format_b.txt");
        QFile file(path);
        QVERIFY(file.open(QIODevice::WriteOnly | QIODevice::Text));
        QTextStream out(&file);
        out << "view0.png 1000 0 320 0 1000 240 0 0 1 1 0 0 0 1 0 0 0 1 1 2 3\n";
        out << "view1.png 1000 0 320 0 1000 240 0 0 1 1 0 0 0 1 0 0 0 1 4 5 6\n";
        file.close();

        std::vector<CameraParams> params;
        QVERIFY(CameraParamsParser::loadFromFile(path, params));
        QCOMPARE(params.size(), size_t(2));
        QCOMPARE(params[0].imageName, QString("view0.png"));
        QCOMPARE(params[1].imageName, QString("view1.png"));
        // P = K [R|t] → cột cuối = K*t (pixel).
        QCOMPARE(params[0].P.at<double>(0, 3), 1960.0); // 1000*1 + 320*3
        QCOMPARE(params[1].P.at<double>(1, 3), 6440.0); // 1000*5 + 240*6
    }

    void skipsMalformedLinesButKeepsValidOnes()
    {
        QTemporaryDir dir;
        QVERIFY(dir.isValid());
        const QString path = dir.filePath("camera_params_malformed.txt");
        QFile file(path);
        QVERIFY(file.open(QIODevice::WriteOnly | QIODevice::Text));
        QTextStream out(&file);
        out << "2\n";
        out << "dòng rác không có số\n";
        out << "image001.jpg 1000 0 320 0 1000 240 0 0 1 1 0 0 0 1 0 0 0 1 1 2 3\n";
        file.close();

        std::vector<CameraParams> params;
        QVERIFY(CameraParamsParser::loadFromFile(path, params));
        QCOMPARE(params.size(), size_t(1));
        QCOMPARE(params[0].imageName, QString("image001.jpg"));
    }

    void missingFileFailsAndLeavesOutputEmpty()
    {
        std::vector<CameraParams> params;
        QVERIFY(!CameraParamsParser::loadFromFile(
            QStringLiteral("F:/nonexistent/camera_params.txt"), params));
        QVERIFY(params.empty());
    }
};

QTEST_GUILESS_MAIN(TestCameraParamsParser)
#include "TestCameraParamsParser.moc"
