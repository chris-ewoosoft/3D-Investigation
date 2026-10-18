#ifndef CUSTOMPROGRESSDIALOG_H
#define CUSTOMPROGRESSDIALOG_H

#include <QDialog>
#include <QProgressBar>
#include <QLabel>
#include <QPushButton>
#include "Global.h"

class ILanguageService;

class APP_EXPORT CustomProgressDialog : public QDialog {
    Q_OBJECT
public:
    explicit CustomProgressDialog(ILanguageService *lang, QWidget *parent = nullptr);

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
    ILanguageService *m_lang;
};

#endif // CUSTOMPROGRESSDIALOG_H
