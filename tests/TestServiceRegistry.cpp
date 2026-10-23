#include <QTest>
#include <QRegularExpression>
#include "ServiceRegistry.h"

class DummyService1 {
public:
    static constexpr const char *ServiceKey = "test.DummyService1";
    virtual ~DummyService1() = default;
    virtual int getValue() = 0;
};

class DummyServiceImpl1 : public DummyService1 {
public:
    int getValue() override { return 42; }
};

class DummyService2 {
public:
    static constexpr const char *ServiceKey = "test.DummyService2";
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

    void testHasAndUnregister() {
        ServiceRegistry registry;
        DummyServiceImpl1 impl1;
        QVERIFY(!registry.has<DummyService1>());
        registry.registerService<DummyService1>(&impl1);
        QVERIFY(registry.has<DummyService1>());
        registry.unregister<DummyService1>();
        QVERIFY(!registry.has<DummyService1>());
        QVERIFY(registry.get<DummyService1>() == nullptr);
    }

    void testClear() {
        ServiceRegistry registry;
        DummyServiceImpl1 impl1;
        DummyServiceImpl2 impl2;
        registry.registerService<DummyService1>(&impl1);
        registry.registerService<DummyService2>(&impl2);
        registry.clear();
        QVERIFY(registry.get<DummyService1>() == nullptr);
        QVERIFY(registry.get<DummyService2>() == nullptr);
    }

    void testNullRegistrationIsRejected() {
        ServiceRegistry registry;
        QTest::ignoreMessage(QtWarningMsg, "ServiceRegistry: refusing to register null service \"test.DummyService1\"");
        registry.registerService<DummyService1>(nullptr);
        QVERIFY(!registry.has<DummyService1>());
    }

    void testDuplicateRegistrationIsRefused() {
        ServiceRegistry registry;
        DummyServiceImpl1 first;
        DummyServiceImpl1 second;
        registry.registerService<DummyService1>(&first);
        QTest::ignoreMessage(QtWarningMsg,
            QRegularExpression("ServiceRegistry: duplicate registration of \"test\\.DummyService1\".*"));
        registry.registerService<DummyService1>(&second);
        // The FIRST registration wins: a later one (a plugin registering a
        // service the composition root already installed) must not silently
        // take the service over.
        QCOMPARE(registry.get<DummyService1>(), static_cast<DummyService1*>(&first));
    }

    void testGetRequiredReturnsRegistered() {
        ServiceRegistry registry;
        DummyServiceImpl1 impl1;
        registry.registerService<DummyService1>(&impl1);
        QCOMPARE(registry.getRequired<DummyService1>(), static_cast<DummyService1*>(&impl1));
    }

    // The deterministic key contract: same interface → same key, independent
    // of RTTI/mangling (this is what makes lookups safe across DLL borders).
    void testKeysAreStable() {
        QCOMPARE(QString::fromLatin1(DummyService1::ServiceKey), QStringLiteral("test.DummyService1"));
        QCOMPARE(QString::fromLatin1(DummyService2::ServiceKey), QStringLiteral("test.DummyService2"));
    }
};

QTEST_MAIN(TestServiceRegistry)
#include "TestServiceRegistry.moc"
