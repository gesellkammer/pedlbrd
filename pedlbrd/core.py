#!/usr/bin/env python
from __future__ import division
import serial
import glob
import os
import sys
import rtmidi2 as rtmidi
import json
import time
import liblo
from liblo import send as oscsend
import logging
import timer2
import shutil

from .config import *
import util
import envir

"""
PROTOCOL 

4 bytes

1: HEADER -- 10000000 + CMD
   where CMD can be:
       - D: digital pin
       - A: analog pin
       - H: heart beat
2: PARAM -- value between 0-127
3: VALUE HIGH
4: VALUE LOW

VALUE = VALUE_HIGH * 128 + VALUE_LOW
"""

#################################
# Init
#################################
envir.prepare()
_scheduler = timer2.Timer(precision=0.2)

################################
# 
# Logging
# 
################################
_logname = 'PEDLBRD'
_logfile = os.path.join(envir.configpath(), "%s.log" % _logname)
logging.basicConfig(level=logging.DEBUG, filename=_logfile)
_log = logging.getLogger(_logname)

#################################
# CONSTANTS & SETUP
#################################
DEBUG = False
BAUDRATE = 57600
CC = 176

###############################
# Helper functions
###############################

def _parsemsg(msg):
	param = ord(msg[0])
	value = ord(msg[1])*128 + ord(msg[2])
	return param, value

def _aspin(pin):
	"""
	pin is either a string like D2, or a tuple ("D", 2)

	returns a tuple (kind, pin)
	"""
	if isinstance(pin, basestring):
		kind = pin[0]
		pin = int(pin[1:])
		return kind, pin
	elif isinstance(pin, tuple):
		return pin
	else:
		raise ValueError("pin should be either a string or a tuple")

class DeviceNotFound(BaseException):
	pass

def _is_heartbeat_present(port):
	"""
	return True if the given serial port is transmitting a heartbeat
	"""
	timeout = 0.333333
	max_time = 20 # wait at the most this time while attempting to connect
	s = serial.Serial(port, baudrate=BAUDRATE, timeout=timeout)
	H = ord("H")
	time0 = time.time()
	while True:
		now = time.time()
		b = s.read(1)
		if len(b):
			b = ord(b)
			if b & 0b10000000:
				command = b & 0b01111111
				if command == H:
					return True
		if (now - time0) > max_time:
			break
	return False

def _jsondump(obj, filename):
	json.dump(obj, open(filename, 'w'), indent=4, sort_keys=True)

def _schedule_regularly(period, function, args=(), kws={}):
	return _scheduler.apply_interval(period*1000, function, args, kws)

def _call_later(deltatime, function, args=(), kws={}):
	return _scheduler.apply_after(deltatime*1000, function, args, kws)

def _add_suffix(p, suffix):
	name, ext = os.path.splitext(p)
	return "".join((name, suffix, ext))

################################
#
#             API
#
################################

def detect_port():
	possible_ports = envir.possible_ports()
	if not possible_ports:
		return None
	_debug("possible ports: %s" % str(possible_ports))
	for port in possible_ports:
		if _is_heartbeat_present(port):
			return port
	return None

# -----------------------------------------------------------------------

class Configuration(dict):
	__slots__ = "callback _modified _label2pin _pin2label".split()
	def __init__(self, config, overrides=None, callback=None,):
		"""
		config: a dictionary
		callback: a function to be called each time the configuration is changed
		overrides: a dictionary that overrides config
		"""
		assert isinstance(config, dict)
		if overrides:
			config.update(overrides)
		self.callback = callback if callback is not None else self._default_callback
		self.update(config)
		self._modified = False
		self._label2pin, self._pin2label = self._get_input_pin_mapping()
		
	def label2pin(self, label):
		return self._label2pin.get(label)

	def pin2label(self, kind, pin):
		return self._pin2label.get((kind, pin))

	def _get_input_pin_mapping(self):
		label2pin = {label:_aspin(inputdef['pin']) for label, inputdef in self['input_definition'].iteritems()}
		pin2label = {pin:label for label, pin in label2pin.iteritems()}
		return label2pin, pin2label
		
	def _get_pins(self):
		pins = []
		for label, definition in self['input_definition'].iteritems():
			pins.append(definition['pin'])
		self._pins = pins
		return pins

	def _get_labels(self):
		return self['input_definition'].keys()

	def _default_callback(self, key, newvalue):
		_debug("config modified: %s=%s" % (key, newvalue))
		self._modified = True

	def getpath(self, path):
		if isinstance(path, basestring):
			if "/" in path:
				keys = path.split("/")
			else:
				keys = [path]
		else:
			return self[path]
		d = self
		for key in keys:
			v = d.get(key)
			if isinstance(v, dict):
				d = v
		return v

	def set(self, path, value):
		"""
		path: a key or a key path like 'key1/key2/...'
		      also possible: a list of keys, [key1, key2, ...]

		value: the new value
		"""
		if isinstance(path, basestring):
			keys = path.split('/') if isinstance(path, basestring) else path
		elif isinstance(path, (tuple, list)):
			keys = path
		else:
			raise ValueError("the path must be a string of the type key1/key2/... or a seq [key1, key2, ...]")
		d = self
		if len(keys) == 0:
			self[path] = value
			self.callback(path, value)
		else:
			for key in keys[:-1]:
				v = d.get(key)
				if isinstance(v, dict):
					d = v
				else:
					raise KeyError("set -- key not found: %s" % key)
			d[keys[-1]] = value
			self.callback(path, value)

	def midi_mapping_for_label(self, label):
		# return self['midi_mapping'].get(label)
		return self['input_mapping'].get(label).get('midi')

	


# --------------------------------------------------------------

class MIDI_Mapping(object):
	def __init__(self, configuration):
		"""
		configuration: a Configuration
		"""
		self.config = configuration
		self._analog_lastvalues = [0 for i in range(self.config['num_analog_pins'])]

	def construct_func(self, label):
		if label[0] == 'D':
			return self.construct_digital_func(label)
		return self.construct_analog_func(label)
	
	def construct_digital_func(self, label):
		mapping = self.config.midi_mapping_for_label(label)
		inverted = self.config['input_mapping'][label]['inverted']
		kind, pin = self.config.label2pin(label)
		if not mapping:
			return None
		byte1 = CC + mapping['channel']
		cc = mapping['cc']
		_, out1 = mapping['output']
		if not inverted:
			func = lambda x: (byte1, cc, x*out1)
		else:
			func = lambda x: (byte1, cc, (1 - x)*out1)
		if DEBUG:
			def debugfunc(x):
				msg = func(x)
				print "D%d: %d -> %s" % (pin, x, str(msg))
				return msg
			return debugfunc
		return func

	def construct_analog_func(self, label):
		mapping = self.config.midi_mapping_for_label(label)
		kind, pin = self.config.label2pin(label)
		lastvalues = self._analog_lastvalues
		if not mapping:
			return None
		byte1 = CC + mapping['channel']
		cc = mapping['cc']
		in0, in1 = mapping['input']
		out0, out1 = mapping['output']
		in_diff = in1 - in0
		out_diff = out1 - out0
		def func(x): 	# a func should return either a msg or None
			delta = (x - in0) / in_diff
			value = int(delta * out_diff + out0 + 0.5)
			lastvalue = lastvalues[pin]
			if value == lastvalue:
				return None
			lastvalues[pin] = value
			return (byte1, cc, value)
		if DEBUG:
			def debugfunc(x):
				msg = func(x)
				print "A", pin, x, msg
				return msg
			return debugfunc
		return func

# -----------------------------------------------------------------------------------------------------

class OSC_Server(liblo.ServerThread):
	def __init__(self, port):
		liblo.ServerThread.__init__(self, port)

# -----------------------------------------------------------------------------------------------------

def _envpath(name):
	"""
	returns the full path (folder/name) of the env.json file
	NB: it does not check that it exists
	"""
	if name is None:
		name = DEFAULTS['envname']
	base = os.path.split(name)[1]
	base = "%s.json" % os.path.splitext(base)[0]
	envpath = envir.configpath()
	return os.path.join(envpath, base)

class Pedlbrd(object):
	def __init__(self, config=None, env=None, restore_session=None, **kws):
		"""
		config: the name of the configuration file or None to use the default
		"""
		self.env = self._load_env(env)
		restore_session = restore_session if restore_session is not None else self.env['restore_session']

		self.config, self.configfile = self._load_config(config, kws, restore_session=restore_session)
		self.reset_state()

		self._running = False
		self._paused = False
		self._midiout = None
		self._serial_timeout = 0.2
		self._dispatch_funcs_by_pin = {}
		self._echo = False
		self._handlers = {}
		self._serialconnection = None
		self._oscserver = None
		self._oscapi = None

		def as_address(addr):
			if isinstance(addr, (tuple, list)):
				return liblo.Address(*addr)
			else:
				return liblo.Address(addr)
		self._osc_report_addresses = [as_address(addr) for addr in self.config['osc_report_addresses']]

		_call_later(3, self._show_configuration_info)
		self._prepare_connection()
		if self.config['autostart']:
			self.start()

	def _show_configuration_info(self):
		if self.config == DEFAULT_CONFIG:
			_info("using default configuration")
		if self.configfile is not None:
			found, configfile_fullpath = envir.config_find(self.configfile)
			if found:
				_info("using config file: %s" % configfile_fullpath)
			else:
				_info("cloned default config with name: %s (will be saved to %s" % (self.configfile, configfile_fullpath))
		self._report_config()

	def _report_config(self):
		d = self.config['input_mapping']
		dl = list(d.iteritems())
		dl = util.sort_natural(dl, key=lambda row:row[0])
		lines = ["", "", "LABEL    MAPPING", "----------------------------"]
		col2 = 8
		for label, mapping in dl:
			midi = mapping['midi']
			if label[0] == "D":
				inverted  = "INVERTED" if mapping['inverted'] else ""
				out0, out1 = midi['output']
				l = "%s    | %s  CH %2d  CC %3d                  (%3d - %3d)" % (label.ljust(3), inverted.ljust(col2), 
					midi['channel'], midi['cc'], out0, out1)
			else:
				normalize = "NORM" if mapping['normalized'] else ""
				if mapping['normalized']:
					normalize = "NORM"
					kind, pin = self.label2pin(label)
					maxvalue = "MAX %d" % self._analog_maxvalues[pin]
				else:
					normalize = ""
					maxvalue = ""
				in0, in1 = midi['input']
				out0, out1 = midi['output']

				l = "%s    | %s  CH %2d  CC %3d  (%3d - %4d) -> (%3d - %3d)  %s" % (label.ljust(3), normalize.ljust(col2),
					midi['channel'], midi['cc'], in0, in1, out0, out1, maxvalue)
			lines.append(l)
		lines.append("\n\n")
		s = "\n".join(lines)
		_info( s )

	def open_config(self, configfile):
		found, configfile = envir.config_find(configfile)
		if found:
			self.config, self.configfile = self._load_config(configfile)
		self.reset_state()

	def _load_config(self, config=None, overrides=None, restore_session=False):
		if config is None:
			config = DEFAULT_CONFIG
			if restore_session:
				last_saved_config = self.env.get('last_saved_config')
				if last_saved_config and os.path.exists(last_saved_config):
					config = last_saved_config
		if isinstance(config, dict):
			configdict = config
			configfile = None
		elif isinstance(config, basestring):
			configdict = envir.config_load(config)
			if configdict:
				_, abspath = envir.config_find(config)
				configfile = abspath
				shutil.copy(configfile, _add_suffix(configfile, '--orig'))
			else:
				# configuration file not found. use it as a name, load a default
				configfile = config
				configdict = DEFAULT_CONFIG
		else:
			raise TypeError("config must be either a dict or a string (or None to use default), got %s" % str(type(config)))

		assert isinstance(configdict, dict) and (configfile is None or isinstance(configfile, basestring))
		configuration = Configuration(configdict, overrides=overrides, callback=self._configchanged_callback)
		return configuration, configfile

	def _load_env(self, name):
		self._envname = name
		envpath = _envpath(name)
		# if it doesn't exist, we first save it
		if not os.path.exists(envpath):
			env = DEFAULT_ENV
			_jsondump(env, envpath)
		else:
			env = json.load(open(envpath))
		env['last_loaded_env'] = envpath
		return env

	def _save_env(self):
		envpath = _envpath(self._envname)
		_jsondump(self.env, envpath)
		self.env['last_saved_env'] = envpath
		_debug("saved env to " + envpath)

	def pin2label(self, kind, pin):
		return self.config.pin2label(kind, pin)

	def label2pin(self, label):
		"""
		returns a tuple (kind, pin)
		"""
		return self.config.label2pin(label)

	def config_restore_defaults(self):
		self.config = DEFAULT_CONFIG
		self.configfile = None

	def get_last_saved(self, skip_autosave=True):
		configfolder = envir.configpath()
		saved = [f for f in glob.glob(os.path.join(configfolder, '*.json')) if not f.startswith('_')]
		if saved and skip_autosave:
			saved = [f for f in saved if 'autosaved' not in f]
		if saved:
			lastsaved = sorted([(os.stat(f).st_ctime, f) for f in saved])[-1][1]
			return lastsaved
		return None

	@property 
	def echo(self): return self._echo

	@echo.setter
	def echo(self, value):
		self._echo = value
		self._update_dispatch_funcs()

	def __del__(self):
		self.stop()

	def _prepare_connection(self):
		retry_period = self.config['firsttime_retry_period']
		serialport = self.find_device(retry_period=retry_period)
		if not serialport:
			raise DeviceNotFound("A Pedlbrd could not be found in the system. Make sure it is connected")
		self._serialport = serialport

		_banner("Pedlbrd found. Using port: %s" % os.path.split(serialport)[1], border_char='*', 
			linesafter=2, linesbefore=10, margin_horiz=2)

	def reset_state(self):
		self._midi_mapping = MIDI_Mapping(self.config)
		self._analog_maxvalues = [0 for i in range(127)]
		self._input_labels = self.config['input_mapping'].keys()

	def find_device(self, retry_period=0):
		"""
		find the path of the serial device. check that it is alive
		if given, retry repeatedly until the device is found 

		Returns: the path of the serial device.
		"""
		while True:
			port = detect_port()
			if not port:
				if not retry_period:
					return None
				else:
					_info('Device not found, retrying in %d seconds' % retry_period)
					time.sleep(retry_period)
			else:
				return port

	@property
	def serialport(self):
		if self._serialport:
			return self._serialport
		port = detect_port()
		self._serialport = port
		return port

	def _create_dispatch_func(self, label):
		"""
		returns a list of functions operating on the pin corresponding to 
		the given label
		"""
		funcs = []
		if self.echo:
			def preecho(value, label=label):
				print "PRE: %s -> %d" % (label, value)
				return value
			funcs.append(preecho)
		# normalization step if asked for
		if label[0] == "A" and self.config['input_mapping'][label]['normalized']:
			kind, pin = self.label2pin(label)
			def normalize(value, pin=pin):
				return self._analog_normalize(pin, value)
			funcs.append(normalize)
		midifunc = self._midi_mapping.construct_func(label)
		midiout = self._midiout
		if midifunc:
			def midifunc2(value, func=midifunc):
				msg = midifunc(value)
				if msg:
					midiout.send_message(msg)
				return value
			funcs.append(midifunc2)
		return funcs

	def _update_dispatch_funcs(self):
		funcs = {self.label2pin(label):self._create_dispatch_func(label) for label in self._input_labels}
		if self._running and not self._paused:
			self._paused = True
			time.sleep(0.01)
			self._dispatch_funcs_by_pin.update(funcs)
			self._paused = False
		else:
			self._dispatch_funcs_by_pin.update(funcs)

	def _input_changed(self, label):
		pin = self.label2pin(label)
		self._dispatch_funcs_by_pin[pin] = self._create_dispatch_func(label)

	def _update_handlers(self):
		for handler in self._handlers.items():
			handler.cancel()
		self._handlers = {
		 	'save_env'   : _schedule_regularly(11, self._save_env)
		}
		if self.env.setdefault('autosave_config', True):
			self._handlers['save_config'] = _schedule_regularly(7, self._save_config, kws={'autosave':False})

	def start(self, async=None):
		"""
		start communication (listen to device, output to midi and/or osc, etc)

		async: if True, do everything non-blocking
		       if None, use the settings in the config ('serialloop_async')
		"""
		if async is None:
			async = self.config.get('serialloop_async', False)

		if async:
			import threading
			self._thread = th = threading.Thread(target=self.start, kwargs={'async':False})
			th.daemon = True
			th.start()
			return

		self._midi_turnon()
		assert self._midiout is not None
		self._update_dispatch_funcs()
		self._update_handlers()

		self._oscserver, self._oscapi = self._create_oscserver()
		self._oscserver.start()

		midiout           = self._midiout
		midi_mapping      = self._midi_mapping

		self._running = True
		_banner("listening!")
		dlabels = [self.pin2label("D", i) for i in range(self.config['num_digital_pins'])]
		alabels = [self.pin2label("A", i) for i in range(self.config['num_analog_pins'])]
		while self._running:
			try:
				self._serialconnection = s = serial.Serial(self.serialport, baudrate=BAUDRATE, timeout=self._serial_timeout)
				last_heartbeat = time.time()
				connected = True
				while self._running:
					if self._paused:
						_debug("paused...")
						while self._paused:
							time.sleep(0.2)
						_debug("resumed...")
					now = time.time()
					b = s.read(1)
					if len(b):    # if we dont time out, we are here
						b = ord(b)
						if b & 0b10000000:  # got a message, read the next bytes according to which message
							cmd = b & 0b01111111
							# -------------
							#    DIGITAL
							# -------------
							if cmd == 68:	# --> D(igital)
								msg = s.read(3)
								param, value = _parsemsg(msg)
								funclist = self._dispatch_funcs_by_pin.get(("D", param))
								self._oscreport('/raw', dlabels[param], value)
								if funclist:
									for func in funclist:
										value = func(value)
							# -------------
							#   ANALOG
							# -------------
							elif cmd == 65: # --> A(nalog)
								msg = s.read(3)
								param, value = _parsemsg(msg)
								funclist = self._dispatch_funcs_by_pin.get(('A', param))
								if funclist:
									for func in funclist:
										value = func(value)
							# -------------
							#   HEARTBEAT
							# -------------
							elif cmd == 72: # --> H(eartbeat)
								ID = ord(s.read(1))
								last_heartbeat = now
								if not connected:
									self._notify_connected()
								connected = True
					if (now - last_heartbeat) > 10:
						connected = False
						print "HERE"
						self._notify_disconnected()
			except KeyboardInterrupt:
				self.stop()
				break
			except OSError:   # arduino disconnected -> s.read throws device not configured
				if not self._reconnect():
					self.stop()
					break
			except serial.SerialException:
				if not self._reconnect():
					self.stop()
					break

	def _oscreport(self, path, *data):
		for address in self._osc_report_addresses:
			oscsend(address, path, *data)

	def save_config(self, newname=None):
		"""
		save the current configuration

		Arguments
		=========

		newname: like "save as", the newname is used for next savings

		If a config file was used, it will be saved to this name unless
		a new name is given.

		If a default config was used ( Pedlbrd(config=None) ), then a default
		name will be used. 

		Returns
		=======

		the full path where the config file was saved
		"""
		return self._save_config(newname)

	def _save_config(self, newname=None, autosave=False):
		used_configfile = self.configfile
		defaultname = 'untitled' if self.config != DEFAULT_CONFIG else 'default'
		configfile = next(f for f in (newname, used_configfile, defaultname) if f is not None)
		assert configfile is not None
		found, abspath = envir.config_find(configfile)
		if autosave:
			saved_path = _add_suffix(abspath, '--autosaved')
		else:
			saved_path = abspath
		_jsondump(self.config, saved_path)
		self.configfile = abspath
		self.env['last_saved_config'] = abspath
		_debug('saving to ' + abspath)

	def edit_config(self):
		if self.configfile and os.path.exists(self.configfile):
			_json_editor(self.configfile)
			self.open_config(self.configfile)
			self._report_config()
		else:
			_error("could not find a config file to edit")
	
	def stop(self):
		if not self._running:
			_info("already stopped!")
			return
		_banner("stopping...", margin_vert=1)
		self._running = False
		time.sleep(self._serial_timeout)
		if self._serialconnection:
			self._serialconnection.close()
		self._midi_turnoff()
		self._oscserver.stop()
		self._oscserver.free()
		for handlername, handler in self._handlers.iteritems():
			_debug('cancelling %s' % handlername)
			handler.cancel()
		if self.env.get('autosave_config', True):
			self._save_config()
		self._save_env()
		
	## ----------------------------------------------------------------------------
	## INTERNAL

	def _analog_normalize(self, pin, value):
		"""pin here refers to the underlying arduino pin"""
		maxvalue = self._analog_maxvalues[pin]
		if value > maxvalue:
			self._analog_maxvalues[pin] = value
			value2 = 1023
		else:
			value2 = (value / maxvalue) * 1023
		return value2

	def _midi_turnon(self):
		if self._midiout is not None:
			return
		midiout = rtmidi.MidiOut()
		midiout.open_virtual_port(self.config['midi_device_name'])
		self._midiout = midiout
		
	def _midi_turnoff(self):
		if self._midiout is not None:
			self._midiout.close_port()
			self._midiout = None

	def _notify_disconnected(self):
		msg = "DISCONNECTED!"
		_info(msg)

	def _notify_connected(self):
		msg = "CONNECTED!"
		_info(msg)

	def _reconnect(self):
		"""
		attempt to reconnect

		True if successful, False if no reconnection possible
		"""
		reconnect_period = self.config['reconnect_period_seconds']
		if not reconnect_period:
			self.stop()
			out = False
		else:
			self._notify_disconnected()
			while True:
				try:
					port = detect_port()
					if port:
						self._serialport = port
						out = True
						break
					else:
						time.sleep(reconnect_period)
				except KeyboardInterrupt:
					out = False
					break
		if out:
			self._notify_connected()
		return out

	def _configchanged_callback(self, key, value):
		_debug('changing config %s=%s' % (key, str(value)))
		paths = key.split("/")
		if paths[0] == 'input_mapping':
			label = paths[1]
			self._input_changed(label)
			self._report_config()

	# -------------------------------------------
	# External API
	def cmd_digitalinvert(self, label, value):
		""" 
		label = D1, D2, etc
		1=inverted, 0=normal 
		"""
		if label[0] != "D":
			_error("label '%s' not a digital input! bypassing" % label)
			return 
		path = "input_mapping/%s/inverted" % label
		value = bool(value)
		self.config.set(path, value)

	def cmd_midichannel(self, label, channel):
		path = 'input_mapping/%s/midi/channel' % label
		if not 0 <= channel < 16:
			_error("midi channel should be between 0 and 15, got %d" % channel)
			return 
		self.config.set(path, channel)

	def cmd_midicc(self, label, cc):
		path = 'input_mapping/%s/midi/cc' % label
		if not 0 <= cc < 128:
			_error("midi CC should be between 0 and 127, got %d" % cc)
			return 
		self.config.set(path, cc)

	def _create_oscserver(self):
		cmds = [(a, getattr(self, a)) for a in dir(self) if a.startswith('cmd_')]
		s = liblo.ServerThread(self.config['osc_port'])
		osc_commands = {}
		for cmdname, method in cmds:
			path = "/%s" % cmdname[4:]
			func = _func2osc(method)
			s.add_method(path, "si", func)
			osc_commands[path] = func
		return s, osc_commands

def _func2osc(func):
	def wrap(path, args, types, src):
		func(*args)
	return wrap

##########################################
# Printing and Logging
##########################################

def _makemsg(msg, border=True, margin_horiz=4, margin_vert=0, linesbefore=1, linesafter=0, 
	        border_char="#", align='center', align_width=60, prompt=""):
	lines = []
	if not border:
		border_char = ""
	mainline = "".join([ border_char, " "*margin_horiz, prompt, msg, " "*margin_horiz, border_char ])
	for i in range(linesbefore):
		lines.append("")
	border_line = border_char * len(mainline)
	if border:
		lines.append(border_line)
	vert = "".join([ border_char, " " * (len(border_line)-2), border_char ])
	for l in range(margin_vert):
		lines.append(vert)
	lines.append(mainline)
	for l in range(margin_vert):
		lines.append(vert)
	if border:
		lines.append(border_line)
	for i in range(linesafter):
		lines.append("")
	if align == 'center':
		lines = [line.center(align_width) for line in lines]
	out = "\n".join(lines)
	return out

def _banner(msg, margin_horiz=4, margin_vert=0, linesbefore=2, linesafter=1, border_char="#"):
	s = _makemsg(msg, margin_horiz=margin_horiz, margin_vert=margin_vert, 
		linesbefore=linesbefore, linesafter=linesafter, border_char=border_char)
	_log.info(s)

def _info(msg, prompt="--> "):
	_log.info(" %s%s" % (prompt, msg))

def _debug(msg):
	_log.debug(msg)

def _error(msg):
	_log.error(msg)

def _json_editor(jsonfile):
	if sys.platform == 'darwin':
		os.system('open -a "Sublime Text 2" %s' % jsonfile)
	else:
		pass

# -----------------------------------------------------
#                         END                    
# -----------------------------------------------------

if __name__ == '__main__':
	config = sys.argv[1] if len(sys.argv) > 1 else None
	p = Pedlbrd(config)

