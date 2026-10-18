#include "ShellService.h"
#include "SignalBus.h"
#include "ILanguageService.h"
#include "CustomProgressDialog.h"

#include <QMap>
#include <QSignalSpy>
#include <QtTest>

// Fake tối thiểu của ILanguageService — chỉ cần translate() cho fallback
// tra tab theo nhãn hiển thị.
class FakeLanguageService : public ILanguageService {
    Q_OBJECT
public:
    QString currentLanguage() const override { return QStringLiteral("vi"); }
    void setLanguage(const QString&) override {}
    QString translate(const QString& key) const override
    {
        return m_table.value(key, key);
    }
    QString translate(const char* key) const override
    {
        return translate(QString::fromUtf8(key));
    }
    QString displayName() const override { return QStringLiteral("Tiếng Việt"); }
    QString flagEmoji() const override { return QStringLiteral("VN"); }

    void setTranslation(const QString& key, const QString& value)
    {
        m_table.insert(key, value);
    }

private:
    QMap<QString, QString> m_table;
};

class TestShellService : public QObject {
    Q_OBJECT

private slots:
    void defaultsAreNull() {
        SignalBus bus;
        ShellService shell(&bus);
        QVERIFY(shell.menuBar() == nullptr);
        QVERIFY(shell.getProgressDialog() == nullptr);
        QVERIFY(shell.getTabPanel(QStringLiteral("tab.missing")) == nullptr);
    }

    void menuCacheReusesMenusAndRecoversAfterDeletion() {
        SignalBus bus;
        ShellService shell(&bus);
        QWidget parent;
        shell.setMenuParent(&parent);
        QMenuBar bar;
        shell.setMenuBar(&bar);
        QCOMPARE(shell.menuBar(), &bar);

        QMenu* first = shell.getMenu(QStringLiteral("menu.file"));
        QVERIFY(first != nullptr);
        QCOMPARE(first->parentWidget(), &parent);
        QCOMPARE(shell.getMenu(QStringLiteral("menu.file")), first);
        QVERIFY(shell.getMenu(QStringLiteral("menu.edit")) != first);

        // Menu bị hủy ngoài service → QPointer reset, lần get sau tạo lại
        // thay vì trả dangling pointer.
        delete first;
        QMenu* recreated = shell.getMenu(QStringLiteral("menu.file"));
        QVERIFY(recreated != nullptr);
        QVERIFY(recreated != first);
    }

    void tabPanelLookupByKeyAndTranslatedLabel() {
        SignalBus bus;
        FakeLanguageService language;
        language.setTranslation(QStringLiteral("tab.help"), QStringLiteral("Trợ giúp"));
        ShellService shell(&bus, &language);

        QWidget panel;
        shell.registerTabPanel(QStringLiteral("tab.help"), &panel);
        QCOMPARE(shell.getTabPanel(QStringLiteral("tab.help")), &panel);
        QCOMPARE(shell.getTabPanel(QStringLiteral("Trợ giúp")), &panel);
        QVERIFY(shell.getTabPanel(QStringLiteral("tab.unknown")) == nullptr);
    }

    void deletedPanelIsNotReturned() {
        SignalBus bus;
        ShellService shell(&bus);
        auto* panel = new QWidget;
        shell.registerTabPanel(QStringLiteral("tab.view"), panel);
        QCOMPARE(shell.getTabPanel(QStringLiteral("tab.view")), panel);

        delete panel;
        QVERIFY(shell.getTabPanel(QStringLiteral("tab.view")) == nullptr);
    }

    void updateMenuStatesEmitsStateChanged() {
        SignalBus bus;
        ShellService shell(&bus);
        QSignalSpy spy(&bus, &SignalBus::stateChanged);
        shell.updateMenuStates();
        QCOMPARE(spy.count(), 1);
    }

    void progressDialogRoundTrip() {
        SignalBus bus;
        FakeLanguageService language;
        ShellService shell(&bus, &language);
        QVERIFY(shell.getProgressDialog() == nullptr);

        CustomProgressDialog dialog(&language);
        shell.setProgressDialog(&dialog);
        QCOMPARE(shell.getProgressDialog(), &dialog);

        // Dialog bị hủy trước service → lookup trả nullptr.
        {
            CustomProgressDialog temporary(&language);
            shell.setProgressDialog(&temporary);
            QCOMPARE(shell.getProgressDialog(), &temporary);
        }
        QVERIFY(shell.getProgressDialog() == nullptr);
    }
};

QTEST_MAIN(TestShellService)
#include "TestShellService.moc"
