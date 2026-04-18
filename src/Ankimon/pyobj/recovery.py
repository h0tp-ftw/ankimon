import shutil
from aqt import mw
from aqt.qt import QDialog, QVBoxLayout, QLabel, QPushButton, QMessageBox, Qt
from ..resources import addon_dir, pre_update_backup_path

class RecoveryDialog(QDialog):
    def __init__(self, exception_msg):
        super().__init__()
        self.setWindowTitle("Ankimon - Critical Error")
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.exception_msg = exception_msg
        
        layout = QVBoxLayout()
        
        msg = QLabel("Ankimon encountered a critical error during startup.")
        layout.addWidget(msg)
        
        err_msg = QLabel(f"Error details:\n{exception_msg}")
        err_msg.setWordWrap(True)
        err_msg.setStyleSheet("color: red;")
        layout.addWidget(err_msg)
        
        copy_btn = QPushButton("Copy Error to Clipboard")
        copy_btn.clicked.connect(self.copy_to_clipboard)
        layout.addWidget(copy_btn)
        
        prompt = QLabel("Would you like to try restoring Ankimon from the pre-update backup?")
        prompt.setStyleSheet("margin-top: 10px;")
        layout.addWidget(prompt)
        
        rollback_btn = QPushButton("Rollback to Previous Version")
        rollback_btn.clicked.connect(self.do_rollback)
        layout.addWidget(rollback_btn)
        
        cancel_btn = QPushButton("Close")
        cancel_btn.clicked.connect(self.reject)
        layout.addWidget(cancel_btn)
        
        self.setLayout(layout)
        
    def copy_to_clipboard(self):
        from aqt.qt import QApplication
        QApplication.clipboard().setText(self.exception_msg)
        
    def do_rollback(self):
        if not pre_update_backup_path.exists():
            QMessageBox.critical(self, "Backup Not Found", "No pre-update backup was found. Cannot rollback.")
            return
            
        try:
            current_dir = addon_dir
            for item in pre_update_backup_path.iterdir():
                target = current_dir / item.name
                if item.is_dir():
                    if target.exists():
                        shutil.rmtree(target)
                    shutil.copytree(item, target)
                else:
                    shutil.copy2(item, target)
            
            QMessageBox.information(self, "Rollback Complete", "Ankimon has been restored. Please restart Anki.")
            self.accept()
            # Try to gracefully close Anki so changes take effect
            if mw:
                mw.close()
                
        except Exception as e:
            QMessageBox.critical(self, "Rollback Failed", f"Failed to restore backup:\n{e}")

def show_recovery_dialog(exception):
    d = RecoveryDialog(str(exception))
    d.exec()
