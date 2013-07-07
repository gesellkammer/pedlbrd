from fnmatch import fnmatch

class NotifyDict(dict):
	"""
	a dictionary that notifies you of changes to it or its subdicts

	>>> def printfunc(*args): print args
	>>> orig = {'A':10, 'B':{'Ba':100, 'Bb':200}}
	>>> d = NotifyDict(lambda key, value: printfunc(key, value), orig)
	>>> d['B']['Ba'] = 101
	B/Ba 101

	You can define qualified callbacks

	>>> d = NotifyDict({'*'  : lambda key, value: printfunc("default", key, value), 
		                'B/*': lambda key, value: printfunc("subdict", key, value)}, orig)
    >>> d['C'] = 9
    default C 9
    >>> d['B']['Bh'] = 8
    subdict B/Bh 8
	"""
	__slots__ = ['callback_registry', 'callback', 'match', 'separator']
	def __init__(self, callback, *args, **kws):
		if callable(callback):
			self.callback = callback
			self.callback_registry = {}
		elif isinstance(callback, dict):
			self.callback = self.match
			self.callback_registry = callback
		else:
			raise TypeError("callback must be either a function or a dictionary of callbacks")
		dict.__init__(self, *args, **kws)
		self.separator = "/"
	def match(self, key, value):
		for pattern, callback in self.callback_registry.iteritems():
			if fnmatch(key, pattern):
				callback(key, value)
	def __setitem__(self, key, value):
		self.callback(key, value)
		dict.__setitem__(self, key, value)
	def __getitem__(self, key):
		value = dict.__getitem__(self, key)
		if isinstance(value, dict) and not isinstance(value, NotifyDict):
			def newcallback(newkey, newvalue, separator=self.separator):
				self.callback(separator.join((key, newkey)), newvalue)
			value = NotifyDict(newcallback, value)
			dict.__setitem__(self, key, value)
		return value
