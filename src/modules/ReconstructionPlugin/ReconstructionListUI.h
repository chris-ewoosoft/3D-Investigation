#ifndef RECONSTRUCTION_LIST_UI_H
#define RECONSTRUCTION_LIST_UI_H

#include <QObject>

class IAppContext;
class QListWidget;
class QListWidgetItem;

class ReconstructionListUI : public QObject {
    Q_OBJECT
public:
    explicit ReconstructionListUI(IAppContext* ctx, QObject* parent = nullptr);
    ~ReconstructionListUI() override = default;

    void setupUI();
    QListWidget* listWidget() const { return m_imageList; }

signals:
    void imageRowChanged(int row);

private slots:
    void onListRowChanged(int row);

private:
    IAppContext* m_ctx = nullptr;
    QListWidget* m_imageList = nullptr;
};

#endif // RECONSTRUCTION_LIST_UI_H
