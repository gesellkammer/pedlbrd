import time
import sys
from Tkinter import *
from ttk import *
from Queue import Queue
from tkFont import Font

class App(Frame):
    def __init__(self, monitor_constructor, coreaddr, port=None, exclude=None):
        self.monitor_constructor = monitor_constructor
        self.corehost, self.coreport = coreaddr
        self.port = port
        self.osc_monitor = None
        self.exclude = exclude if exclude is not None else []
        self.queue = Queue()
        self.update_period_ms = 100
        ok = self.setup_monitor()
        if not ok:
            raise RuntimeError("could not create osc server")
        #master = Tk(screenName='OSC Monitor', baseName='oscmonitor')
        master = Tk(screenName='OSC Monitor')
        master.title("OSC Monitor")
        master.resizable(False,False)
        master.geometry('+0+400')
        Frame.__init__(self, master)
        self.root = master
        self._console_lines = 0

        #self.grid(column=0, row=0, columnspan=4, rowspan=2, sticky=('n', 's', 'e','w'))
        #self.columnconfigure(0, weight=1)
        #self.rowconfigure(0, weight=1)

        self.console = Text()
        self.console.grid(column=0, row=1, columnspan=4)#, 'anchor':'s'})#{})
        self.console.columnconfigure(0, weight=1)
        self.console.rowconfigure(0, weight=1)
        self.console['state'] = DISABLED
        font = Font(family='Monaco', size=10)
        self.console.configure(font=font)

        self.menu = Menu(tearoff=False)
        self.root.config(menu=self.menu)
        fm = self.file_menu = None
        fm = Menu(self.menu, tearoff=False)
        self.menu.add_cascade(label='File', menu=fm)

        appmenu = Menu(self.menu, name='apple')
        self.menu.add_cascade(menu=appmenu)
        self.root.protocol('WM_DELETE_WINDOW', lambda *args:self.quit())
        self.root.createcommand('tkAboutDialog', lambda *args:None)
        self.root.createcommand('exit', self.quit)

        self.osc_monitor.start()
        self._running = True
        self.watch_queue()

    def watch_queue(self):
        if not self._running:
            return
        q = self.queue
        if q.not_empty:     
            N = 50
            console = self.console
            console['state'] = NORMAL
            insert = console.insert
            while N > 0:
                if q.empty():
                    break
                msg = q.get_nowait()        
                insert('end lineend', '\n' + msg)
                self._console_lines += 1
                if self._console_lines > 200:
                    self.console.delete(1.0, 100.0)
                    self._console_lines -= 100
                N -= 1
            console.see('end lineend')
            console['state'] = DISABLED
        self.root.after(self.update_period_ms, self.watch_queue)

    def post(self, msg):
        self.queue.put(msg)

    def setup_monitor(self):
        if self.osc_monitor is not None:
            self.osc_monitor.stop()
            self.osc_monitor.free()
        self.osc_monitor = self.monitor_constructor(app=self, exclude=self.exclude)
        self.port = self.osc_monitor.server.port
        return self.osc_monitor.ok

    def quit(self, external=False):
        self._running = False
        if not external:
            self.osc_monitor.signout()
        def quit2():
            self.root.quit()
        self.root.after(120, quit2)

    # def handle_entry(self, event):
    #     try:
    #         newport = int(self.port_entry_ctrl_contents.get())
    #         newhost = self.host_entry_ctrl_contents.get()
    #         if newport != self.port or newhost != self.host:
    #             self.port = newport
    #             self.host = newhost
    #             self.setup_monitor(self.port)
    #             self.osc_monitor.start()
    #     except ValueError:
    #         self.append_message("Port must be an integer!")
    #     except None, e:
    #         self.append_message("Failed to monitor host " + str(self.port_entry_ctrl_contents.get()) + ' at port ' + str(self.host_entry_ctrl_contents.get()) + ":" + str(e))
        
    def append_message(self, message):
        self.console['state'] = NORMAL
        self.console.insert('end lineend', '\n' + message)
        self.console.see('end lineend')
        self.console['state'] = DISABLED
