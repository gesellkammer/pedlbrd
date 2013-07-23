from PySide.QtCore import *
from  PySide.QtGui import *
import sys
import liblo
import time

global qt_app

def _func2osc(func):
    def wrap(path, args, types, src):
        func(*args)
    return wrap

class OSCThread(QThread):
    def __init__(self, gui, parent=None):
        QThread.__init__(self, parent)
        self.s = liblo.Server()
        self.pedlbrd_address = liblo.Address('127.0.0.1', 47120)
        self.register_osc_methods()
        self.s.send(self.pedlbrd_address, '/registerui')
        self.gui = gui
        self._heartbeat_counter = 0

    def register_osc_methods(self):
        cmds = [(a, getattr(self, a)) for a in dir(self) if a.startswith('cmd_')]
        for cmdname, method in cmds:
            _, path = cmdname.split('_')
            path = '/' + path
            func = _func2osc(method)
            print "adding method: ", path, func
            self.s.add_method(path, None, func)
        def default(path, args, types, src):
            print path, args, src
        # self.s.add_method(None, None, default)

    def run(self):
        self._exiting = False
        while self.isRunning() and not self._exiting:
            self.s.recv(50)

    def stop(self):
        self._exiting = True

    def sendosc(self, path, *args):
        self.s.send(self.pedlbrd_address, path, *args)

    def cmd_status(self, status):
        print "status:", status
        self.gui.status.setText(status)
        self.gui.conn_status = status

    def cmd_heartbeat(self):
        self.gui.status.setText('running')
        self._heartbeat_counter += 1
        if self._heartbeat_counter > 9999999:
            self._heartbeat_counter = 0
        self.dispatch_on_heartbeat()

    def dispatch_on_heartbeat(self):
        c = self._heartbeat_counter
        if c % 3:
            self.s.send(self.pedlbrd_address, '/getmidichannel')

    def cmd_midich(self, ch):
        self.gui.set_midi_channel(ch)

class Pedlbrd(QWidget):
    def __init__(self):
        # Initialize the object as a QWidget and
        # set its title and minimum width
        QWidget.__init__(self)
        self.setup_widgets()
        self.osc_thread = OSCThread(self)
        self.osc_thread.start()
        self.conn_status = None

    def setup_widgets(self):
        self.setWindowTitle('Pedlbrd')
        self.setMinimumWidth(400)
 
        # Create the QVBoxLayout that lays out the whole form
        self.layout = QVBoxLayout()
 
        # Create the form layout that manages the labeled controls
        self.form_layout = QFormLayout()
        self.form_layout.setFormAlignment(Qt.AlignTop | Qt.AlignLeft)

        self.status = QLabel('...', self)
        self.form_layout.addRow('STATUS', self.status)
 
        midichannels = [str(i) for i in range(1, 17)]
        self.midichannel_combo = QComboBox(self)
        self.midichannel_combo.addItems(midichannels)
 
        # Add it to the form layout with a label
        self.form_layout.addRow('&MIDI Channel', self.midichannel_combo)
         
        # Add the form layout to the main VBox layout
        self.layout.addLayout(self.form_layout)
 
        # Add stretch to separate the form layout from the button
        self.layout.addStretch(1)
 
        # Create a horizontal box layout to hold the button
        self.button_box = QHBoxLayout()
 
        # Add stretch to push the button to the far right
 
        calibrate_button = QPushButton('Calibrate', self)
        calibrate_button.clicked.connect(self.action_calibrate)
        console_button = QPushButton('Console', self)
        console_button.clicked.connect(self.action_console)
        self.quit_button = QPushButton('Quit', self)
        self.quit_button.clicked.connect(QCoreApplication.instance().quit)

        # Add it to the button box
        self.button_box.addWidget(calibrate_button)
        self.button_box.addWidget(console_button)

        self.button_box.addStretch(1)
        self.button_box.addWidget(self.quit_button)

        # Add the button box to the bottom of the main VBox layout
        self.layout.addLayout(self.button_box)
 
        # Set the VBox layout as the window's main layout
        self.setLayout(self.layout)

    def action_calibrate(self):
        self.osc_thread.sendosc('/calibrate')

    def action_console(self):
        self.osc_thread.sendosc('/openlog', 0)

    def set_midi_channel(self, ch):
        self.midichannel_combo.setCurrentIndex(ch)

    def run(self):
        # Show the form
        print "running!"
        
        self.show()
        # Run the qt application
        qt_app.exec_()
 
def startgui():
    # Create an instance of the application window and run it
    global qt_app
    qt_app = QApplication(sys.argv)
    app = Pedlbrd()
    app.run()
    app.osc_thread.stop()
    time.sleep(0.2)

if __name__ == '__main__':
    startgui()