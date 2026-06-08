#include <QTest>
#include <QSignalSpy>
#include "SignalBus.h"

class TestSignalBus : public QObject {
    Q_OBJECT

private slots:
    void testReconstructionSignals() {
        SignalBus bus;
        QSignalSpy spyStarted(&bus, &SignalBus::reconstructionStarted);
        QSignalSpy spyFinished(&bus, &SignalBus::reconstructionFinished);

        emit bus.reconstructionStarted();
        QCOMPARE(spyStarted.count(), 1);

        emit bus.reconstructionFinished(true, 100);
        QCOMPARE(spyFinished.count(), 1);
        QList<QVariant> arguments = spyFinished.takeFirst();
        QCOMPARE(arguments.at(0).toBool(), true);
        QCOMPARE(arguments.at(1).toInt(), 100);
    }

    void testAISignals() {
        SignalBus bus;
        QSignalSpy spyStarted(&bus, &SignalBus::aiInferenceStarted);
        QSignalSpy spyFinished(&bus, &SignalBus::aiInferenceFinished);

        emit bus.aiInferenceStarted("Detection");
        QCOMPARE(spyStarted.count(), 1);

        emit bus.aiInferenceFinished("Detection");
        QCOMPARE(spyFinished.count(), 1);
        QList<QVariant> args = spyFinished.takeFirst();
        QCOMPARE(args.at(0).toString(), QString("Detection"));
    }
};

QTEST_MAIN(TestSignalBus)
#include "TestSignalBus.moc"
