#ifndef VIEWERNAVIGATORUI_H
#define VIEWERNAVIGATORUI_H

#include <QObject>

class QWidget;
class QPushButton;
class IAppContext;

class ViewerNavigatorUI : public QObject {
    Q_OBJECT
public:
    explicit ViewerNavigatorUI(IAppContext* ctx, QObject* parent = nullptr);
    
    QPushButton* btnPrev() const { return m_btnPrev; }
    QPushButton* btnNext() const { return m_btnNext; }
    QPushButton* btnAutoPrev() const { return m_btnAutoPrev; }
    QPushButton* btnAutoNext() const { return m_btnAutoNext; }

private:
    IAppContext* m_ctx;
    QPushButton* m_btnPrev;
    QPushButton* m_btnNext;
    QPushButton* m_btnAutoPrev;
    QPushButton* m_btnAutoNext;
};

#endif // VIEWERNAVIGATORUI_H
