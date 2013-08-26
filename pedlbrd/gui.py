import os
import sys
import time
import shutil
import glob
import liblo
import Queue
import subprocess

from Tkinter import Tk, StringVar, Menu
import ttk
import tkFont

# TODO:
# * very simple monitor and activity signal (we would need to register to data)

#######################
# API
#######################

_PREPARED = False

def prepare():
	global _PREPARED
	if _PREPARED:
		return
	install_fonts()
	_PREPARED = True

def start(coreaddr, guiport=None):
	"""
	coreaddr: a tuple (hostname, ort)
	guiport : a port number or None to create one ad-hoc (a random valid port)
	"""
	prepare()
	gui = GUI(coreaddr, guiport)
	gui.start() # block

######################
# GUI
######################

class GUI(object):
	def __init__(self, coreaddr, guiport=None):
		self.connection = 'CORE NOT PRESENT'
		self.coreaddr = as_liblo_address(coreaddr)
		self.guiport = guiport
		self._reply_callbacks = {}
		self._replyid = 0
		self._ip = None
		self.setup_widgets()
		self.oscserver = self.make_oscserver()
		self.oscserver.start()
		self._reset_lasttime = time.time()
		self._reset_mintime = 1
		self._quitting = False
		self._subprocs = {}

	def make_oscserver(self):
		def heartbeat(path, args):
			self.heartbeat()
		def status_handler(path, args):
			status = args[0]
			self.set_status(status)
		def quit_handler(path, args):
			self.quit(quitcore=False)
		def reply_handler(path, args):
			ID = args[0]
			callback = self._reply_callbacks[ID]
			if callback:
				try:
					out = args[1:]
					if len(out) == 1:
						callback(out[0])
					else:
						callback(out)
				except:
					print "Error in reply callback. ID=%s" % str(ID), sys.exc_info(), args
					return
		if self.guiport is None:
			s = liblo.ServerThread()
			self.guiport = s.port
		else:
			while True:
				try:
					s = liblo.ServerThread(self.guiport)
					break
				except liblo.ServerError:
					print "Could not create a Server at port %d. Trying with port %d" % (self.guiport, self.guiport+1)
					self.guiport += 1
		print "gui: using port %d" % self.guiport

		s.add_method('/heartbeat', None, heartbeat)
		s.add_method('/status', 's', status_handler)
		s.add_method('/reply', None, reply_handler)
		s.add_method('/quit', None, quit_handler)
		s.send(self.coreaddr, '/registerui')
		return s

	def get_future(self, post=None):
		return Future(sleepfunc=self.win.after, post=post)

	def get_midichannel(self):
		"""
		returns a Future

		get the value with .value
		"""
		out = self.get_future()
		self.osc_ask('/midichannel/get', out)
		return out

	def get_outport(self):
		out = self.get_future(post=lambda value:value.split("#"))
		self.osc_ask('/dataaddr/get', out)
		return out

	def get_digitalmapstr(self, sepindices=(3, 6, 9), sepstr=" "):
		"""
		Pedlbrd normalized digital inputs, so that a device at rest outputs 0
		get a string representation of the invertion mapping of the digital inputs

		sepindices: None or a list of indices where a separator will be included
		sepstr: the string used as a separator

		RETURNS
		=======

		a Future

		UNTOUCHES STATE   
		0                 -> NORMAL
		1                 -> INVERTED
		"""
		def postproc(s):
			if s is None: return
			if sepindices:
				out, now = [], 0
				for index in sepindices:
					out.append(s[now:index])
					now = index
				out.append(s[now:])
				s = sepstr.join(out)
			return s
		future = self.get_future()
		self.osc_ask('/digitalmapstr/get', lambda out: future(postproc(out)))
		return future
			
	def _get_reply_id(self):
		self._replyid += 1
		self._replyid %= 127
		return self._replyid

	def osc_ask(self, path, callback, *args):
		"""
		Expects a /reply

		>>> def show(s): print s
		>>> self.osc_ask('/ask/add', show, 3, 4)
		7
		"""
		ID = self._get_reply_id()
		self._reply_callbacks[ID] = callback
		self.oscserver.send(self.coreaddr, path, ID, *args)

	def setup_widgets(self):
		self.win = win = Tk()
		self.win.title('Pedlbrd')
		self.win.resizable(0, 0)
		self.win.tk.call('ttk::setTheme', "clam")
		#self.win.geometry('+200+100')

		defstyle('main.TFrame', background='white')
		self.root = root = ttk.Frame(self.win, style='main.TFrame')
		root.grid(column=0, row=0)

		# STYLE
		padx, pady = 4, 4
		ipadx, ipady = padx * 2, pady * 2

		color = {
			'bg': '#2095F0',
			'fg': '#FFFFFF',
			'active': '#00FF4b',
			'disabled': '#A4DCFF'
		}

		fonts = {
			'button'   : tkFont.Font(family='Abel', size=32),
			'label'    : tkFont.Font(family='Abel', size=32),
			'statusbar': tkFont.Font(family='Abel', size=14)
		}

		btn_style = defstyle('flat.TButton',
				font = fonts['button'],
				relief = 'flat',
				background = color['bg'],
				foreground = color['fg']
		)

		btn_style.map('flat.TButton',
			background=[('pressed', '!disabled', color['fg']), ('active', color['bg']), ('disabled', color['disabled'])],
			foreground=[('pressed', color['bg']), ('active', color['fg'])]
		)

		defstyle('label_static.TLabel',
			font       = fonts['label'],
			background = 'white',
			foreground = color['bg']
		)

		defstyle('statusbar.TLabel',
			font = fonts['statusbar'],
			background = 'white',
			foreground = '#CCCCCC'
		)

		defstyle('label_dynamic.TLabel',
			font       = fonts['label'],
			foreground = color['active'],
			background = color['fg'],
			padx=padx, pady=pady, ipadx=ipadx, ipady=ipady
		)

		defstyle('btnframe.TFrame',
			background='white'
		)

		# /////////////////////////////////////////////
		# WIDGETS
		# /////////////////////////////////////////////

		# STATUS //////////////////////////////////////

		status_frame = ttk.Frame(root, style='btnframe.TFrame').grid(
			column=0, row=0, columnspan=10, sticky='we', padx=padx*2, pady=pady*2
		)

		self.var_statusbar = StringVar(value="")
		ttk.Label(status_frame, textvariable=self.var_statusbar, style='statusbar.TLabel').grid(
			column=0, row=0, columnspan=6, sticky='w', padx=padx
		)

		ttk.Label(status_frame, text='STATUS', style='label_static.TLabel').grid(
			column=1, row=2, columnspan=1, rowspan=2, sticky='e' , padx=padx, pady=pady
		)

		self.var_connection = StringVar(value=self.connection)
		ttk.Label(status_frame, textvariable=self.var_connection, style='label_dynamic.TLabel').grid(
			column=2, row=2, columnspan=8, rowspan=2, sticky="wens" , padx=padx*2, pady=pady
		)

		# BUTTONS ///////////////////////////////////////

		frame_buttons = status_frame
		button_row = 4

		self.btn_reset = ttk.Button(frame_buttons, text='RESET', style='flat.TButton', command=self.click_reset)
		self.btn_reset.grid(column=0, row=button_row, columnspan=2, rowspan=2,
			                padx=padx, pady=pady, ipadx=ipadx, ipady=ipady, sticky="nswe")

		self.btn_ctrlpanel = ttk.Button(frame_buttons, text='CONTROL PANEL', style='flat.TButton', command=self.click_ctrl)
		self.btn_ctrlpanel.grid(column=2, row=button_row, columnspan=2, rowspan=1,
							    padx=padx, pady=pady, ipadx=ipadx, ipady=ipady, sticky="nswe")

		self.btn_monitor = ttk.Button(frame_buttons, text='MONITOR', style='flat.TButton', command=self.click_monitor)
		self.btn_monitor.grid(column=2, row=button_row+1, columnspan=2, padx=padx, pady=pady, ipadx=ipadx, ipady=ipady, sticky="nswe")

		self.btn_log = ttk.Button(frame_buttons, text='CONSOLE', style='flat.TButton', command=self.click_console)
		self.btn_log.grid(column=4, row=button_row, padx=padx, pady=pady, ipadx=ipadx, ipady=ipady, sticky="nswe")


		self.btn_quit = ttk.Button(frame_buttons, text='QUIT', style='flat.TButton', command=self.click_quit)
		self.btn_quit.grid(column=4, row=button_row+1, padx=padx, pady=pady, ipadx=ipadx, ipady=ipady, sticky="nswe")

		# ////////////////////////////////
		# Menu

		self.menu = Menu(tearoff=False)
		win.config(menu=self.menu)
		fm = self.file_menu = None
		fm = Menu(self.menu, tearoff=False)
		self.menu.add_cascade(label='File', menu=fm)
		# fm.add_command(label='Quit', command=self.quit)

		appmenu = Menu(self.menu, name='apple')
		self.menu.add_cascade(menu=appmenu)
		win.protocol('WM_DELETE_WINDOW', lambda *args:None)
		win.createcommand('tkAboutDialog', lambda *args:None)
		win.createcommand('exit', self.quit)

		#if sys.platform == 'darwin':
		#	win.createcommand('::tk::mac::ShowPreferences', self.dialog_config)

	def dialog_config(self):
		print "NO PREFERENCES!"

	def sendcore(self, path, *args):
		self.oscserver.send(self.coreaddr, path, *args)

	def click_quit(self):
		self.quit()

	def quit(self, quitcore=True):
		if self._quitting:
			return
		self._quitting = True
		if quitcore:
			self.sendcore('/quit')
		else:
			self.sendcore('/singout')
		time.sleep(0.1)
		self.win.quit()

	def click_reset(self):
		running = (self.connection == 'ACTIVE')
		if not running:
			print "Connection not ACTIVE, cannot RESET (connection={conn})".format(conn=self.connection)
			return
		self.btn_reset.configure(state='disabled')
		now = time.time()
		if (now - self._reset_lasttime) < self._reset_mintime:
			return
		self._reset_lasttime = now
		
		self.sendcore('/resetstate')
		self.sendcore('/calibrate') 
		self.win.after(300, self.statusbar_update)
		def enable(btn):
			btn.configure(state='active')
		self.win.after(int(self._reset_mintime * 1000), enable, self.btn_reset)
		
	def click_ctrl(self):
		print "NOT IMPLEMENTED"

	def click_console(self):
		self.sendcore('/dumpconfig')
		self.sendcore('/openlog', 0)
		self.sendcore('/openlog', 1)
		pedltalk_proc = self._subprocs.get('pedltalk')
		if pedltalk_proc is None or pedltalk_proc.poll() is not None:  # either first call, or subprocess finished
			pedltalkpath = os.path.abspath("pedltalk.py")
			p = subprocess.Popen(args=['osascript', 
				'-e', 'tell app "Terminal"', 
				'-e', 'do script "{python} {pedltalk}"'.format(python=sys.executable, pedltalk=pedltalkpath),
				'-e', 'end tell'])
			self._subprocs['pedltalk'] = p

	def click_monitor(self):
		self.open_monitor()

	def open_monitor(self):
		print "opening monitr..."
		if sys.platform == 'darwin':
			midi = subprocess.Popen(args=['open', '-a', 'MIDI Monitor'])
		oscmonpath = os.path.abspath('oscmonitor.py')
		oscproc = self._subprocs.get('osc')
		if os.path.exists(oscmonpath):
			if oscproc is None or oscproc.poll() is not None:  # either first call, or subprocess finished
				oscproc = subprocess.Popen(args=[sys.executable, oscmonpath])
		self._subprocs.update({'midi': midi, 'osc': oscproc})
		
	def heartbeat(self):
		self.set_status('ACTIVE')

	def set_status(self, status):
		if status == self.connection:
			return
		self.connection = status
		self.var_connection.set( status )	
		self.win.after(200, self.statusbar_update)

	def statusbar_update(self):
		if self.connection == 'ACTIVE':
			midich  = self.get_midichannel()
			digmap  = self.get_digitalmapstr()
			outport = self.get_outport()
		else:
			midich = Future()("?")
			digmap = Future()("??? ??? ???? ")
			outport = Future()("?")
		statusbar_separator = '   /   '
		statusbar_spaces = ' ' * len(statusbar_separator)
		def update(n=10, midich=midich, digmap=digmap, outport=outport):
			if n == 0:
				return
			if not( midich.ready and digmap.ready and outport.ready):
				self.win.after(100, update, n-1)
				return 
			else:
				chan = midich.value
				if isinstance(chan, int):
					chan += 1
				first_dataaddr = outport.value[0]
				if ":" in first_dataaddr:
					datah, datap = first_dataaddr.split(":")
					if datah == self.coreaddr.hostname:
						first_dataaddr = datap
				statusbar_text = 'MIDICHAN: {midich}{sep}OSC IP: {dev_ip}  IN: {dev_port}  OUT: {out_port}{sep}{digitalmap}{spaces}'.format(
					dev_ip    =self.coreaddr.hostname,
					dev_port  =self.coreaddr.port,
					sep       =statusbar_separator,
					spaces    =statusbar_spaces,
					midich    =chan,
					digitalmap=digmap.value,
					out_port  =first_dataaddr
				)
				self.var_statusbar.set(statusbar_text)
		self.win.after(100, update)

	@property
	def ip(self):
		if self._ip is not None:
			return self._ip
		self._ip = ip = get_ip()
		return ip

	def start(self):
		self.win.mainloop()

#######################
# helpers
#######################

def install_fonts():
	fonts = glob.glob("assets/fonts/*")
	installed = False
	for f in fonts:
		if sys.platform == 'darwin':
			dest = os.path.expanduser('~/Library/Fonts')
			if not os.path.exists(os.path.join(dest, f)):
				shutil.copy(f, dest)
				installed = True
		else:
			print "platform not supported, fonts will not be installed"
	if installed:
		time.sleep(0.5)

def defstyle(stylename, *args, **kws):
	style = ttk.Style()
	style.configure(stylename, *args, **kws)
	return style

def get_ip():
	import socket
	return socket.gethostbyname(socket.gethostname())

def as_liblo_address(addr):
	if isinstance(addr, liblo.Address):
		return addr
	elif isinstance(addr, tuple):
		return liblo.Address(*addr)
	else:
		raise TypeError("did not undersand addr")

##########################
# Futures
##########################
class UNSET(object): pass

class Future(object):
    __slots__ = ["_value", "_sleepfunc", "_sleeptime", "_post"]
    def __init__(self, sleepfunc=None, pollrate=50, post=None):
        """
        sleepfunc: a function to call when sleeping. sleepfunc(sleeptime_milliseconds)
        pollrate: how often to look for changes (in milliseconds)
        """
        self._value = UNSET
        self._sleepfunc = sleepfunc
        self._sleeptime = pollrate
        self._post = post
    def __call__(self, value):
    	if self._post:
    		value = self._post(value)
        self._value = value
        return self
    @property
    def ready(self):
        return self._value is not UNSET
    @property
    def value(self):
    	if self._sleepfunc is None:
            return self._value
        else:
            while True:
                if self.ready:
                    return self._value
                self._sleepfunc(self._sleeptime)


##########################
#          MAIN
##########################

if __name__ == '__main__':
	def usage():
		print """{progname} [--coreaddr [hostname:]port] [port=random]
		
		Example
		-------

		Create the gui connected to the default core address (localhost:47120)
		with at random osc port
		
		$ {progname}    

		Connect to a core with address 192.168.0.102:47120, and create a gui
		process listening at port 5678

		$ {progname} --coreaddr 192.168.0.102:47120 5678
		""".format(progname=sys.argv[0])
		sys.exit()

	if '--help' in sys.argv:
		usage()

	def argv_getoption(argv, option, default=None, remove=False, astype=None):
	    try:
	        index = argv.index(option)
	        try:
	            value = argv[index+1]
	            if value.startswith('-'):
	                raise ValueError("option %s had no value!" % option)
	            if remove:
	                argv.pop(index+1)
	                argv.pop(index)
	            if astype:
	                try:
	                    value = astype(value)
	                except ValueError:
	                    raise TypeError("could not interpret value %s as type given" % str(value))
	            return value
	        except IndexError:
	            raise ValueError('no value set for option %s' % option)
	    except ValueError:  # not in argv
	        return default
	
	coreaddr = argv_getoption(sys.argv, '--coreaddr', default="47120", remove=True)
	
	if len(sys.argv) == 1: # no port, will choose a random one
		oscport = None
	elif len(sys.argv) == 2:
		try:
			oscport = int(sys.argv[1])
		except ValueError: # could not convert to int
			print "The port must be an integer. Got: %s" % sys.argv[1]
	else:
		usage()

	if ":" in coreaddr:
		corehost, coreport = coreaddr.split(":")
		coreport = int(coreport)
	else:
		corehost = "127.0.0.1"
		coreport = int(coreaddr)

	assert (isinstance(oscport, int) and oscport > 0) or (oscport is None)

	print "Connecting to core at {corehost}:{coreport}".format(**locals())
	print "Listening to OSC at port {oscport}".format(oscport=oscport if oscport is not None else "UNSET")

	coreaddr = (corehost, coreport)
	prepare()
	start(coreaddr, oscport)