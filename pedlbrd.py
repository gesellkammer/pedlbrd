import serial
import glob
import os
import sys
import rtmidi2 as rtmidi
import json
import time

platform = os.uname()[0]
DEBUG = True
BAUDRATE = 57600
CC = 176

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

class DeviceNotFound(BaseException):
	pass

def detect_port():
	if platform == 'Darwin':
		possible_ports = glob.glob("/dev/tty.usbmodem*")
	else:
		print "Platform not supported!"
		return None
	if not possible_ports:
		return None
	for port in possible_ports:
		if is_heartbeat_present(port):
			return port
	return None

def parse_msg(header, msg):
	cmd = header & 0b01111111
	param = ord(msg[0])
	value = ord(msg[1])*128 + ord(msg[2])
	return cmd, param, value

def is_heartbeat_present(port, timeout=2):
	s = serial.Serial(port, baudrate=BAUDRATE, timeout=timeout)
	maxreads = 5
	H = ord("H")
	while maxreads > 0:
		b = ord(s.read(1))
		if b & 0b10000000:
			msg = s.read(3)
			command, param, value = parse_msg(b, msg)
			if command == H:
				return True
		maxreads -= 1
	return False

def monitor(port):
	s = serial.Serial(port, baudrate=BAUDRATE)
	while True:
		b = ord(s.read(1))
		if b & 0b10000000:
			# high bit set, read command and 2 bytes
			cmd, param, value = parse_msg(b, s.read(3))
			print "COMMAND: %s  PARAM: %d  VALUE: %d" % (chr(command), param, value)

def _get_path(path):
	folder, name = os.path.split(path)
	if not folder:
		folder = "."
	path = os.path.join(folder, name)
	path = os.path.abspath(path)
	return path

def _load_config(config):
	d = None
	if isinstance(config, dict):
		d = config
	if isinstance(config, basestring):
		config = _get_path(config)
		if os.path.exists(config):
			d = json.load(open(config))
	else:
		raise TypeError("config must be either a dictionary or the path to a .json config file")
	if not d:
		raise ValueError("could not parse the config dictionary")

	# check for sanity
	_config_check(d, DEFAULT_CONFIG, 
		[
			'midi_device_name',
			'osc_port'
		]
	)
	return d

def debug(*args):
	print args
	return args


def _config_check(newd, defaultd, attrs):
	for attr in attrs:
		if not newd.get(attr):
			newd[attr] = defaultd[attr]

DEFAULT_CONFIG = {
	'midi_device_name' : 'PEDLBRD',
	'midi_enabled' : True,
	'osc_enabled' : False,
	'osc_port' : 47120,
	'reconnect_period': 1,     # 0 if no reconnection should be attempted
	'osc_registered_ports' : [],
	'autostart': True,
	'num_digital_pins': 12,
	'num_analog_pins': 4,
	'midi_mapping_digital': {
		2: {'channel': 0, 'cc': 1,  'output':[0, 127], 'input':[0, 1], 'inverted':False},
		3: {'channel': 0, 'cc': 2,  'output':[0, 127], 'input':[0, 1], 'inverted':False},
		4: {'channel': 0, 'cc': 3,  'output':[0, 127], 'input':[0, 1], 'inverted':False},
		5: {'channel': 0, 'cc': 4,  'output':[0, 127], 'input':[0, 1], 'inverted':False},
		6: {'channel': 0, 'cc': 5,  'output':[0, 127], 'input':[0, 1], 'inverted':False},
		7: {'channel': 0, 'cc': 6,  'output':[0, 127], 'input':[0, 1], 'inverted':False},
		8: {'channel': 0, 'cc': 7,  'output':[0, 127], 'input':[0, 1], 'inverted':False},
		9: {'channel': 0, 'cc': 8,  'output':[0, 127], 'input':[0, 1], 'inverted':False},
		10:{'channel': 0, 'cc': 9,  'output':[0, 127], 'input':[0, 1], 'inverted':False},
		11:{'channel': 0, 'cc': 10, 'output':[0, 127], 'input':[0, 1], 'inverted':False}
	},
	'midi_mapping_analog': {
		0: {'channel': 0, 'cc': 101, 'output':[0, 127], 'input':[0, 1023]},
		1: {'channel': 0, 'cc': 102, 'output':[0, 127], 'input':[0, 1023]},
		2: {'channel': 0, 'cc': 103, 'output':[0, 127], 'input':[0, 1023]},
		3: {'channel': 0, 'cc': 104, 'output':[0, 127], 'input':[0, 1023]},
	}
}

class MIDI_Mapping(object):
	def __init__(self, config, midiout):
		"""
		config: a dictionary containing the entire configuration
		"""
		self.parse_config(config)
		self.midiout = midiout
		
	def parse_config(self, config):
		"""
		config: a dictionary
		"""
		assert isinstance(config, dict)
		self.digital_pins = digital_pins = sorted(config['midi_mapping_digital'].keys())
		self.num_digital_pins = num_digital_pins = max(digital_pins) + 1
		midi_mapping_digital = config['midi_mapping_digital']

		self.digital_mapping = [midi_mapping_digital.get(i) for i in range(num_digital_pins)]
		# TODO: analog mapping
			
	def construct_digital_func(self, pin):
		mapping = self.digital_mapping[pin]
		midiout = self.midiout
		if mapping:
			byte1 = CC + mapping['channel']
			cc = mapping['cc']
			out0, out1 = mapping['output']
			if not mapping['inverted']:
				func = lambda x: midiout.send_message((byte1, cc, x*out1))
			else:
				func = lambda x: midiout.send_message((byte1, cc, (1 - x)*out1))
			return func
		else:
			return None

	def construct_analog_func(self, pin):
		mapping = self.digital_mapping[pin]
		midiout = self.midiout
		if mapping:
			byte1 = CC + mapping['channel']
			cc = mapping['cc']
			in0, in1 = mapping['input']
			out0, out1 = mapping['output']
			in_diff = in1 - in0
			out_diff = out1 - out0
			def func(x):
				delta = (x - in0) / in_diff
				value = delta * out_diff + out0
				midiout.send_message((byte1, cc, value))
			return func
		else:
			return None		

class Pedlbrd(object):
	def __init__(self, config=None):
		"""
		config: the name of the configuration file or None to use the default

		>>> import pedlbrd
		>>> brd = pedlbrd.Pedlbrd()
		>>> brd.midi_enabled
		True
		>>> brd.osc_enabled
		False
		>>> brd.info()  # will print all the configuration to stdout
		>>> brd.save('myconfig')  # if no path is given, config files are saved in ~/.pedlbrd in Unix, /Users/you/.pedlbrd/ in Windows
		/Users/edu/.pedlbrd/config.json
		"""
		self.configfile = config
		self._parse_config()
		self._running = False
		self._midiout = None
		port = detect_port()
		if not port:
			raise DeviceNotFound("A Pedlbrd could not be found in the system. Make sure it is connected")
		elif DEBUG:
			_show_banner("Pedlbrd found! Using port: %s" % port, char_horiz='*', space_before=2, space_after=1)
		self._serialport = port 
		if self.midi_enabled:
			self._midi_turnon()
		else:
			self._midi_mapping = None
		if self.config['autostart']:
			self.start()

	@property
	def serialport(self):
		if self._serialport:
			return self._serialport
		port = detect_port()
		self._serialport = port
		return port

	def _parse_config(self):
		if self.configfile:
			d = _load_config(self.configfile)
		else:
			d = DEFAULT_CONFIG
		if not d:
			raise ValueError("could not load config file")
		self.config = d

	@property
	def midi_enabled(self):
		return self.config['midi_enabled']
	@midi_enabled.setter
	def midi_enabled(self, status):
		if status == self.midi_enabled:
			return
		self.config['midi_enabled'] = status
		if status:
			self._midi_turnon()
		else:
			self._midi_turnoff()

	def _midi_turnon(self):
		if self._midiout is not None:
			return
		midiout = rtmidi.MidiOut()
		midiout.open_virtual_port(self.config['midi_device_name'])
		self._midi_mapping = MIDI_Mapping(self.config, midiout)
		self._midiout = midiout
		
	def _midi_turnoff(self):
		self._midiout.close_port()
		self._midiout = None
		self._midi_mapping = None

	def start(self): 
		num_digital_pins = self.config['num_digital_pins']
		num_analog_pins = self.config['num_analog_pins']
		digital_func_list = [[] for i in range(num_digital_pins)]
		analog_func_list = [[] for i in range(num_analog_pins)]
		if not(self.midi_enabled):
			raise ValueError("no action to perform on serial data. Try enabling midi or osc")
		else:
			self._midi_turnon()
			midiout = self._midiout
			midi_mapping = self._midi_mapping
			for pin in range(num_digital_pins):
				midifunc = midi_mapping.construct_digital_func(pin)
				digital_func_list[pin].append(midifunc)
			for pin in range(num_analog_pins):
				midifunc = midi_mapping.construct_analog_func(pin)
				analog_func_list[pin].append(midifunc)
		self._running = True
		_time = time.time
		while self._running:
			try:
				self.serialconnection = s = serial.Serial(self.serialport, baudrate=BAUDRATE, timeout=2)
				last_heartbeat = _time()
				connected = True
				while self._running:
					now = _time()
					b = s.read(1)
					if len(b):
						b = ord(b)
						if b & 0b10000000:
							cmd, param, value = parse_msg(b, s.read(3))
							if cmd == 68:	# --> D(igital)
								funclist = digital_func_list[param]
								for func in funclist:
									func(value)
							elif cmd == 65: # --> A(nalog)
								print "A", param
								funclist = analog_func_list[param]
								for func in funclist:
									func(value)
							elif cmd == 72: # --> H(eartbeat)
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
			except OSError:
				# arduino disconnected -> s.read throws device not configured
				self._reconnect()
			except serial.SerialException:
				self._reconnect()
			#except:
		# 		print "Unexpected error. Stopping"
		#		print "Error: ",  sys.exc_info()[0]
				
	def stop(self):
		self._running = False
		self.serialconnection.close()
		if self.midi_enabled:
			self._midi_turnoff()

	def _notify_disconnected(self):
		msg = "DISCONNECTED!"
		_show_banner(msg)

	def _notify_connected(self):
		msg = "CONNECTED!"
		_show_banner(msg)

	def _reconnect(self):
		"""
		attempt to reconnect

		True if successful, False if no reconnection possible
		"""
		reconnect_period = self.config['reconnect_period']
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
				
def _show_banner(msg, margin_horiz=4, space_after=1, space_before=0, char_horiz="#"):
	for i in range(space_before):
		print
	sep = char_horiz * (len(msg) + margin_horiz*2 + 2)
	print sep
	print "%s%s%s%s%s" % (char_horiz, " " * margin_horiz, msg, " " * margin_horiz, char_horiz)
	print sep
	for i in range(space_after):
		print



