#ifndef CUSTOMPROGRESSDIALOG_H
#define CUSTOMPROGRESSDIALOG_H

#include <QDialog>
#include <QProgressBar>
#include <QLabel>
#include <QPushButton>
#include "Global.h"

class APP_EXPORT CustomProgressDialog : public QDialog {
    Q_OBJECT
public:
    explicit CustomProgressDialog(QWidget *parent = nullptr);

    void setRange(int minimum, int maximum);
    void setValue(int value);
    void setLabelText(const QString &text);
    void reset();
    void centerOnWidget(QWidget* target = nullptr);

signals:
    void stopRequested();

private slots:
    void onStopClicked();

private:
    QLabel *lblTitle;
    QProgressBar *progressBar;
    QPushButton *btnStop;
};

#endif // CUSTOMPROGRESSDIALOG_H
