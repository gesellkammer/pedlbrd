#!/usr/bin/env python
from __future__ import division as _division, absolute_import as _absolute_import
# stdlib
import os
import sys
import glob
import json
import time
import logging
import logging.handlers
import shutil
import inspect
import fnmatch

# dependencies
import timer2
import liblo
import serial
import rtmidi2 as rtmidi

# local
from .config import *
from . import util
from . import envir

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
try:
	_scheduler = timer2.Timer(precision=0.3)
except:
	raise RuntimeError("XXXXX")

################################
#
# Logging
#
################################
class Log:
	def __init__(self):
		logname = 'PEDLBRD'
		self.filename_debug = os.path.join(envir.configpath(), "%s--debug.log" % logname)
		self.filename_info  = os.path.join(envir.configpath(), "%s.log" % logname)
		self.debug_log = debug_log = logging.getLogger('pedlbrd-debug')
		debug_log.setLevel(logging.DEBUG)
		debug_handler = logging.handlers.RotatingFileHandler(self.filename_debug, maxBytes=80*2000, backupCount=1)
		debug_handler.setFormatter( logging.Formatter('%(levelname)s: -- %(message)s') )
		debug_log.addHandler(debug_handler)

		self.info_log = info_log = logging.getLogger('pedlbrd-info')
		info_log.setLevel(logging.INFO)
		info_handler = logging.handlers.RotatingFileHandler(self.filename_info, maxBytes=80*500, backupCount=0)
		info_handler.setFormatter( logging.Formatter('%(message)s') )
		info_log.addHandler(info_handler)
		self.loggers = [debug_log, info_log]

	def debug(self, msg):
		for logger in self.loggers:
			logger.debug(msg)

	def info(self, msg):
		for logger in self.loggers:
			logger.info(msg)

	def error(self, msg):
		for logger in self.loggers:
			logger.error(msg)

logger = Log()

#################################
# CONSTANTS & SETUP
#################################
DEBUG = False
BAUDRATE = 57600
CC = 176

CMD_FORCE_DIGITAL = 'F'

ERRORCODES = {
	7: 'ERROR_COMMAND_BUF_OVERFLOW'
}

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

class DeviceNotFound(BaseException): pass

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

def write_default_config(name=None):
	"""
	write the default configuration to the configpath

	name: the name to be given to the resulting config file

	Returns
	=======

	the path of the written file
	"""
	if name is None:
		name = DEFAULTS['configname']
	name = os.path.splitext(name)[0] + '.json'
	path = os.path.join(envir.configpath(), name)
	_jsondump(DEFAULT_CONFIG, path)
	return path

# -----------------------------------------------------------------------

class Configuration(dict):
	__slots__ = "callback _label2pin _pin2label _callback_enabled".split()
	def __init__(self, config, overrides=None, callback=None):
		"""
		config   : (dict) The configuration dict
		callback : (func) The function to be called each time the
		                  configuration is changed
		overrides: (dict) A dictionary that overrides (updates) config
		"""
		assert isinstance(config, dict)
		if overrides:
			config.update(overrides)
		self.callback = callback
		self.update(config)
		self._label2pin, self._pin2label = self._get_input_pin_mapping()
		self._callback_enabled = True

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
			if self._callback_enabled:
				self.callback(path, value)
		else:
			for key in keys[:-1]:
				v = d.get(key)
				if isinstance(v, dict):
					d = v
				else:
					raise KeyError("set -- key not found: %s" % key)
			d[keys[-1]] = value
			if self._callback_enabled:
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
		assert isinstance(configuration, Configuration)
		self.config = configuration
		self._analog_lastvalues = [0 for i in range(self.config['num_analog_pins'])]

	def construct_func(self, label):
		if label[0] == 'D':
			return self.construct_digital_func(label)
		return self.construct_analog_func(label)

	def construct_digital_func(self, label):
		mapping = self.config.midi_mapping_for_label(label)
		kind, pin = self.config.label2pin(label)
		if not mapping:
			return None
		byte1 = CC + mapping['channel']
		cc = mapping['cc']
		_, out1 = mapping['output']
		func = lambda x: (byte1, cc, x*out1)
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
		config: (str) The name of the configuration file
		        None to use the default

		restore_session: (bool) Override the directive in config
		"""
		self.env = self._load_env(env)
		if restore_session is None:
			restore_session = self.env['restore_session']
		self.config, self.configfile = self._load_config(config, kws, restore_session=restore_session)
		
		self._labels = self.config['input_definition'].keys()
		self._running = False
		self._status  = ''
		self._paused  = False
		self._midiout = None
		self._serial_timeout = 0.2
		self._dispatch_funcs_by_pin = {}
		self._echo = False
		self._handlers = {}
		self._serialconnection = None
		self._oscserver = None
		self._oscapi    = None
		self._midichannel = -1
		self._reply_funcs = {}

		self.reset_state()
		self._cache_update()
		self._oscserver, self._oscapi = self._create_oscserver()
		self._oscserver.start()

		# Here we actually try to connect to the device.
		# If firsttime_retry_period is possitive, it will block and wait for device to show up
		self._prepare_connection()
		self.report()
		if self.config['autostart']:
			_debug("starting...")
			self.start()
		if self.config.get('open_log_at_startup', False):
			self.open_log()

	#####################################################
	#
	#          P U B L I C    A P I
	#
	#####################################################

	def open_config(self, configfile):
		found, configfile = envir.config_find(configfile)
		if found:
			self.config, self.configfile = self._load_config(configfile)
		self.reset_state()
		self._cache_update()

	def pin2label(self, kind, pin):
		return self.config.pin2label(kind, pin)

	def label2pin(self, label):
		"""
		returns a tuple (kind, pin)
		"""
		return self.config.label2pin(label)

	def config_restore_defaults(self):
		"""
		save the current config and load the default configuration

		Returns
		=======

		the path of the default configuration
		"""
		self.save_config()
		configname = DEFAULTS['configname']
		configpath = write_default_config(configname)
		self.open_config(configname)
		return configpath

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
				self._set_status('nodevice')
				if not retry_period:
					return None
				else:
					_error('Device not found, retrying in %0.1f seconds' % retry_period)
					time.sleep(retry_period)
			else:
				self._set_status('deviceok')
				return port

	@property
	def serialport(self):
		if self._serialport:
			return self._serialport
		port = detect_port()
		self._serialport = port
		return port

	def start(self, async=None):
		"""
		start communication (listen to device, output to midi and/or osc, etc)

		async: if True, do everything non-blocking
		       if None, use the settings in the config ('serialloop_async')
		"""
		self._mainloop(async=async)

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

	def calibrate_digital(self):
		"""
		call this function with all digital input devices
		untouched. this will be the 'rest' state, devices which
		untouched send 1 will be inverted

		This has only sense por "push-to-talk" devices.
		Latching devices (toggle) should be put in the
		off position before calibration
		"""
		if self._running:
			self._calibrate_digital = True
			self._send_command(CMD_FORCE_DIGITAL, 0, 0)
			time.sleep(0.5)
			self._calibrate_digital = False
		else:
			_error("attempted to calibrate digital inputs outside of main loop")
			return

	####################################################
	#
	#          P R I V A T E
	#
	####################################################

	def _register_reply_func(self, param, func):
		"""
		param: a number from 0-127
		func : a function taking one integer argument
		"""
		self._reply_funcs[param] = func

	def _cache_osc_addresses(self):
		def as_address(addr):
			if isinstance(addr, (tuple, list)):
				return liblo.Address(*addr)
			else:
				return liblo.Address(addr)
		self._osc_ui_addresses = [as_address(addr) for addr in self.config['osc_ui_addresses']]
		self._osc_data_addresses = [as_address(addr) for addr in self.config['osc_data_addresses']]

	def _cache_update(self):
		if self._running:
			wasrunning = True
			self.stop()
		else:
			wasrunning = False
		self._cache_osc_addresses()
		self._sendraw = self.config['osc_send_raw_data']
		self._update_midichannel()
		if wasrunning:
			self.start()

	def report(self):
		lines = []
		_info("\n\n")
		_info("- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - ")
		_info("MIDI       : %s" % self.config['midi_device_name'])
		_info("PORT       : %s" % self._serialport)
		_info("OSC IN     : %s, %d" % (_get_ip(), self.config['osc_port']))
		osc_data = self.config['osc_data_addresses']
		osc_ui   = self.config['osc_ui_addresses']
		def addr_to_str(addr):
			return ("%s:%d" % addr).ljust(16)
		if osc_data:
			oscdata_addresses = map(addr_to_str, osc_data)
			_info("OSC OUT    : data  ---------> %s" % " | ".join(oscdata_addresses))
		if osc_ui:
			oscui_addresses = map(addr_to_str, osc_ui)
			_info("           : notifications -> %s" % " | ".join(oscui_addresses))
		if self.config == DEFAULT_CONFIG:
			_info("CONFIG     : default")
		if self.configfile is not None:
			found, configfile_fullpath = envir.config_find(self.configfile)
			if found:
				configstr = configfile_fullpath
			else:
				configstr = "cloned default config with name: %s (will be saved to %s)" % (self.configfile, configfile_fullpath)
			_info("CONFIGFILE : %s" % configstr)
		_info("- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - ")
		self.report_oscapi()
		self.report_config()

	def _digitalmap(self):
		dl = list(self.config['input_mapping'].iteritems())
		dl = util.sort_natural(dl, key=lambda row:row[0])
		out = []
		for label, mapping in dl:
			if label[0] == "D":
				out.append((label, mapping['inverted']))
		return out

	def _digitalmapstr(self):
		m = self._digitalmap()
		out = []
		for label, inverted in m:
			out.append("X" if inverted else "_")
		return ''.join(out)

	def report_config(self):
		d = self.config['input_mapping']
		dl = list(d.iteritems())
		dl = util.sort_natural(dl, key=lambda row:row[0])
		lines = ["\nLABEL    MAPPING", "------------------------------------------------------------"]
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
		lines.append("")
		s = "\n".join(lines)
		_info( s )

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
				shutil.copy(configfile, _add_suffix(configfile, '(orig)'))
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

	def __del__(self):
		self.stop()

	def _prepare_connection(self):
		retry_period = self.config['firsttime_retry_period']
		accept_fail  = self.config['firsttime_accept_fail']
		if accept_fail:
			serialport = self.find_device(retry_period=0)
		else:
			serialport = self.find_device(retry_period=retry_period)
			if not serialport:
				raise DeviceNotFound('PEDLBRD device not found. Aborting')	
		self._serialport = serialport
		
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

		input_mapping = self.config['input_mapping'][label]
		midifunc = self._midi_mapping.construct_func(label)
		midiout = self._midiout
		# ----------------------
		# Digital
		# ----------------------
		if label[0] == "D":
			inverted = input_mapping['inverted']
			sendmidi = midiout.send_message
			if midifunc and self._osc_data_addresses:
				def combinedfunc(value): #, addresses=self._osc_data_addresses):
					if inverted:
						value = 1 - value
					sendmidi(midifunc(value))
					self._send_osc_data('/data', label, value)
					return value
				funcs.append(combinedfunc)
			elif midifunc:
				def onlymidi(value):
					if inverted:
						value = 1 - value
					sendmidi(midifunc(value))
					return value
				funcs.append(onlymidi)
			elif self._osc_data_addresses:
				def onlyosc(value, label=label):
					if inverted:
						value = 1 - value
					self._send_osc_data('/data', label, value)
					return value
				funcs.append(onlyosc)
		# --------------
		# Analog
		# --------------
		if label[0] == "A":
			normalized = input_mapping['normalized']
			sendmidi = midiout.send_message
			kind, pin = self.label2pin(label)
			if midifunc and self._osc_data_addresses:
				def combinedfunc(value, pin=pin):
					if normalized:
						value = self._analog_normalize(pin, value)
					msg = midifunc(value)
					if msg:
						sendmidi(msg)
					self._send_osc_data('/data', label, value)
					return value
				funcs.append(combinedfunc)
			elif midifunc:
				def onlymidi(value, pin=pin):
					if normalized:
						value = self._analog_normalize(pin, value)
					msg = midifunc(value)
					if msg:
						sendmidi(msg)
					return value
				funcs.append(onlymidi)
			elif self._osc_data_addresses:
				def onlyosc(value, label=label, pin=pin):
					if normalized:
						value = self._analog_normalize(pin, value)
					self._send_osc_data('/data', label, value)
					return value
				funcs.append(onlyosc)
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
		self._handlers = {}
		self._handlers['save_env'] = _schedule_regularly(11, self._save_env)
		time.sleep(0.5)
		autosave_config_period = self.config.setdefault('autosave_config_period', 20)
		if autosave_config_period:
			self._handlers['save_config'] = _schedule_regularly(autosave_config_period, self._save_config, kws={'autosave':False})

	# ***********************************************
	#
	# *           M A I N L O O P                   *
	#
	# ***********************************************

	def _mainloop(self, async=None):
		if async is None:
			async = self.config.setdefault('serialloop_async', False)

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

		midiout      = self._midiout
		midi_mapping = self._midi_mapping
		config       = self.config

		self._calibrate_digital = False
		self._running = True
		self._set_status('running')
		_info("\n>>> started listening!")
		dlabels = [self.pin2label("D", i) for i in range(self.config['num_digital_pins'])]
		alabels = [self.pin2label("A", i) for i in range(self.config['num_analog_pins'])]
		while self._running:
			try:
				if not self._reconnect():
					self.stop()
					break
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
							#   ANALOG
							# -------------
							if cmd == 65: # --> A(nalog)
								msg = s.read(3)
								param, value = _parsemsg(msg)
								funclist = self._dispatch_funcs_by_pin.get(('A', param))
								if self._sendraw:
									self._send_osc_ui('/raw', alabels[param], value)
								if funclist:
									for func in funclist:
										value = func(value)
							# -------------
							#    DIGITAL
							# -------------
							elif cmd == 68:	# --> D(igital)
								msg = s.read(3)
								param, value = _parsemsg(msg) # TODO: inline this function
								if not self._calibrate_digital:
									if self._sendraw:
										self._send_osc_ui('/raw', dlabels[param], value)
									funclist = self._dispatch_funcs_by_pin.get(("D", param))
									if funclist:
										for func in funclist:
											value = func(value)								
								else:
									label = self.pin2label('D', param)
									config.set("input_mapping/%s/inverted" % label, bool(value))	
							# -------------
							#   HEARTBEAT
							# -------------
							elif cmd == 72: # --> H(eartbeat)
								ID = ord(s.read(1))
								last_heartbeat = now
								if not connected:
									self._notify_connected()
								connected = True
								self._send_osc_ui('/heartbeat')
							# -------------
							#    REPLY
							# -------------
							elif cmd == 82: # --> R(eply)
								param = ord(s.read(1))
								func = self._reply_funcs.get(param)
								if func:
									value = ord(s.read(1)) * 128 + ord(s.read(1))
									func(value)
									del self._reply_funcs[param]
								else:
									# discard reply
									_debug('no callback for param %d' % param)
									s.read(2)
					if (now - last_heartbeat) > 10:
						connected = False
						self._notify_disconnected()
			except KeyboardInterrupt:
				# poner una opcion en config para decidir si hay que interrumpir por ctrl-c
				self.stop()
				break
			except OSError:   # arduino disconnected -> s.read throws device not configured
				pass
			except serial.SerialException:
				pass
				
	def _send_command(self, cmd, param=0, data=0):
		"""
		cmd: a byte indicating the command
		param: an integer between 0-127
		data: an integer between  0-16383

		This function will only be called if we are _running
		"""
		if not self._running:
			_debug("asked to _send_command outside the loop (need to call start first)")
			return
		conn = self._serialconnection
		b0 = data >> 7;
		b1 = data & 0b1111111;
		s = ''.join( [cmd, chr(param), chr(b0), chr(b1), chr(128)] )
		conn.write(s)

	def _send_osc_ui(self, path, *data):
		if self._oscserver:
			for address in self._osc_ui_addresses:
				self._oscserver.send(address, path, *data)

	def _set_status(self, status=None):
		"""
		set the status and notify it
		if None, just notify

		status must be a string
		"""
		if status is not None:
			assert isinstance(status, basestring)
			self._status = status
			self._send_osc_ui('/status', status)
		else:
			self._send_osc_ui('/status', self._status)
		#for callback in self._status_callbacks:
		#	callback(self._status)

	def _send_osc_data(self, path, *data):
		for address in self._osc_data_addresses:
			self._oscserver.send(address, path, *data)

	def _save_config(self, newname=None, autosave=False):
		used_configfile = self.configfile
		defaultname = 'untitled' if self.config != DEFAULT_CONFIG else DEFAULTS['configname']
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
			#self.open_config(self.configfile)
			#self.report_config()
		else:
			_error("could not find a config file to edit")

	def _analog_normalize(self, pin, value):
		"""
		pin here refers to the underlying arduino pin
		"""
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
		self._set_status('disconnected')

	def _notify_connected(self):
		msg = "CONNECTED!"
		_info(msg)
		self._set_status('connected')

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
			if self.config['autocalibrate_digital']:
				_call_later(3, self.calibrate_digital)
			self.reset_state()
		return out

	def _configchanged_callback(self, key, value):
		_debug('changing config %s=%s' % (key, str(value)))
		paths = key.split("/")
		paths0 = paths[0]
		if paths0 == 'input_mapping':
			label = paths[1]
			self._input_changed(label)
			if "channel" in key:
				self._update_midichannel()
		elif paths0 == 'osc_send_raw_data':
			self._sendraw = value
			_debug('send raw data: %s' % (str(value)))
		elif paths0 == 'osc_data_addresses' or paths0 == 'osc_ui_addresses':
			self._cache_osc_addresses()

	# -------------------------------------------
	# External API
	#
	# methodname: cmd_[cmdname]_[signature]
	#
	# -------------------------------------------
	def cmd_digitalinvert_si(self, label, value):
		"""
		invert a digital input.
		"""
		labels = self._match_labels(label)
		for label in labels:
			path = "input_mapping/%s/inverted" % label
			value = bool(value)
			self.config.set(path, value)

	def cmd_midichannel_si(self, label, channel):
		"""set the channel of the input"""
		labels = self._match_labels(label)
		if not 0 <= channel < 16:
			_error("midi channel should be between 0 and 15, got %d" % channel)
			return
		for label in labels:
			path = 'input_mapping/%s/midi/channel' % label
			self.config.set(path, channel)

	def cmd_midicc_si(self, label, cc):
		"""set the cc. of input"""
		path = 'input_mapping/%s/midi/cc' % label
		if not 0 <= cc < 128:
			_error("midi CC should be between 0 and 127, got %d" % cc)
			return
		self.config.set(path, cc)

	def cmd_resetstate_(self):
		"""
		reset state, doesn't change config
		"""
		self.reset_state()
		_info('reset state!')

	def cmd_resetconfig_(self):
		"""reset config to default values"""
		self.config_restore_defaults()
		_info('config reset to defaults')

	def cmd_calibrate_(self):
		""" calibrate digital inputs """
		_debug('calibrating...')
		self.calibrate_digital()
		self.report()

	def cmd_openlog_i(self, debug=0):
		"""if debug is 1, open the debug console"""
		self.open_log(debug=bool(debug))

	def cmd_registerui_meta(self, path, args, types, src, report=True):
		""" register to receive notifications """
		addresses = self.config.get('osc_ui_addresses', [])
		addr = _sanitize_osc_address(src.hostname, src.port)
		if addr not in addresses:
			addresses.append(addr)
			self.config.set('osc_ui_addresses', addresses)
			if report:
				self.report()

	def cmd_registerdata_meta(self, path, args, types, src, report=True):
		""" register to receive data """
		addresses = self.config.get('osc_data_addresses', [])
		addr = _sanitize_osc_address(src.hostname, src.port)
		if addr not in addresses:
			addresses.append(host, port)
			self.config.set('osc_data_addresses', addresses)
			if report:
				self.report()

	def cmd_registerall_meta(self, path, args, types, src):
		""" register to receive both data and notifications """
		self.cmd_registerui_meta(path, args, types, src, report=False)
		self.cmd_registerdata_meta(path, args, types, src, report=True)

	def cmd_help_(self):
		self.report_oscapi()

	def cmd_dumpconfig_(self):
		self.report()

	def cmd_getstatus_(self):
		""" sends the status to /status"""
		self._set_status()

	def cmd_askHeartperiod_i(self, reply_id):
		""" ask heartbeat rate in ms. ==> /reply reply_id value"""
		self._ask(ord('H'), 0, reply_id)

	def _ask(param, arg, reply_id):
		def callback(outvalue):
			self._send_osc_ui('/reply', reply_id, outvalue)
		self._register_reply_func(param, callback)
		self._send_command('G', param, arg)

	def cmd_askDigitalmapstr_i(self, reply_id):
		mapstr = self._digitalmapstr()
		print "MAPSTR", mapstr
		self._send_osc_ui('/reply', reply_id, mapstr)

	def cmd_askMidichannel_i(self, reply_id):
		"""ask the midichannel to send data to ==> /reply reply_id value"""
		self._send_osc_ui('/reply', reply_id, self._midichannel)

	def cmd_setHeartperiod_i(self, value):
		"""set the heartbeat period in ms"""
		self._send_command('S', 72, value)

	def cmd_quit_(self):
		self.stop()

	def _update_midichannel(self):
		m = self.config['input_mapping']
		midichs = []
		for label, mapping in m.iteritems():
			midichs.append(mapping['midi']['channel'])
		midichs = list(set(midichs))
		if len(midichs) > 1:
			_debug('asked for midichannel, but more than one midichannel found!')
		midich = midichs[0]
		if self._midichannel != midich:
			self._midichannel = midichs[0]
			self._send_osc_ui('/midich', self._midichannel)


	def open_log(self, debug=False):
		if sys.platform == 'darwin':
			os.system("open -a Console %s" % logger.filename_info)
			if debug:
				os.system("open -a Console %s" % logger.filename_debug)
		else:
			_error("...")

	# --------------------------------------------------------
	# OSC server
	# --------------------------------------------------------

	def _osc_get_commands(self):
		cmds = [(a, getattr(self, a)) for a in dir(self) if a.startswith('cmd_')]
		out = []
		def parsecmd(cmd):
			_, path, signature = cmdname.split('_')
			chs = []
			for ch in path:
				if ch.isupper():
					chs.append('/')
				chs.append(ch.lower())	
			path = ''.join(chs)
			if not signature:
				signature = None
			path = "/" + path
			return path, signature
		for cmdname, method in cmds:
			path, signature = parsecmd(cmdname)
			out.append((cmdname, method, path, signature))
		return out

	def _create_oscserver(self):
		"""
		create the OSC server on an independent Thread (ServerThread)

		Populate the methods with all the commands defined in this class
		(methods beginning with cmd_)

		Returns
		=======

		(the osc-server, a dictionary of osc-commands)
		"""
		s = liblo.ServerThread(self.config['osc_port'])
		osc_commands = {}
		for cmdname, method, path, signature in self._osc_get_commands():
			if signature == 'meta':
				# functions annotated as meta will be called directly
				_debug('registering osc %s --> %s' % (path, method))
				s.add_method(path, None, method)
			else:
				# in all other cases, functions are wrapped and arguments are extracted
				func = _func2osc(method)
				_debug('registering osc %s --> %s --> %s' % (path, func, method))
				s.add_method(path, signature, func)
			osc_commands[path] = func
		return s, osc_commands

	def report_oscapi(self):
		lines = []
		ip, oscport = _get_ip(), self.config['osc_port']
		msg = "    OSC Input    |    IP %s    PORT %d    " % (ip, oscport)
		lines.append("=" * len(msg))
		lines.append(msg)
		lines.append("=" * len(msg))
		lines2 = []
		def get_args(method, signature):
			argnames = [arg for arg in inspect.getargspec(method).args if arg != 'self']
			if not signature:
				return []
			osc2arg = {
				's': 'str',
				'i': 'int',
				'd': 'double'
			}
			out = ["%s:%s" % (argname, osc2arg.get(argtype, '?')) for argtype, argname in zip(signature, argnames)]
			return out
		for cmdname, method, path, signature in self._osc_get_commands():
			sign_col_width = 26
			no_sig = " -".ljust(sign_col_width)
			if signature and signature != "meta":
				args = get_args(method, signature)
				signature = ", ".join(args)
				signature = ("(%s)" % signature).ljust(sign_col_width) if signature else no_sig
			else:
				signature = no_sig
			doc = inspect.getdoc(method)
			if doc:
				docstr = doc[:60]
			else:
				docstr = ""
			l = "%s %s | %s" % (path.ljust(16), signature, docstr)
			lines2.append(l)
		lines2.sort()
		lines.extend(lines2)
		lines.append("\nlabel: identifies the input. Valid labels are: D1-D10, A1-A4")
		lines.append("Example: oscsend %s %d /midicc D2 41" % (ip, oscport))
		lines.append("         oscsend %s %s /midichannel * 2" % (ip, oscport))
		s = "\n".join(lines)
		_info(s)

	def _match_labels(self, pattr):
		out = []
		for label in self._labels:
			if fnmatch.fnmatch(label, pattr):
				out.append(label)
		return out

def _get_ip():
	import socket
	return socket.gethostbyname(socket.gethostname())

def _func2osc(func):
	def wrap(path, args, types, src):
		func(*args)
	return wrap

def _sanitize_osc_address(host, port):
	if host == "localhost":
		host = "127.0.0.1"
	assert isinstance(port, int)
	return host, port

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

def _banner(msg, margin_horiz=4, margin_vert=0, linesbefore=0, linesafter=1, border_char="#"):
	s = _makemsg(msg, margin_horiz=margin_horiz, margin_vert=margin_vert,
		linesbefore=linesbefore, linesafter=linesafter, border_char=border_char)
	logger.info(s)

def _info(msg):
	"""
	msg can only be one line
	"""
	logger.info(msg)

def _debug(msg):
	logger.debug(msg)

def _error(msg):
	logger.error(msg)
	print "ERROR:", msg

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

