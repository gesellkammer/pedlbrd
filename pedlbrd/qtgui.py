# from PySide.QtCore import *
from PyQt4.QtCore import *
# from  PySide.QtGui import *
from PyQt4.QtGui import *
import sys
import os
import time
import subprocess
import liblo
import logging

global qt_app

# -------------- CONFIGURATION ------------------------------

MAXIMUM_UPDATE_RATE = 12
LOGPATH = '~/.log/pedlbrd-gui.log'

# -----------------------------------------------------------

LOGPATH = os.path.expanduser(LOGPATH)
LOGDIR = os.path.split(LOGPATH)[0]

if not os.path.exists(LOGDIR):
    os.mkdir(LOGDIR)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gui")
logger.addHandler(logging.FileHandler(LOGPATH))

logger.info("-------------------------- GUI --------------------------")

# /////////// HELPERS ////////////


def _func2osc(func):
    def wrap(path, args, types, src):
        func(*args)
    return wrap


class InvokeEvent(QEvent):
    EVENT_TYPE = QEvent.Type(QEvent.registerEventType())

    def __init__(self, fn, *args):
        QEvent.__init__(self, InvokeEvent.EVENT_TYPE)
        self.fn = fn
        self.args = args


class Invoker(QObject):

    def event(self, event):
        event.fn(*event.args)
        return True

_invoker = Invoker()


def invoke_in_main_thread(fn, *args):
    QCoreApplication.postEvent(_invoker, InvokeEvent(fn, *args))

# /////////////////// OSC //////////////////////


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
        self.s.send(self.pedlbrd_address, '/register')
        self.s.send(self.pedlbrd_address, '/registerui')
        self.gui = gui
        self._heartbeat_counter = 0
        self._reply_callbacks = {}
        self._last_replyid = 0
        self._last_time_anpin = [0, 0, 0, 0]
        self._analog_value = [0, 0, 0, 0]

    def register_osc_methods(self):
        cmds = [(a, getattr(self, a)) for a in dir(self) if a.startswith('cmd_')]
        for cmdname, method in cmds:
            path = cmdname.split("_")[1:]
            path = '/' + '/'.join(path)
            func = _func2osc(method)
            self.s.add_method(path, None, func)

    def run(self):
        self._exiting = False
        recv = self.s.recv
        while not self._exiting:  # self.isRunning() and not self._exiting:
            recv(100)

    def stop(self):
        self._exiting = True

    def sendosc(self, path, *args):
        print("sending osc: %s" % path)
        self.s.send(self.pedlbrd_address, path, *args)

    def cmd_status(self, status):
        self.gui.set_status(status)

    def cmd_changed_midichannel(self, channel):
        self.gui.set_midichannel(channel)

    def cmd_midioutports(self, *ports):
        invoke_in_main_thread(self.gui._update_midiports, ports)

    def cmd_midithrough(self, index):
        self.gui.midithrough_set(index, notifycore=False, updategui=True)

    def cmd_data_D(self, pin, value):
        gui = self.gui
        gui._dirty = True
        gui.digpins[pin].setValue(value)

    def cmd_data_A(self, pin, value, rawvalue):
        gui = self.gui
        gui._dirty = True
        gui.anpins[pin].setValue(value)

    def _get(self, param, callback, args, in_main_thread):
        path = "/%s/get" % param
        reply_id = self._get_reply_id()
        print("get, param: %s, reply_id: %d" % (param, reply_id))
        self._reply_callbacks[reply_id] = (callback, in_main_thread)
        self.s.send(self.pedlbrd_address, path, reply_id, *args)

    def get(self, param, callback, *args):
        """
        communicate with the core via the get protocol.
        The callback should not update the UI
        """
        self._get(param, callback, args, in_main_thread=False)

    def get_mainthread(self, param, callback, *args):
        """
        communicate with the core via the get protocol.
        The callback can update the UI
        """
        self._get(param, callback, args, in_main_thread=True)

    def cmd_reply(self, param, replyid, *args):
        func, in_main_thread = self._reply_callbacks.get(replyid, (None, None))
        if func:
            if in_main_thread:
                invoke_in_main_thread(func, *args)
            else:
                func(*args)

    def cmd_notify_calibrate(self):
        invoke_in_main_thread(lambda gui: gui.calibrated(), self.gui)

    def cmd_quit(self):
        # the core is asking to quit
        print("GUI: asked by core to quit")
        self.gui.action_quit(notify_core=False)

    def _get_reply_id(self):
        self._last_replyid = (self._last_replyid + 1) % 999999
        return self._last_replyid

# /////////// WIDGETS


class Slider(QWidget):

    def __init__(self, index, parent=None):
        super(Slider, self).__init__(parent)
        self._index = index
        self._value = 0
        pen = QPen()
        pen.setColor(QColor(50, 50, 50, 50))
        pen.setWidth(0)
        self._pen = pen
        self._coloroff = QColor(240, 240, 240)
        self._coloron = QColor(0, 180, 255)
        self._height = self.height()
        self._width = self.width()
        self._dirty = False
        self.setMaximumWidth(24)

    def minimumSizeHint(self):
        return QSize(10, 20)

    def sizeHint(self):
        return QSize(20, 100)

    def paintEvent(self, event):
        h = self.height()
        w = self.width()
        y = h * (1 - self._value)
        p = QPainter(self)
        p.setPen(self._pen)
        p.setBrush(self._coloroff)
        p.drawRect(0, 0, w - 1, y - 1)
        p.setBrush(self._coloron)
        p.drawRect(0, y, w, h - y)
        self._dirty = False

    def setValue(self, value):
        h = self._height
        if abs(value * h - self._value * h) >= 1:
            self._dirty = True
            self._value = value


class BigCheckBox(QWidget):

    def __init__(self, size, parent=None):
        super(BigCheckBox, self).__init__(parent)
        self._size = size
        self._dirty = False
        self.value = 0
        self._pen = pen = QPen()
        gray = 200  # 0-255
        pen.setColor(QColor(gray, gray, gray))
        pen.setWidth(1)
        self._brushes = (QColor(240, 240, 240), QColor(0, 180, 255))
        self.firstpaint = True

    def minimumSizeHint(self):
        return QSize(self._size+1, self._size+1)

    def get_center(self):
        size = self.size()
        w, h = size.width(), size.height()
        cx = w * 0.5
        cy = h * 0.5
        center = (cx, cy)
        self._center = center
        return center

    def setValue(self, value):
        if value != self.value:
            self.value = value
            self._dirty = True

    def paintEvent(self, event):
        r = self._size * 0.5
        if self.firstpaint:
            cx, cy = self.get_center()
            self.firstpaint = False
        else:
            cx, cy = self._center
        p = QPainter()
        pen = self._pen
        p.begin(self)
        p.setPen(pen)
        p.setBrush(self._brushes[self.value > 0])
        p.drawRect(cx - r, int(cy - r), self._size-1, self._size - 1)
        p.end()
        self._dirty = False

#######################################################
#         MAIN             
#######################################################


class Pedlbrd(QWidget):

    def __init__(self, pedlbrd_address):
        super(Pedlbrd, self).__init__()
        self._pedlbrd_address = pedlbrd_address
        self._midithrough_index = 0
        self._subprocs = {}
        self.conn_status = None
        self.osc_thread = OSCThread(self, pedlbrd_address=pedlbrd_address)
        self._polltimer_updaterate = MAXIMUM_UPDATE_RATE
        self.setWindowIcon(QIcon('assets/pedlbrd-icon.png'))
        self._midiports = []
        self._analog_dirty = [False, False, False, False, False, False]
        self._dirty = False
        self._quitting = False

        # -----------------------------------------------
        self.setup_widgets()
        self.create_polltimer()
        self.osc_thread.start()
        self.call_later(500, self.post_init)

    def create_polltimer(self):
        self._polltimer = timer = QTimer()
        timer.timeout.connect(self.poll_action)
        timer.start(1000 / self._polltimer_updaterate)

    def calibrated(self):
        self.reset_digital_pins()
        self.update_status()

    def update_status(self):
        self.osc_thread.get_mainthread("midichannel", lambda chan: self.set_midichannel)
        self.get_midiports()

    def poll_action(self):
        if self._dirty:
            for pin in self.anpins:
                if pin._dirty:
                    pin.repaint()
            for pin in self.digpins:
                if pin._dirty:
                    pin.repaint()
            self._dirty = False

    def set_status(self, status):
        self.conn_status = status.strip()
        self.status.setText(status)

    def setup_widgets(self):
        self.setWindowTitle('Pedlbrd')
        self.setMinimumWidth(200)

        # Create the QVBoxLayout that lays out the whole form
        self.layout = QVBoxLayout()

        # Create the form layout that manages the labeled controls
        self.widget_info = form_layout = QFormLayout()
        form_layout.setFormAlignment(Qt.AlignTop | Qt.AlignLeft)

        self.status = QLabel('...', self)
        form_layout.addRow('STATUS', self.status)

        osc_in = str(self._pedlbrd_address[1])
        form_layout.addRow('OSC IN', QLabel(osc_in))

        self._oscout = QLabel(str(47121))
        form_layout.addRow('OSC OUT', self._oscout)

        midichannels = [str(i) for i in range(1, 17)]
        self.midichannel_combo = QComboBox(self)
        self.midichannel_combo.addItems(midichannels)
        self.midichannel_combo.activated[int].connect(self.action_midichannel)

        # Add it to the form layout with a label
        form_layout.addRow('&MIDI Channel', self.midichannel_combo)

        self.midiports_combo = QComboBox(self)
        self.midiports_combo.addItems(["- - - - - - - -"])
        form_layout.addRow('&MIDI Through', self.midiports_combo)
        self.midiports_combo.activated[int].connect(self.action_midithrough)

        # Add the form layout to the main VBox layout
        self.layout.addLayout(form_layout)

        # Create a horizontal box layout to hold the buttons
        button_box = QHBoxLayout()

        reset_button = QPushButton('Reset', self)
        reset_button.clicked.connect(self.action_reset)
        debug_button = QPushButton('Debug', self)
        debug_button.clicked.connect(self.action_debug)
        daemon_button = QPushButton('Hide', self)
        daemon_button.clicked.connect(self.action_daemon)

        self.quit_button = QPushButton('Quit', self)
        self.quit_button.clicked.connect(lambda: self.action_quit(notify_core=True))

        # Add it to the button box
        buttons = [reset_button, debug_button, daemon_button]
        for button in buttons:
            button_box.addWidget(button)

        button_box.addWidget(self.quit_button)
        button_box.setSpacing(2)

        # Grid
        grid0 = QGridLayout()
        grid_size = 50
        chks = [BigCheckBox(grid_size, self) for i in range(10)]
        grid = QGridLayout()
        self.digpins = chks
        grid.setSpacing(2)
        positions = (
            (0, 0), (0, 1), (0, 2),
            (1, 0), (1, 1), (1, 2),
            (2, 0), (2, 1), (2, 2),
                    (3, 1)
        )
        for chk, position in zip(chks, positions):
            x, y = position
            grid.addWidget(chk, x, y)
        grid0.addLayout(grid, 0, 0)

        # Sliders
        sliders = [Slider(i) for i in range(4)]
        self.anpins = sliders
        slider_grid = QGridLayout()
        for i, slider in enumerate(sliders):
            slider_grid.addWidget(slider, 0, i, 1, 1)
        slider_grid.setSpacing(1)

        grid0.addLayout(slider_grid, 0, 1)

        # Set the VBox layout as the window's main layout
        self.layout.addLayout(grid0)
        self.setLayout(self.layout)
        self.setMaximumWidth(280)

        # midiports
        """
        midiports_layout = QVBoxLayout()
        midiports_checkboxes = [QCheckBox("", self) for i in range(5)]
        for chk in midiports_checkboxes:
            chk.setChecked(False)
            midiports_layout.addWidget(chk)
        self.midiports_checkboxes = midiports_checkboxes
        self.midiports_layout = midiports_layout
        self.layout.addLayout(self.midiports_layout)
        """

        # Add the button box to the bottom of the main VBox layout
        self.layout.addLayout(button_box)

    def post_init(self):
        print("--------------------- post_init")
        self.get_midiports()
        self.osc_thread.get('status', lambda status: invoke_in_main_thread(self.set_status, status))
        self.osc_thread.get('midithrough', lambda index: self.midithrough_set(index, notifycore=False, updategui=True))
        self.osc_thread.get('midichannel', 
                            lambda chan: invoke_in_main_thread(self.midichannel_combo.setCurrentIndex, chan))

    def _update_midiports(self, ports):
        if ports != self._midiports:
            # Remove old items in combobox
            numitems = self.midiports_combo.count()
            if numitems > 1:
                for i in range(numitems - 1):
                    self.midiports_combo.removeItem(numitems - 1 - i)
            self.midiports_combo.addItems(ports)
            self.midiports_combo.setMinimumWidth(
                self.midiports_combo.minimumSizeHint().width())
            #self.setFixedSize(self.sizeHint())
            if self._midithrough_index > 0:
                # midiports changed and there was a port selected.
                # find its index and set it as the new selection
                midiport_name = self._midiports[self._midithrough_index - 1]
                print("ports changed and selection was %s" % midiport_name)
                if midiport_name in ports:
                    # the +1 is to account for the "No Midithrough" ("------") item
                    newindex = ports.index(midiport_name) + 1  
                    print("old midiport still present at index %d" % newindex)
                    self.midithrough_set(newindex)
                    self.midiports_combo.setCurrentIndex(newindex)
                else:
                    print("midiport %s not present any more. Resetting midithrough" 
                          % midiport_name)
                    self.midithrough_set(0)
            self._midiports = ports
        else:
            print("midiports did not change. current ports are: %s" 
                  % str(self._midiports))

    def set_digitalpin(self, pin, value):
        self.digpins[pin - 1].setValue(value)

    def action_quit(self, notify_core=True):
        if self._quitting:
            print("GUI: action_quit called, but already quitting")
            return
        self._quitting = True
        print("GUI: action_quit: quitting now")
        if notify_core:
            print("action_quit: sending /quit to core")
            self.osc_thread.sendosc('/quit')
        else:
            print("Quitting but not sending /quit to core")
        print("action_quit: stopping osc thread")
        self.osc_thread.stop()
        time.sleep(0.2)
        QCoreApplication.instance().quit()

    def action_daemon(self):
        print("------> action_daemon")
        self.action_quit(notify_core=False)

    def action_reset(self):
        self.osc_thread.sendosc('/resetstate')
        self.reset_digital_pins()
        self.get_midiports()

    def reset_digital_pins(self):
        for digpin in self.digpins:
            digpin.setValue(0)

    def launch_debugging_console(self):
        pedltalk_proc = self._subprocs.get('pedltalk')
        # either first call, or subprocess finished
        if pedltalk_proc is None or pedltalk_proc.poll() is not None:
            if sys.platform == 'darwin':
                pedltalkpath = os.path.realpath("pedltalk.py")
                if not os.path.exists(pedltalkpath):
                    logger.error("pedltalk not found! Searched path: %s" % pedltalkpath)
                    return
                args = [
                    'osascript', '-e', 'tell app "Terminal"', 
                    '-e', 'do script "{python} {pedltalk}"'.format(python=sys.executable, pedltalk=pedltalkpath),
                    '-e', 'activate',
                    '-e', 'end tell']
                self._subprocs['pedltalk'] = subprocess.Popen(args=args)
            elif sys.platform == 'linux2': 
                currentdir = os.path.split(os.path.realpath(__file__))[0]
                pedltalkpath = os.path.realpath(
                    os.path.join(currentdir, "../pedltalk.py"))
                if not os.path.exists(pedltalkpath):
                    logger.error("pedltalk not found! Searched path: %s" % pedltalkpath)
                    return
                self._subprocs['pedltalk'] = subprocess.Popen(args=["xterm", "-e", "python", pedltalkpath])

    def get_midiports(self, callback=None):
        """
        callback: function, if given, it will be called with the ports as argument
        """
        def pre_callback(*ports):
            print("get_midiports:callback. ports: %s" % str(ports))
            invoke_in_main_thread(self._update_midiports, ports)
            if callback is not None:
                callback(ports)
        self.osc_thread.get_mainthread('midioutports', pre_callback)

    def action_debug(self):
        self.osc_thread.sendosc('/openlog', 0)
        self.launch_debugging_console()

    def action_midithrough(self, index):
        self.midithrough_set(index)

    def midithrough_set(self, index, updategui=False, notifycore=True):
        if notifycore:
            if index != self._midithrough_index:
                self.osc_thread.sendosc('/midithrough/set', self._midithrough_index - 1, 0)
            if index > 0:
                self.call_later(100, lambda: self.osc_thread.sendosc('/midithrough/set', index - 1, 1))
        self._midithrough_index = index
        if updategui:
            invoke_in_main_thread(self.midiports_combo.setCurrentIndex, index)

    def call_later(self, ms, action):
        QTimer.singleShot(ms, action)

    def action_midichannel(self, index):
        self.osc_thread.sendosc('/midichannel/set', index)

    def set_midichannel(self, ch):
        self.midichannel_combo.setCurrentIndex(ch)

    def cmd_devinfo(self, tags, devid, max_digpins, max_anpins, 
                    num_digpins, num_anpins, *args):
        self._devinfo = {
            'devid': devid,
            'max_digpins': max_digpins,
            'max_anpins': max_anpins,
            'num_digpins': num_digpins,
            'num_anpins': num_anpins
        }

    def run(self):
        self.show()
        qt_app.exec_()


def start(pedlbrd_address=("localhost", 47120)):
    global qt_app
    qt_app = QApplication(sys.argv)
    app = Pedlbrd(pedlbrd_address)
    app.run()  # <-------------- this will block
    app.osc_thread.stop()


if __name__ == '__main__':
    start()
