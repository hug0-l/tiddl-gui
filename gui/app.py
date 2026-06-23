import sys
import asyncio

from PySide6.QtWidgets import QApplication, QMainWindow
import qasync

from tiddl.cli.config import load_config_file, APP_PATH, CONFIG_FILENAME


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("tiddl-gui")
        self.resize(800, 600)


def main():
    """Entry point for tiddl-gui. Creates QApplication, sets up qasync event loop."""
    app = QApplication(sys.argv)
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    load_config_file(APP_PATH / CONFIG_FILENAME)

    window = MainWindow()
    window.show()

    loop.run_forever()
