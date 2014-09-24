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
	'reset_after_reconnect' : False,
	'open_log_at_startup': False,

	# OSC
	'osc_port' : 47120,
	'osc_data_addresses' : [ ("127.0.0.1", 47121) ],
	'osc_ui_addresses'   : [ ("127.0.0.1", 47121) ], 
	'osc_send_raw_data': True,
	'osc_datatype': 'f',			   # use f (32bit) or d (64bit) to send normalized analog values
	'osc_async': True,
	'osc_add_kind_to_address': True,   # send {/data/kind pin value}, otherwise {/data kind pin value}
	'osc_reply_namespace': True,        # the /reply(s) are mirrored as /reply/method values
	
	# CONNECTION
	'firsttime_retry_period': 0.3,     # if possitive, dont give up if no device present at creation time, try to reconnect
	'firsttime_accept_fail': True,     # dont fail if there is no connection. Build everything and drops to noconnection state
	'reconnect_period_seconds': 0.25,  # 0 if no reconnection should be attempted 
	'autostart': True,
	'autosave_config_period': 40,
	'serialloop_async': True,
	'sync_bg_checkinterval': 0.2,
	'idle_threshold' : 3,
	'serialtimeout_async': 0.1,
	'serialtimeout_sync' : 0.1,
	'stop_on_keyboard_interrupt' : True,
	'force_device_info_when_reconnect': False,  # When reconnecting, should we ask again for the device info? (this should not change between connects)

	'reset_click_duration': 1,

	# MIDI
	'midi_device_name' : 'PEDLBRD',
	'midichannel' : 0,
	
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
		'A1': { 'midi':{ 'channel': 0, 'cc': 101}
	    },
	    'A2': { 'midi':{ 'channel': 0, 'cc': 102}
	    },
	    'A3': { 'midi':{ 'channel': 0, 'cc': 103}
	    },
	    'A4': { 'midi':{ 'channel': 0, 'cc': 104}
	    }
	},
}

# ---------------------------------------
# Here go default constants
DEFAULTS = {
	'envname'    : '__env__',
	'configname' : '__default__',
	'max_analog_value' : 1023
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