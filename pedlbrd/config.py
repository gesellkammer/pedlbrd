#######################################################
#######################################################
#
#                    CONFIGURATION
#
#######################################################
#######################################################

DEFAULT_CONFIG = {
	'num_digital_pins': 12,
	'num_analog_pins' : 4,

	'autocalibrate_digital' : True,
	'open_log_at_startup': True,

	# OSC
	'osc_port' : 47120,
	'osc_ui_addresses'   : [ ("127.0.0.1", 47121) ], 
	'osc_data_addresses' : [ ("127.0.0.1", 47121) ],
	'osc_send_raw_data': True,

	# CONNECTION
	'reconnect_period_seconds': 1,     # 0 if no reconnection should be attempted 
	'firsttime_retry_period': 0.3,       # if possitive, dont give up if no device present at creation time, try to reconnect
	'autostart': True,
	'autosave_config_period': 20,
	'serialloop_async': True,

	# MIDI
	'midi_device_name' : 'PEDLBRD',
	
	# PIN DEFINITIONS 
	'input_definition': {
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
# Here go default constants
DEFAULTS = {
	'envname'    : '__env__',
	'configname' : '__default__'
}

# Here go the settings that are hardware independent
DEFAULT_ENV = {
	'restore_session': True,
	'envname' : DEFAULTS['envname'],
	'info_max_length' : 60,
	'last_saved_env': '',
	'last_loaded_env': '',
	'last_saved_config': ''
}