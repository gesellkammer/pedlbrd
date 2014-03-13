#stdlib
import os
import json
import glob
import sys
import time
import shutil
import serial

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

def configpath():
	return DEFAULT_PATHS.get(sys.platform).get('configpath')

def prepare():
	path = configpath()
	if not os.path.exists(path):
		os.mkdir(path)

def config_find(configname):
	"""
	configname is a simple name or a full path

	Returns 
	=======

	If config-file was found:
	- (True, absolute_path_of_configfile)

	Else:
	- (False, absolute_path_of_configfile_to_create)
	"""
	folder, name = os.path.split(configname)
	ext = os.path.splitext(name)[1]
	if not ext:
		name = name + '.json'
	absfolder = os.path.split(os.path.abspath(configname))[0]
	defaultpath = DEFAULT_PATHS.get(sys.platform).get('configpath')
	searchpaths = [folder, absfolder, defaultpath]
	for path in searchpaths:
		possiblepath = os.path.join(path, name)
		if os.path.exists(possiblepath):
			outpath = possiblepath
			found = True
			break
	else:
		found = False
		if folder and os.path.exists(folder):
			outpath = os.path.join(folder, name)
		else:
			outpath = os.path.join(defaultpath, name)
	return found, outpath

def config_load(configfile):
	"""
	find configfile, load it as a dictionary
	"""
	assert isinstance(configfile, basestring)
	found, configfile = config_find(configfile)
	out = json.load(open(configfile)) if found else None
	assert isinstance(out, dict) or (out is None)
	return out

def possible_ports():
	"""
	return a list of possible serial ports to look for an arduino device
	"""
	from serial.tools import list_ports
	if sys.platform in ('darwin', 'linux2'):
		comports = list_ports.comports()
		ports = [path for path, name, portid in comports if "arduino" in name.lower()]
		# This is just hear-say, but on OSX, the tty. version of the port should be used
		if sys.platform == 'darwin':
			ports2 = []
			for port in ports:
				ttyname = port.replace("cu.", "tty.") 
				if os.path.exists(ttyname):
					port = ttyname
				ports2.append(port)
			ports = ports2
	else:
		print "Platform not supported!"
		return None
	return ports