import os
import sys
import time
import shutil
import glob
import liblo
import Queue

from Tkinter import Tk, StringVar
import ttk
import tkFont

# TODO:
# * menu
# * very simple monitor and activity signal (we would need to register to data)

#######################
# API
#######################

def prepare():
	install_fonts()

def start(oscport):
	gui = GUI(oscport)
	gui.start() # block

######################
# GUI
######################

class GUI(object):
	def __init__(self, pedlbrd_oscport):
		self._connection = 'NOTCONNECTED'
		self._core_oscport = pedlbrd_oscport
		self.oscserver = self.make_oscserver(pedlbrd_oscport)
		self.setup_widgets()
		self.oscserver.start()
		self._reply_callbacks = {}
		self._replyid = 0
		self._ip = None

	def make_oscserver(self, pedlbrd_oscport):
		def heartbeat(path, args):
			self.heartbeat()
		def status_handler(path, args):
			self.set_status(args[0])
		def reply_handler(path, args):
			ID = args[0]
			callback = self._reply_callbacks[ID]
			if callback:
				try:
					callback(*args[1:])
				except:
					print "Error in reply callback:", sys.exc_info()[0]
		s = liblo.ServerThread()
		s.add_method('/heartbeat', None, heartbeat)
		s.add_method('/status', 's', status_handler)
		s.add_method('/reply', None, reply_handler)
		s.send(pedlbrd_oscport, '/registerui')
		return s

	def get_ref(self, pollrate=0.05):
		return Ref(self, pollrate)

	def get_midichannel(self, done=None):
		"""
		done: if None, we will block until answer is there.
			  if a callable, it will be called when the answer is ready.
		"""
		if done is not None:
			self.osc_ask('/ask/midichannel', done)
			return done
		else:
			self.osc_ask_sync('/ask/midichannel', None)[0]

	def get_digitalmapstr(self, done=None, sepindices=(3, 6), sepstr=" "):
		"""
		Pedlbrd normalized digital inputs, so that a device at rest outputs 0
		get a string representation of the invertion mapping of the digital inputs

		done: a callable for async, None for sync
		sepindices: None or a list of indices where a separator will be included
		sepstr: the string used as a separator

		UNTOUCHES STATE   
		0                 -> NORMAL
		1                 -> INVERTED
		"""
		def postproc(s):
			if s is not None:
				if sepindices:
					out = []
					now = 0
					for index in sepindices:
						out.append(s[now:index])
						now = index
					out.append(s[now:])
					s = sepstr.join(out)
				return s
		if done is None:
			reply = self.osc_ask_sync('/ask/digitalmapstr')[0]
			return postproc(reply)
		else:
			self.osc_ask('/ask/digitalmapstr', lambda out: done(postproc(out)))
			
	def _get_reply_id(self):
		self._replyid += 1
		self._replyid %= 127
		return self._replyid

	def osc_ask(self, path, callback, *args):
		ID = self._get_reply_id()
		self._reply_callbacks[ID] = callback
		self.oscserver.send(self._core_oscport, path, ID, *args)

	def osc_ask_sync(self, path, done_callback=None, timeout=1):
		queue = Queue.Queue()
		def callback(*args):
			if done_callback:
				done_callback(*args)
			queue.put(args)
		self.osc_ask(path, callback)
		if done_callback is None:
			try:
				out = queue.get(timeout=timeout)
			except Queue.Empty:
				return None
			return out

	def setup_widgets(self):
		self.win = Tk()
		self.win.title('Pedlbrd')
		self.win.resizable(0, 0)
		self.win.tk.call('ttk::setTheme', "clam")

		defstyle('main.TFrame', background='white')
		self.root = root = ttk.Frame(self.win, style='main.TFrame')
		root.grid(column=0, row=0)

		# STYLE
		padx, pady = 4, 4
		ipadx, ipady = padx * 2, pady * 2

		color = {
			'bg': '#2095F0',
			'fg': '#FFFFFF',
			'active': '#00FF4b'
		}

		fonts = {
			'button'   : tkFont.Font(family='Abel', size=36),
			'label'    : tkFont.Font(family='Abel', size=36),
			'statusbar': tkFont.Font(family='Courier', size=16)
		}

		btn_style = defstyle('flat.TButton',
				font = fonts['button'],
				relief = 'flat',
				background = color['bg'],
				foreground = color['fg']
		)

		btn_style.map('flat.TButton',
			background=[('pressed', '!disabled', color['fg']), ('active', color['bg'])],
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
			foreground = '#DDDDDD'
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
			column=0, row=2, columnspan=2, rowspan=2, sticky='w' , padx=padx, pady=pady
		)

		self.var_connection = StringVar(value='NOCONNECTION')
		ttk.Label(status_frame, textvariable=self.var_connection, style='label_dynamic.TLabel').grid(
			column=2, row=2, columnspan=8, rowspan=2, sticky="wens" , padx=padx, pady=pady
		)

		# BUTTONS ///////////////////////////////////////

		frame_buttons = status_frame
		button_row = 4

		self.btn_reset = ttk.Button(frame_buttons, text='RESET', style='flat.TButton', command=self.click_reset).grid(
			column=0, row=button_row, columnspan=2, rowspan=2,
			padx=padx, pady=pady, ipadx=ipadx, ipady=ipady, sticky="nswe"
		)

		self.btn_ctrlpanel = ttk.Button(frame_buttons, text='CONTROL PANEL', style='flat.TButton', command=self.click_ctrl).grid(
			column=2, row=button_row, columnspan=2, rowspan=1,
			padx=padx, pady=pady, ipadx=ipadx, ipady=ipady, sticky="nswe"
		)

		self.btn_monitor = ttk.Button(frame_buttons, text='MONITOR', style='flat.TButton', command=self.click_monitor).grid(
			column=2, row=button_row+1, columnspan=2, padx=padx, pady=pady, ipadx=ipadx, ipady=ipady, sticky="nswe"
		)


		self.btn_log = ttk.Button(frame_buttons, text='CONSOLE', style='flat.TButton', command=self.click_console).grid(
			column=4, row=button_row, padx=padx, pady=pady, ipadx=ipadx, ipady=ipady, sticky="nswe"
		)


		self.btn_quit = ttk.Button(frame_buttons, text='QUIT', style='flat.TButton', command=self.click_quit).grid(
			column=4, row=button_row+1, padx=padx, pady=pady, ipadx=ipadx, ipady=ipady, sticky="nswe"
		)

	def sendtocore(self, path, *args):
		self.oscserver.send(self._core_oscport, path, *args)

	def click_quit(self):
		self.sendtocore('/stop')
		self.oscserver.stop()
		self.win.after(200, lambda:
			self.oscserver.free() or
			self.win.quit()
		)
		
	def click_reset(self):
		running = self._connection == 'ACTIVE'
		if not running:
			print "Connection not ACTIVE, cannot RESET"
			return
		self.sendtocore('/resetstate')
		self.win.after(200, lambda:
			self.sendtocore('/calibrate') or 
			self.statusbar_update()
		)
		
	def click_ctrl(self):
		print "NOT IMPLEMENTED"

	def click_console(self):
		self.sendtocore('/dumpconfig')
		self.sendtocore('/openlog', 0)

	def click_monitor(self):
		open_monitor()

	def heartbeat(self):
		self.set_status('ACTIVE')

	def set_status(self, status):
		status = status.upper()
		oldstatus = self._connection
		if status == oldstatus:
			return
		self._connection = status
		self.var_connection.set( status )	
		if status == 'ACTIVE':
			self.win.after(200, self.statusbar_update)

	def statusbar_update(self):
		midich, digmap = Ref(), Ref()
		self.get_midichannel(midich)
		self.get_digitalmapstr(digmap)
		statusbar_separator = '      '
		def update():
			if not( midich.ready and digmap.ready ):
				self.win.after(100, update)
			else:
				statusbar_text = 'MIDI CHANNEL (1-16): {midich}{sep}OSC: {dev_ip}//{dev_port}{sep}{digitalmap}'.format(
					dev_ip=self.ip,
					dev_port=self._core_oscport,
					sep=statusbar_separator,
					midich=midich.value+1,
					digitalmap=digmap.value
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

def open_monitor():
	if sys.platform == 'darwin':
		os.system("open -a 'MIDI Monitor'")

class UNSET(object): pass

class Ref(object):
	__slots__ = ["_value", "_root", "_sleeptime"]
	def __init__(self, root=None, pollrate=0.05):
		"""
		root: the root window
		pollrate: how often to look for changes (in seconds)
		"""
		self._value = UNSET
		self._root  = root
		self._sleeptime = pollrate * 1000
	def __call__(self, value):
		self._value = value
	@property
	def ready(self):
		return self._value is not UNSET
	@property
	def value(self):
		if self._root is None:
			return self._value
		else:
			while True:
				if self.ready:
					return self._value
				self._root.after(self._sleeptime)

##########################
#          MAIN
##########################

if __name__ == '__main__':
	oscport = sys.argv[1] if len(sys.argv) > 1 else 47120
	prepare()
	start(oscport)
