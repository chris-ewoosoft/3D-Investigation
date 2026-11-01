#ifndef MODERN_DIALOG_H
#define MODERN_DIALOG_H

#include <QDialog>
#include <QLabel>
#include <QPushButton>
#include <QHBoxLayout>
#include <QVBoxLayout>
#include <QMouseEvent>
#include "Global.h"

class APP_EXPORT ModernDialog : public QDialog {
    Q_OBJECT
public:
    explicit ModernDialog(const QString &title, QWidget *parent = nullptr);
    
    void setContentLayout(QLayout *layout);
    void setWindowTitle(const QString &title);

protected:
    void mousePressEvent(QMouseEvent *event) override;
    void mouseMoveEvent(QMouseEvent *event) override;
    void mouseReleaseEvent(QMouseEvent *event) override;
    void showEvent(QShowEvent *event) override;

private:
    QLabel *m_titleLabel;
    QPushButton *m_closeBtn;
    QWidget *m_titleBar;
    QWidget *m_contentArea;
    QVBoxLayout *m_mainLayout;
    
    bool m_dragging = false;
    QPoint m_dragPos;
};

#endif // MODERN_DIALOG_H
