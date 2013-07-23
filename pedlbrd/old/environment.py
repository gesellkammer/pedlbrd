import os
import json

#############################
# Environment
#############################

DEFAULT_PATHS = {
	'Darwin': {
		'configpath' : os.path.expanduser("~/.pedlbrd")
	},
	'Linux': {
		'configpath' : os.path.expanduser("~/.pedlbrd")
	},
	'Windows': {}
}

def _prepare_environment():
	configpath = DEFAULT_PATHS.get(platform).get('configpath')
	if not os.path.exists(configpath):
		os.mkdir(configpath)

def _find_config(configname):
	"""
	configname is a simple name or a full path

	Returns
	=======

	the absolute path of the config file
	"""
	folder, name = os.path.split(configname)
	ext = os.path.splitext(name)[1]
	if not ext:
		name = name + '.json'
	absfolder = os.path.split(os.path.abspath(config))[0]
	defaultpath = DEFAULT_PATHS.get(platform).get('configpath')
	searchpaths = [folder, absfolder, defaultpath]
	foundpath = _search_name_in_paths(name, searchpaths)
	if foundpath is None:
		raise IOError("config file not found in search path: %s" % str(searchpaths))
	return foundpath

def _search_name_in_paths(name, paths):
	for path in paths:
		possible_path = os.path.join(path, name)
		if os.path.exists(possible_path):
			return possible_path
	return None

def _load_config(config):
	d = None
	if isinstance(config, dict):
		d = config
	if isinstance(config, basestring):
		configfolder = _find_config(config)
		if os.path.exists(config):
			d = json.load(open(config))
	else:
		raise TypeError("config must be either a dictionary or the path to a .json config file")
	if not d:
		raise ValueError("could not parse the config dictionary")
	return d