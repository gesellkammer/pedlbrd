#stdlib
import os
import json
import glob
import sys
import time
import shutil
import serial

class PlatformNotSupported(BaseException): pass

#############################
# Environment
#############################

DEFAULT_PATHS = {
	'darwin': {
		'configpath' : os.path.expanduser("~/.pedlbrd")
	},
	'linux2': {
		'configpath' : os.path.expanduser("~/.config/pedlbrd")
	},
	'win32': {}
}

def basepath():
	return DEFAULT_PATHS.get(sys.platform).get('configpath', None)

def configpath():
	"""
	return the path of the config file (a .json file)
	"""
	return os.path.join(basepath(), "config.json")

def prepare():
	path = os.path.split(configpath())[0]
	if not path:
		raise PlatformNotSupported
	if not os.path.exists(path):
		os.mkdir(path)

def config_load():
	"""
	find configfile, load it as a dictionary

	returns (configdict, configfile), where

	configdict: a dictionary of None
	configfile: the file loaded
	"""
	configfile = configpath()
	exists = os.path.exists(configfile)
	out = json.load(open(configfile)) if os.path.exists(configfile) else None
	assert isinstance(out, dict) or (out is None)
	return out, configfile

def possible_ports():
	"""
	return a list of possible serial ports to look for an arduino device
	"""
	from serial.tools import list_ports
	comports = list_ports.comports()
	ports = [path for path, name, portid in comports if "arduino" in name.lower()]
	if sys.platform == 'linux2':
		return ports
	elif sys.platform == 'darwin':
		# This is just hear-say, but on OSX, the tty. version of the port should be used
		ports2 = []
		for port in ports:
			ttyname = port.replace("cu.", "tty.") 
			if os.path.exists(ttyname):
				port = ttyname
			ports2.append(port)
		return ports2
	else:
		raise PlatformNotSupported