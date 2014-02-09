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
    def __init__(self, gui, pedlbrd_address, parent=None):
        QThread.__init__(self, parent)
        self.s = liblo.Server()
        if isinstance(pedlbrd_address, (list, tuple)):
            addr = liblo.Address(*pedlbrd_address)
        else:
            addr = liblo.Address(pedlbrd_address)
        self.pedlbrd_address = addr
        self.register_osc_methods()
        self.s.send(self.pedlbrd_address, '/registerall')
        self.gui = gui
        self._heartbeat_counter = 0
        self._reply_callbacks = {}
        self._last_replyid = 0
        self._last_time_anpin = [0, 0, 0, 0]

    def register_osc_methods(self):
        cmds = [(a, getattr(self, a)) for a in dir(self) if a.startswith('cmd_')]
        for cmdname, method in cmds:
            path = cmdname.split("_")[1:]
            path = '/' + '/'.join(path)
            func = _func2osc(method)
            self.s.add_method(path, None, func)
        def default(path, args, types, src):
            print path, args, src
        def reply_handler(path, args, types, src):
            reply_id = args[1]
            func = self._reply_callbacks.get(reply_id)
            if func:
                reply_args = args[2:]
                func(*reply_args)

    def run(self):
        self._exiting = False
        while self.isRunning() and not self._exiting:
            self.s.recv(50)

    def stop(self):
        self._exiting = True

    def sendosc(self, path, *args):
        self.s.send(self.pedlbrd_address, path, *args)

    def cmd_status(self, status):
        self.gui.set_status(status)

    def cmd_heartbeat(self):
        self.gui.on_heartbeat()

    def cmd_changed_midichannel(self, label, channel):
        if label == "*":
            self.gui.set_midichannel(channel)

    def cmd_data_D(self, digpin, value):
        #self.gui.set_digitalpin(digpin, value)
        self.gui.digpins[digpin-1].setValue(value)

    def cmd_data_A(self, anpin, value):
        now = time.time()
        if now - self._last_time_anpin[anpin] > 0.05:
            #self.gui.anpins[anpin-1].setValue(value*256)
            self.gui.anpins[anpin-1].setValue(value)
            self._last_time_anpin[anpin] = now

    def get(self, param, callback, *args):
        path = "/%s/get" % param
        reply_id = self._get_reply_id()
        print "get, param: %s, reply_id: %d" % (param, reply_id)
        self._reply_callbacks[reply_id] = callback
        self.s.send(self.pedlbrd_address, path, reply_id, *args)

    def cmd_reply(self, param, replyid, *args):
        func = self._reply_callbacks.get(replyid)
        if func:
            func(*args)

    def _get_reply_id(self):
        self._last_replyid = (self._last_replyid + 1) % 999999
        return self._last_replyid

class Slider(QWidget):
    def __init__(self, parent=None):
        super(Slider, self).__init__(parent)
        self._value = 0
        pen = QPen()
        pen.setColor(QColor(50, 50, 50, 50))
        pen.setWidth(4)
        self._pen = pen
        self._coloroff = QColor(240, 240, 240)
        self._coloron  = QColor(80, 10, 255)
    def minimumSizeHint(self):
        return (10, 10)
    def paintEvent(self, event):
        p = QPainter()
        p.begin(self)
        p.setPen(self._pen)
        p.setBrush(self._coloroff)
        h = self.height()
        w = self.width()
        y = h * (1-self._value)
        p.drawRect(0, 0, w, y)
        p.setBrush(self._coloron)
        p.drawRect(0, y, w, h-y)
        p.end()
    def setValue(self, value):
        self._value = value
        self.repaint()

class CheckBox(QWidget):
    def __init__(self, size, parent=None):
        super(CheckBox, self).__init__(parent)
        self._size = size
        self.value = 0
    def minimumSizeHint(self):
        return QSize(self._size, self._size)
    
    def get_center(self):
        size = self.size()
        w, h = size.width(), size.height()
        return w * 0.5, h*0.5

    def setValue(self, value):
        self.value = value
        self.repaint()

    def paintEvent(self, event):
        p = QPainter()
        pen = QPen()
        pen.setColor(QColor(50, 50, 50, 50))
        pen.setWidth(4)
        p.begin(self)
        cx, cy = self.get_center()
        r = self._size * 0.5
        p.setPen(pen)
        if self.value == 0:
            p.setBrush(QColor(240, 240, 240))
        else:
            p.setBrush(QColor(255, 0, 0))
        p.drawRect(cx-r, cy-r, self._size, self._size)
        p.end()

class Pedlbrd(QWidget):
    def __init__(self, pedlbrd_address):
        # Initialize the object as a QWidget and
        # set its title and minimum width
        super(Pedlbrd, self).__init__()
        self.setup_widgets()
        self.osc_thread = OSCThread(self, pedlbrd_address=pedlbrd_address)
        self.osc_thread.start()
        self.conn_status = None
        self._last_heartbeat = time.time()

    def on_heartbeat(self):
        new_status = "ACTIVE"
        if self.conn_status != new_status:
            self.update()
        self.set_status("ACTIVE")
        
    def update(self):
        # update midichannel
        def callback(chan):
            print "callback!", chan
            self.set_midichannel(chan)
        self.osc_thread.get("midichannel", callback)

    def set_status(self, status):
        self.conn_status = status
        self.status.setText(status)

    def setup_widgets(self):
        self.setWindowTitle('Pedlbrd')
        self.setMinimumWidth(200)
 
        # Create the QVBoxLayout that lays out the whole form
        self.layout = QVBoxLayout()
 
        # Create the form layout that manages the labeled controls
        form_layout = QFormLayout()
        form_layout.setFormAlignment(Qt.AlignTop | Qt.AlignLeft)

        self.status = QLabel('...', self)
        form_layout.addRow('STATUS', self.status)
 
        midichannels = [str(i) for i in range(1, 17)]
        self.midichannel_combo = QComboBox(self)
        self.midichannel_combo.addItems(midichannels)
        self.midichannel_combo.activated[int].connect(self.action_midichannel)
 
        # Add it to the form layout with a label
        form_layout.addRow('&MIDI Channel', self.midichannel_combo)
         
        # Add the form layout to the main VBox layout
        self.layout.addLayout(form_layout)
 
        # Add stretch to separate the form layout from the button
        # self.layout.addStretch(1)
 
        # Create a horizontal box layout to hold the button
        button_box = QHBoxLayout()
 
        # Add stretch to push the button to the far right
        reset_button = QPushButton('Reset', self)
        reset_button.clicked.connect(self.action_reset)
        console_button = QPushButton('Console', self)
        console_button.clicked.connect(self.action_console)
        
        self.quit_button = QPushButton('Quit', self)
        self.quit_button.clicked.connect(QCoreApplication.instance().quit)
        self.quit_button.clicked.connect(self.action_quit)

        # Add it to the button box
        button_box.addWidget(reset_button)
        button_box.addWidget(console_button)

        button_box.addStretch(1)
        button_box.addWidget(self.quit_button)
        
        # Grid
        grid0 = QGridLayout()
        grid_size = 50
        chks = [CheckBox(grid_size, self) for i in range(10)]
        grid = QGridLayout()
        self.digpins = chks
        grid.setSpacing(2)
        positions = ((0, 0), (0, 1), (0, 2), (1, 0), (1, 1), (1, 2), (2, 0), (2, 1), (2, 2), (3, 1))
        for chk, position in zip(chks, positions):
            x, y = position
            # chk.setFixedSize(grid_size*0.5, grid_size*0.5)
            grid.addWidget(chk, x, y)
        grid0.addLayout(grid, 0, 0)
        
        # Sliders
        def new_slider():
            # s = QSlider(Qt.Vertical)
            s = Slider()
            #s.setMinimum(0)
            #s.setMaximum(256)
            return s
        sliders = [new_slider() for i in range(4)]
        self.anpins = sliders
        slider_grid = QGridLayout()
        for i, slider in enumerate(sliders):
            slider_grid.addWidget(slider, 0, i, 1, 1)

            # addWidget ( QWidget * widget, int fromRow, int fromColumn, int rowSpan, int columnSpan, Qt::Alignment alignment = 0 )

        grid0.addLayout(slider_grid, 0, 1)
 
        # Set the VBox layout as the window's main layout
        self.layout.addLayout(grid0)
        self.setLayout(self.layout)

        # Add the button box to the bottom of the main VBox layout
        self.layout.addLayout(button_box)

        self.setFixedSize(self.sizeHint())

    def set_digitalpin(self, pin, value):
        self.digpins[pin-1].setValue(value)

    def action_quit(self):
        self.osc_thread.sendosc('/quit')
        QCoreApplication.instance().quit()
        
    def action_reset(self):
        self.osc_thread.sendosc('/resetstate')
        for digpin in self.digpins:
            digpin.setValue(0)

    def action_console(self):
        self.osc_thread.sendosc('/openlog', 0)

    def action_midichannel(self, index):
        self.osc_thread.sendosc('/midichannel/set', '*', index)

    def set_midichannel(self, ch):
        self.midichannel_combo.setCurrentIndex(ch)

    def cmd_devinfo(self, tags, devid, max_digpins, max_anpins, num_digpins, num_anpins, *args):
        self._devinfo = {
            'devid' : devid,
            'max_digpins':max_digpins,
            'max_anpins' : max_anpins,
            'num_digpins' : num_digpins,
            'num_anpins' : num_anpins
        }

    def run(self):
        # Show the form
        self.show()
        # Run the qt application
        qt_app.exec_()
 
def start(pedlbrd_address=("localhost", 47120)):
    # Create an instance of the application window and run it
    global qt_app
    qt_app = QApplication(sys.argv)
    app = Pedlbrd( pedlbrd_address )
    app.run()
    app.osc_thread.stop()
    time.sleep(0.2)

if __name__ == '__main__':
    start()