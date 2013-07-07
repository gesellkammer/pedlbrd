#######################################################
#######################################################
#
#                    CONFIGURATION
#
#######################################################
#######################################################

DEFAULT_CONFIG = {
	'midi_device_name' : 'PEDLBRD',
	# 'configname' : 'DEFAULT',
	'osc_port' : 47120,
	'osc_report_addresses' : [ ("localhost", 47121) ], 
	'reconnect_period_seconds': 1,     # 0 if no reconnection should be attempted 
	'firsttime_retry_period': 1,       # if possitive, dont give up if no device present at creation time, try to reconnect
	'osc_out_addresses' : [],
	'autostart': True,
	'num_digital_pins': 12,
	'num_analog_pins': 4,
	'serialloop_async': True,
	'input_definition': {
	# label     definition
		'D1' : {'pin':'D2'},
		'D2' : {'pin':'D3'},
		'D3' : {'pin':'D4'},
		'D4' : {'pin':'D5'},
		'D5' : {'pin':'D6'},
		'D6' : {'pin':'D7'},
		'D7' : {'pin':'D8'},
		'D8' : {'pin':'D9'},
		'D9' : {'pin':'D10'},
		'D10': {'pin':'D11'},
		'A1' : {'pin':'A0'},
		'A2' : {'pin':'A1'},
		'A3' : {'pin':'A2'},
		'A4' : {'pin':'A3'},
	},
	# ----------------------------------------------------- 
	# inputs are the UI side of pins, identified by a label
	# -----------------------------------------------------
	'input_mapping' : { 
		'D1' : { 'inverted': False, 
		         'midi':{ 'channel': 0, 'cc': 1, 'output':[0, 127]}
		},
		'D2' : { 'inverted': False, 
		         'midi':{ 'channel': 0, 'cc': 2, 'output':[0, 127]}
		},
		'D3' : { 'inverted': False, 
		         'midi':{ 'channel': 0, 'cc': 3, 'output':[0, 127]}
		},
		'D4' : { 'inverted': False, 
		         'midi':{ 'channel': 0, 'cc': 4, 'output':[0, 127]}
		},
		'D5' : { 'inverted': False, 
		         'midi':{ 'channel': 0, 'cc': 5, 'output':[0, 127]}
		},
		'D6' : { 'inverted': False, 
		         'midi':{ 'channel': 0, 'cc': 6, 'output':[0, 127]}
		},
		'D7' : { 'inverted': False, 
		         'midi':{ 'channel': 0, 'cc': 7, 'output':[0, 127]}
		},
		'D8' : { 'inverted': False, 
		         'midi':{ 'channel': 0, 'cc': 8, 'output':[0, 127]}
		},
		'D9' : { 'inverted': False, 
		         'midi':{ 'channel': 0, 'cc': 9, 'output':[0, 127]}
		},
		'D10': { 'inverted': False, 
		         'midi':{ 'channel': 0, 'cc': 10, 'output':[0, 127]}
		},
		'A1': { 'normalized': True,
	     		'midi':{ 'channel': 0, 'cc': 101, 'output':[0, 127], 'input':[0, 1023]}
	    },
	    'A2': { 'normalized': True,
	     		'midi':{ 'channel': 0, 'cc': 102, 'output':[0, 127], 'input':[0, 1023]}
	    },
	    'A3': { 'normalized': True,
	     		'midi':{ 'channel': 0, 'cc': 103, 'output':[0, 127], 'input':[0, 1023]}
	    },
	    'A4': { 'normalized': True,
	     		'midi':{ 'channel': 0, 'cc': 104, 'output':[0, 127], 'input':[0, 1023]}
	    }
	},
}

# ---------------------------------------
DEFAULTS = {
	'envname' : '__env__'
}

DEFAULT_ENV = {
	'restore_session': True,
	'envname' : DEFAULTS['envname'],
	'autosave_config': True
}