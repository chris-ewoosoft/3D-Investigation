#include <QTest>
#include "ServiceRegistry.h"

class DummyService1 {
public:
    virtual ~DummyService1() = default;
    virtual int getValue() = 0;
};

class DummyServiceImpl1 : public DummyService1 {
public:
    int getValue() override { return 42; }
};

class DummyService2 {
public:
    virtual ~DummyService2() = default;
    virtual bool isReady() = 0;
};

class DummyServiceImpl2 : public DummyService2 {
public:
    bool isReady() override { return true; }
};

class TestServiceRegistry : public QObject {
    Q_OBJECT

private slots:
    void testRegisterAndGet() {
        ServiceRegistry registry;
        DummyServiceImpl1 impl1;
        DummyServiceImpl2 impl2;

        registry.registerService<DummyService1>(&impl1);
        registry.registerService<DummyService2>(&impl2);

        DummyService1* svc1 = registry.get<DummyService1>();
        QVERIFY(svc1 != nullptr);
        QCOMPARE(svc1->getValue(), 42);

        DummyService2* svc2 = registry.get<DummyService2>();
        QVERIFY(svc2 != nullptr);
        QVERIFY(svc2->isReady());
    }

    void testGetUnregistered() {
        ServiceRegistry registry;
        DummyService1* svc1 = registry.get<DummyService1>();
        QVERIFY(svc1 == nullptr);
    }
};

QTEST_MAIN(TestServiceRegistry)
#include "TestServiceRegistry.moc"
