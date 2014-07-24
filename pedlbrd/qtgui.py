from PySide.QtCore import *
from  PySide.QtGui import *
import sys, os, time, subprocess
import liblo
import Queue
import logging
import time

global qt_app

LOGPATH = '~/.log/pedlbrd-gui.log'

LOGPATH = os.path.expanduser(LOGPATH)
LOGDIR = os.path.split(LOGPATH)[0]

if not os.path.exists(LOGDIR):
    os.mkdir(LOGDIR)

logging.basicConfig(filename=LOGPATH,level=logging.DEBUG)

## /////////// HELPERS ////////////

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
    QCoreApplication.postEvent(_invoker,
        InvokeEvent(fn, *args))

## /////////////////// OSC //////////////////////

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

    def cmd_data_D(self, pin, value):
        self.gui.digpins[pin-1].setValue(value)
    
    def cmd_data_A(self, pin, value, rawvalue):
        self.gui.anpins[pin - 1].setValue(value)

    def _get(self, param, callback, args, in_main_thread):
        path = "/%s/get" % param
        reply_id = self._get_reply_id()
        logging.debug("get, param: %s, reply_id: %d" % (param, reply_id))
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
        invoke_in_main_thread(lambda gui:gui.calibrated(), self.gui)

    def cmd_quit(self):
        # the core is asking to quit
        self.gui.action_quit(notify_core=False)


    def _get_reply_id(self):
        self._last_replyid = (self._last_replyid + 1) % 999999
        return self._last_replyid

## /////////// WIDGETS

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
        self._dirty = False
    
    def minimumSizeHint(self):
        return QSize(8, 10)

    def sizeHint(self):
        return QSize(8, 100)
    
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
        self._dirty = False
    
    def setValue(self, value):
        if value != self._value:
            self._dirty = True
            self._value = value
        
class BigCheckBox(QWidget):
    def __init__(self, size, parent=None):
        super(BigCheckBox, self).__init__(parent)
        self._size = size
        self._dirty = False
        self.value = 0
        self._pen = pen = QPen()
        pen.setColor(QColor(50, 50, 50, 50))
        pen.setWidth(4)
        self._brushes = (QColor(240, 240, 240), QColor(255, 0, 0))

    def minimumSizeHint(self):
        return QSize(self._size, self._size)
    
    def get_center(self):
        size = self.size()
        w, h = size.width(), size.height()
        return w * 0.5, h*0.5

    def setValue(self, value):
        if value != self.value:
            self.value = value
            self._dirty = True

    def paintEvent(self, event):
        p = QPainter()
        pen = self._pen
        p.begin(self)
        cx, cy = self.get_center()
        r = self._size * 0.5
        p.setPen(pen)
        p.setBrush(self._brushes[self.value>0])
        p.drawRect(cx-r, cy-r, self._size, self._size)
        p.end()
        self._dirty = False

#######################################################
#         MAIN             
#######################################################

class Pedlbrd(QWidget):
    def __init__(self, pedlbrd_address):
        super(Pedlbrd, self).__init__()
        self._pedlbrd_address = pedlbrd_address
        self._midithrough_index = None
        self._subprocs = {}
        self.conn_status = None
        self.osc_thread = OSCThread(self, pedlbrd_address=pedlbrd_address)
        self.osc_thread.start()
        self._polltimer_updaterate = 12
        self.setWindowIcon(QIcon('assets/pedlbrd-icon.png'))
        self._midiports = []

        # -----------------------------------------------
        self.setup_widgets()
        self.create_polltimer()
        self.call_later(1000, self.post_init)
        
    def create_polltimer(self):
        self._polltimer = timer = QTimer()
        timer.timeout.connect(self.poll_action)
        timer.start(1000 / self._polltimer_updaterate)

    def calibrated(self):
        self.reset_digital_pins()
        self.update_status()
        
    def on_heartbeat(self):
        new_status = "ACTIVE"
        if self.conn_status != new_status:
            self.update_status()
        self.set_status("ACTIVE")
        
    def update_status(self):
        self.osc_thread.get_mainthread("midichannel", lambda chan:self.set_midichannel)
        self.fetch_midiports()

    def poll_action(self):
        for pin in self.anpins:
            if pin._dirty:
                pin.repaint()
        for pin in self.digpins:
            if pin._dirty:
                pin.repaint()

    def set_status(self, status):
        self.conn_status = status
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

        #osc_in = "%s:%d" % self._pedlbrd_address
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
 
        # Create a horizontal box layout to hold the button
        button_box = QHBoxLayout()
 
        # Add stretch to push the button to the far right
        reset_button = QPushButton('Reset', self)
        reset_button.clicked.connect(self.action_reset)
        debug_button = QPushButton('#', self)
        debug_button.clicked.connect(self.action_debug)
        daemon_button = QPushButton('Hide', self)
        daemon_button.clicked.connect(self.action_daemon)

        
        self.quit_button = QPushButton('Quit', self)
        self.quit_button.clicked.connect(self.action_quit)

        # Add it to the button box
        buttons = [reset_button, debug_button, daemon_button]
        for button in buttons:
            button_box.addWidget(button)

        #button_box.addStretch()
        button_box.addWidget(self.quit_button)
        button_box.setSpacing(2)
        
        # Grid
        grid0 = QGridLayout()
        grid_size = 50
        chks = [BigCheckBox(grid_size, self) for i in range(10)]
        grid = QGridLayout()
        self.digpins = chks
        grid.setSpacing(2)
        positions = ((0, 0), (0, 1), (0, 2), (1, 0), (1, 1), (1, 2), (2, 0), (2, 1), (2, 2), (3, 1))
        for chk, position in zip(chks, positions):
            x, y = position
            grid.addWidget(chk, x, y)
        grid0.addLayout(grid, 0, 0)
        
        # Sliders
        sliders = [Slider() for i in range(4)]
        self.anpins = sliders
        slider_grid = QGridLayout()
        for i, slider in enumerate(sliders):
            slider_grid.addWidget(slider, 0, i, 1, 1)
        slider_grid.setSpacing(6)

        grid0.addLayout(slider_grid, 0, 1)
 
        # Set the VBox layout as the window's main layout
        self.layout.addLayout(grid0)
        self.setLayout(self.layout)

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
        self.fetch_midiports()
        def callback(status):
            self.set_status(status)
        self.osc_thread.get_mainthread('status', callback)

        
    def fetch_midiports(self):
        def callback(ports):
            logging.debug("fetch_midiports: got ports %s" % str(ports))
            if ports != self._midiports:
                logging.debug("midiports changed")
                # Remove old items in combobox
                numitems = self.midiports_combo.count()
                if numitems > 1:
                    for i in range(numitems-1):
                        self.midiports_combo.removeItem(numitems - 1 - i)
                self.midiports_combo.addItems(ports)
                self.midiports_combo.setMinimumWidth(self.midiports_combo.minimumSizeHint().width())
                self.setFixedSize(self.sizeHint())
                if self._midithrough_index > 0:
                    # midiports changed and there was a port selected. Will try to 
                    # find its index and set it as the new selection
                    midiport_name = self._midiports[self._midithrough_index]
                    logging.debug("ports changed and selection was %s" % midiport_name)
                    if midiport_name in ports:
                        newindex = ports.index(midiport_name) + 1 # the +1 is to account for the "No Midithrough" ("------") item
                        logging.debug("old midiport still present at index %d" % newindex)
                        self.midithrough_set(newindex)
                        self.midiports_combo.setCurrentIndex(newindex)
                    else:
                        logging.debug("midiport %s not present any more. Resetting midithrough" % midiport_name)
                        self.midithrough_set(0)
                self._midiports = ports
            else:
                logging.debug("midiports did not change. current ports are: %s" % str(self._midiports))                
        self.get_midiports(callback)

    def set_digitalpin(self, pin, value):
        self.digpins[pin-1].setValue(value)

    def action_quit(self, notify_core=True):
        if notify_core:
            self.osc_thread.sendosc('/quit')
        self.osc_thread.stop()
        time.sleep(0.2)
        QCoreApplication.instance().quit()

    def action_daemon(self):
        self.action_quit(notify_core=False)
        
    def action_reset(self):
        self.osc_thread.sendosc('/resetstate')
        self.reset_digital_pins()
        self.fetch_midiports()
        
    def reset_digital_pins(self):
        for digpin in self.digpins:
            digpin.setValue(0)

    def launch_debugging_console(self):
        pedltalk_proc = self._subprocs.get('pedltalk')
        # this changed
        
        if pedltalk_proc is None or pedltalk_proc.poll() is not None:  # either first call, or subprocess finished

            if sys.platform == 'darwin':
                pedltalkpath = os.path.realpath("pedltalk.py")
                if not os.path.exists(pedltalkpath):
                    logging.error("pedltalk not found! Searched path: %s" % pedltalkpath)
                    return
                p = subprocess.Popen(args=['osascript', 
                    '-e', 'tell app "Terminal"', 
                    '-e', 'do script "{python} {pedltalk}"'.format(python=sys.executable, pedltalk=pedltalkpath),
                    '-e', 'activate',
                    '-e', 'end tell'])
                self._subprocs['pedltalk'] = p
            elif sys.platform == 'linux2': 
                currentdir = os.path.split(os.path.realpath(__file__))[0]
                pedltalkpath = os.path.realpath(os.path.join(currentdir, "../pedltalk.py"))
                if not os.path.exists(pedltalkpath):
                    logging.error("pedltalk not found! Searched path: %s" % pedltalkpath)
                    return
                p = subprocess.Popen(args=["xterm", "-e", "python", pedltalkpath])
                self._subprocs['pedltalk'] = p
                    
                
    def get_midiports(self, notify=None):
        def callback(*ports):
            logging.debug("get_midiports:callback. ports: %s" % str(ports))
            if notify is not None:
                invoke_in_main_thread(notify, ports)
        logging.debug("getting midioutports, notify set: %s" % str(notify is not None))
        self.osc_thread.get('midioutports', callback)

    def action_debug(self):
        self.osc_thread.sendosc('/openlog', 0)
        self.open_gui_log()
        self.launch_debugging_console()
        
    def open_gui_log(self):
        if sys.platform == 'darwin':
            os.system("open -a Console %s" % LOGPATH)
        elif sys.platform == 'linux2':
            os.system("xdg-open %s" % LOGPATH)    

    def action_midithrough(self, index):
        self.midithrough_set(index)

    def midithrough_set(self, index):
        if self._midithrough_index is not None:
            if index == 0:
                self.osc_thread.sendosc('/midithrough/set', self._midithrough_index, 0)
                self._midithrough_index = None
            else:
                self.osc_thread.sendosc('/midithrough/set', self._midithrough_index, 0)
                self._midithrough_index = index - 1
                self.call_later(100, lambda:self.osc_thread.sendosc('/midithrough/set', index-1, 1))
        else:
            if index > 0:
                self._midithrough_index = index - 1
                self.osc_thread.sendosc('/midithrough/set', index-1, 1)

    def call_later(self, ms, action):
        QTimer.singleShot(ms, action)

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
        self.show()
        qt_app.exec_()
 
def start(pedlbrd_address=("localhost", 47120)):
    global qt_app
    qt_app = QApplication(sys.argv)
    app = Pedlbrd( pedlbrd_address )
    app.run()
    app.osc_thread.stop()
    time.sleep(0.2)

if __name__ == '__main__':
    start()