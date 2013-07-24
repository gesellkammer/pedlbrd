import os
import sys
import time
import shutil
import glob
import liblo

from Tkinter import Tk, StringVar
import ttk
import tkFont

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


#######################
# API
#######################

def prepare():
	install_fonts()

def start(oscport=47120):
	gui = GUI(oscport)
	gui.start() # block

#######################
# helpers
#######################

def defstyle(stylename, *args, **kws):
	style = ttk.Style()
	style.configure(stylename, *args, **kws)
	return style

def get_ip():
	import socket
	return socket.gethostbyname(socket.gethostname())

######################
# GUI
######################

class GUI(object):
	def __init__(self, pedlbrd_oscport):
		self._status = 'NOT CONNECTED'
		self._pedlbrd_oscport = pedlbrd_oscport
		self.oscserver = self.make_oscserver(pedlbrd_oscport)
		self.setup_widgets()
		self.oscserver.start()

	def make_oscserver(self, pedlbrd_oscport):
		s = liblo.ServerThread()
		def heartbeat(path, args):
			self.heartbeat()
		def status_handler(path, args):
			self.set_status(args[0])
		s.add_method('/heartbeat', None, heartbeat)
		s.add_method('/status', 's', status_handler)
		s.send(pedlbrd_oscport, '/registerui')
		return s

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
			'statusbar': tkFont.Font(family='Abel', size=24)
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

		defstyle('faint.TSeparator',
			background = '#FEFEFE',
			foreground = '#FEFEFE'
		)

		# /////////////////////////////////////////////
		# WIDGETS
		# /////////////////////////////////////////////

		# STATUS //////////////////////////////////////

		status_frame = ttk.Frame(root, style='btnframe.TFrame')
		status_frame.grid(column=0, row=0, columnspan=10, sticky='we', padx=padx*2, pady=pady*2)

		ttk.Label(status_frame, text='%s//%d ' % (str(get_ip()), self.oscserver.port), style='statusbar.TLabel').grid(
			column=0, row=0, columnspan=2, sticky='w', padx=padx
		)

		ttk.Label(status_frame, text='STATUS', style='label_static.TLabel').grid(
			column=0, row=2, columnspan=2, rowspan=2, sticky='w' , padx=padx, pady=pady
		)

		self.statusvar = StringVar()
		self.statusvar.set('NOCONNECTION')
		ttk.Label(status_frame, textvariable=self.statusvar, style='label_dynamic.TLabel').grid(
			column=2, row=2, columnspan=8, rowspan=2, sticky="wens" , padx=padx, pady=pady
		)

		# BUTTONS ///////////////////////////////////////

		# -- Frame
		#frame_buttons = ttk.Frame(status_frame, style='btnframe.TFrame')
		#frame_buttons.grid(column=0, row=2, sticky='nsew', padx=padx*2, pady=pady*2)
		frame_buttons = status_frame

		# -- Buttons
		self.btn_reset = ttk.Button(frame_buttons, text='RESET', style='flat.TButton', command=self.click_reset).grid(
			column=0, row=4, columnspan=2, rowspan=2,
			padx=padx, pady=pady, ipadx=ipadx, ipady=ipady, sticky="nswe"
		)

		self.btn_ctrlpanel = ttk.Button(frame_buttons, text='CONTROL PANEL', style='flat.TButton', command=self.click_ctrl).grid(
			column=2, row=4, columnspan=2, rowspan=2,
			padx=padx, pady=pady, ipadx=ipadx, ipady=ipady, sticky="nswe"
		)

		self.btn_log = ttk.Button(frame_buttons, text='CONSOLE', style='flat.TButton', command=self.click_console).grid(
			column=4, row=4, padx=padx, pady=pady, ipadx=ipadx, ipady=ipady, sticky="nswe"
		)
		self.btn_quit = ttk.Button(frame_buttons, text='QUIT', style='flat.TButton', command=self.click_quit).grid(
			column=4, row=5, padx=padx, pady=pady, ipadx=ipadx, ipady=ipady, sticky="nswe"
		)

	def click_quit(self):
		self.oscserver.stop()
		time.sleep(0.3)
		self.oscserver.free()
		self.win.quit()

	def click_reset(self):
		s = self.oscserver
		port = self._pedlbrd_oscport
		s.send(port, '/resetstate')
		time.sleep(0.1)
		s.send(port, '/calibrate')

	def click_ctrl(self):
		print "NOT IMPLEMENTED"

	def click_console(self):
		self.oscserver.send(self._pedlbrd_oscport, '/openlog', 0)

	def heartbeat(self):
		self.set_status('ACTIVE')

	def set_status(self, status):
		status = status.upper()
		if status == self._status:
			return
		self._status = status
		self.statusvar.set( status )

	def start(self):
		self.win.mainloop()

