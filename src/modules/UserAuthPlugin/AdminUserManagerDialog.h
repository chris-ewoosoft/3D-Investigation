#ifndef ADMIN_USER_MANAGER_DIALOG_H
#define ADMIN_USER_MANAGER_DIALOG_H

#include <QWidget>

class QTableWidget;
class QPushButton;
class QLabel;

class AdminUserManagerDialog : public QWidget {
    Q_OBJECT
public:
    explicit AdminUserManagerDialog(QWidget *parent = nullptr);

private slots:
    void refreshTable();
    void onAddUser();
    void onEditUser();
    void onDeleteUser();
    void onGenerateLicenseKey();
    void onRevokeLicenseKey();
    void onSmtpSettings();
    void onTableSelectionChanged();
    void onKeyTableCellClicked(int row, int column);

private:
    void setupUi();
    void applyStyle();

    QTableWidget *m_userTable;
    QTableWidget *m_keyTable;
    QPushButton  *m_editBtn;
    QPushButton  *m_deleteBtn;
    QPushButton  *m_revokeBtn;
};

#endif // ADMIN_USER_MANAGER_DIALOG_H
