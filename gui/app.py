import sys
import asyncio

from PySide6.QtWidgets import QApplication
import qasync

from gui.error_handler import install_global_exception_hook
from gui.main_window import MainWindow


def main():
    """Entry point for tiddl-gui. Creates QApplication, sets up qasync event loop."""
    app = QApplication(sys.argv)
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    install_global_exception_hook()

    window = MainWindow()
    window.show()

    loop.run_forever()
