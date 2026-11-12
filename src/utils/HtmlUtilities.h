#ifndef HTMLUTILITIES_H
#define HTMLUTILITIES_H

#include <QString>
#include <QRegularExpression>

#include "Global.h"

class APP_EXPORT HtmlUtilities {
public:
    static QString mdToHtml(QString txt);
};

#endif // HTMLUTILITIES_H
