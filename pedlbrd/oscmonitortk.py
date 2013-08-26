import time
import sys
from Tkinter import *
from ttk import *
from Queue import Queue

class App(Frame):
    def __init__(self, monitor_constructor, coreaddr, port=None, exclude=None):
        self.monitor_constructor = monitor_constructor
        self.corehost, self.coreport = coreaddr
        self.port = port
        self.osc_monitor = None
        self.exclude = exclude if exclude is not None else []
        self.queue = Queue()
        ok = self.setup_monitor()
        if not ok:
            raise RuntimeError("could not create osc server")
        master = Tk(screenName='OSC Monitor', baseName='oscmonitor')
        master.title("OSC Monitor")
        master.resizable(False,False)
        master.geometry('+0+320')
        Frame.__init__(self, master)
        self.root = master

        self.grid(column=0, row=0, columnspan=4, rowspan=2, sticky=('n', 's', 'e','w'))
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.host_label_ctrl = Label(master, text='Host')
        self.host_label_ctrl.grid(column=0, row=0, sticky='e')

        self.host_entry_ctrl = Entry()
        self.host_entry_ctrl.grid(column=1, row=0, sticky='w')
        
        self.port_label_ctrl = Label(master, text='Port')
        self.port_label_ctrl.grid(column=2, row=0, sticky='e')
        self.port_entry_ctrl = Entry()
        self.port_entry_ctrl.grid(column=3, row=0, sticky='w')

        self.console = Text()#state='disabled')
        self.console.grid(column=0, row=1, columnspan=4)#, 'anchor':'s'})#{})
        self.console.columnconfigure(0, weight=1)
        self.console.rowconfigure(0, weight=1)

        self.port_entry_ctrl_contents = StringVar()
        self.host_entry_ctrl_contents = StringVar()
        
        self.port_entry_ctrl_contents.set(self.coreport)
        self.host_entry_ctrl_contents.set(str(self.corehost))

        self.port_entry_ctrl["textvariable"] = self.port_entry_ctrl_contents
        self.host_entry_ctrl["textvariable"] = self.host_entry_ctrl_contents

        self.port_entry_ctrl.bind('<Key-Return>', self.handle_entry)
        self.host_entry_ctrl.bind('<Key-Return>', self.handle_entry)

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
                N -= 1
            console.see('end lineend')
            console['state'] = DISABLED
        self.root.after(100, self.watch_queue)

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

    def handle_entry(self, event):
        try:
            newport = int(self.port_entry_ctrl_contents.get())
            newhost = self.host_entry_ctrl_contents.get()
            if newport != self.port or newhost != self.host:
                self.port = newport
                self.host = newhost
                self.setup_monitor(self.port)
                self.osc_monitor.start()
        except ValueError:
            self.append_message("Port must be an integer!")
        except None, e:
            self.append_message("Failed to monitor host " + str(self.port_entry_ctrl_contents.get()) + ' at port ' + str(self.host_entry_ctrl_contents.get()) + ":" + str(e))
        
    def append_message(self, message):
        self.console['state'] = NORMAL
        self.console.insert('end lineend', '\n' + message)
        self.console.see('end lineend')
        self.console['state'] = DISABLED
