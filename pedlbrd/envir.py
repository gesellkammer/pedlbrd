import os
import json
import glob
import sys

#############################
# Environment
#############################

DEFAULT_PATHS = {
	'darwin': {
		'configpath' : os.path.expanduser("~/.pedlbrd")
	},
	'linux': {
		'configpath' : os.path.expanduser("~/.pedlbrd")
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

	(found, the absolute path of the config file)
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
	config is 
	"""
	assert isinstance(configfile, basestring)
	found, configfile = config_find(configfile)
	if found:
		d = json.load(open(configfile))
		return d
	return None

def possible_ports():
	if sys.platform == 'darwin':
		ports = glob.glob("/dev/tty.usbmodem*")
	else:
		print "Platform not supported!"
		return None
	return ports