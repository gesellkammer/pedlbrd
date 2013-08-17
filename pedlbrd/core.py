#!/usr/bin/env python
from __future__ import division as _division, absolute_import as _absolute_import
# stdlib
import os
import sys
import glob
import time
import logging
import logging.handlers
import shutil
import inspect
import fnmatch
from numbers import Number


try:
    import ujson as json
except ImportError:
    import json

# dependencies
import timer2
import liblo
import serial
import rtmidi2 as rtmidi
from notifydict import ChangedDict

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
# CONSTANTS & SETUP
#################################
DEBUG = False
#BAUDRATE = 115200
BAUDRATE = 250000

CMD_FORCE_DIGITAL = 'F'

ERRORCODES = {
    7: 'ERROR_COMMAND_BUF_OVERFLOW'
}

#################################
# Errors
#################################
class DeviceNotFound(BaseException): pass

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
        else:
            _debug("found port %s, but the device is not sending its heartbeat.\nIt is either another device, or the device is in debug mode" % port)
    return None

def write_default_config(name=None):
    """
    write the default configuration to the configpath

    name: the name to be given to the resulting config file

    ==> the path of the written file
    """
    if name is None:
        name = DEFAULTS['configname']
    name = os.path.splitext(name)[0] + '.json'
    path = os.path.join(envir.configpath(), name)
    _jsondump(DEFAULT_CONFIG, path)
    return path

# -----------------------------------------------------------------------

class Configuration(dict):
    __slots__ = "callback _label2pin _pin2label _callback_enabled state".split()
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
        self.state = {'saved':False, 'changed':True}

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
                self.state['changed'] = True
        else:
            for key in keys[:-1]:
                v = d.get(key)
                if isinstance(v, dict):
                    d = v
                else:
                    _error("set -- key not found: %s" % key)
                    return
            d[keys[-1]] = value
            if self._callback_enabled:
                self.callback(path, value)
                self.state['changed'] = True

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
        byte1 = 176 + mapping['channel']  # 176=CC
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
        byte1 = 176 + mapping['channel']  # 176=CC
        cc = mapping['cc']
        def func(x):    
            # a func should return either a msg or None. x is 0. - 1.
            value = int(x * 127)
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
    def __init__(self, config=None, env=None, restore_session=None, oscasync=None, **kws):
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
        self._max_analog_value = DEFAULTS['max_analog_value']
        self._midiout = None
        self._oscasync = oscasync if oscasync is not None else self.config['osc_async']
        self._serialtimeout = self.config['serialtimeout_async'] if oscasync else self.config['serialtimeout_sync']
        self._dispatch_funcs_by_pin = {}
        self._analog_funcs  = [None for i in range(16)]
        self._digital_funcs = [None for i in range(64)]
        self._handlers = {}
        self._serialconnection = None
        self._oscserver = None
        self._oscapi    = None
        self._midichannel = -1
        self._ip = None
        self._reply_funcs = {}
        self._first_conn = True
        self._calibrate_digital = [False for i in range(64)]
        self._osc_data_addresses = []
        self._osc_ui_addresses = []
        self._replyid = 0

        self.reset_state()
        self._cache_update()
        self._oscserver, self._oscapi = self._create_oscserver()
        if self._oscasync:
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

        ==> the path of the default configuration
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

    def reset_state(self):
        MAX_ANALOG = self._max_analog_value
        NUMPINS = 127
        self._midi_mapping = MIDI_Mapping(self.config)
        self._analog_maxvalues = [1 for i in range(NUMPINS)]
        self._analog_minvalues = [MAX_ANALOG for i in range(NUMPINS)]
        self._input_labels = self.config['input_mapping'].keys()

    def find_device(self, retry_period=0):
        """
        find the path of the serial device. check that it is alive
        if given, retry repeatedly until the device is found

        ==> the path of the serial device.
        """
        while True:
            port = detect_port()
            if not port:
                self._set_status('NO DEVICE')
                if not retry_period:
                    return None
                else:
                    _error('Device not found, retrying in %0.1f seconds' % retry_period)
                    time.sleep(retry_period)
            else:
                self._set_status('DEVICE FOUND')
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
        if async is None:
            async = self.config['serialloop_async']
        self._mainloop(async=async)

    def stop(self):
        if not self._running:
            _debug("already stopped!")
            return
        _banner("stopping...", margin_vert=1)
        self._set_status('QUIT')
        self._send_osc_ui('/quit')      # signal that we are quitting, 
        self._send_osc_data('/quit')
        self._running = False
        # after exiting the mainloop, _terminate will be called

    def _terminate(self):
        if self._oscasync:
            self._oscserver.stop()
            time.sleep(0.1)
            self._oscserver.free()

        if self._serialconnection:
            self._serialconnection.close()
        self._midi_turnoff()
        
        for handlername, handler in self._handlers.iteritems():
            _debug('cancelling %s' % handlername)
            handler.cancel()
        time.sleep(0.2)
        if self.env.get('autosave_config', True):
            self._save_config()
        self._save_env(force=True)

    def save_config(self, newname=None):
        """
        save the current configuration

        newname: like "save as", the newname is used for next savings

        - If a config file was used, it will be saved to this name unless
          a new name is given.
        - If a default config was used ( Pedlbrd(config=None) ), then a default
          name will be used.

        ==> full path where the config file was saved
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
            for i in range(len(self._calibrate_digital)):
                self._calibrate_digital[i] = True
            self._send_command(CMD_FORCE_DIGITAL, 0, 0)
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
        if isinstance(param, basestring):
            assert len(param) == 1
            param = ord(param[0])
        self._reply_funcs[param] = func

    def _cache_osc_addresses(self):
        def as_address(addr):
            if isinstance(addr, (tuple, list)):
                return liblo.Address(*addr)
            else:
                return liblo.Address(addr)
        self._osc_ui_addresses[:] = [as_address(addr) for addr in self.config['osc_ui_addresses']]
        self._osc_data_addresses[:] = [as_address(addr) for addr in self.config['osc_data_addresses']]

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

    @property
    def ip(self):
        if self._ip is not None:
            return self._ip
        self._ip = ip = _get_ip()
        return ip

    def report(self):
        lines = []
        _info("\n\n")
        _info("- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - ")
        _info("MIDI       : %s" % self.config['midi_device_name'])
        _info("PORT       : %s" % self._serialport)
        _info("OSC IN     : %s, %d" % (self.ip, self.config['osc_port']))
        osc_data = self.config['osc_data_addresses']
        osc_ui   = self.config['osc_ui_addresses']
        def addr_to_str(addr):
            return ("%s:%d" % tuple(addr)).ljust(16)
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

    def _digitalmapstr(self, chars="0X"):
        m = self._digitalmap()
        out = []
        for label, inverted in m:
            out.append(chars[inverted])
        return ''.join(out)

    def report_config(self):
        MAX_ANALOG = self._max_analog_value
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
                pin_normalized = True
                # pin_normalized = mapping['normalized']
                normalize = "NORM" if pin_normalized else ""
                if pin_normalized:
                    normalize = "NORM"
                    kind, pin = self.label2pin(label)
                    maxvalue = ("MAX %d" % self._analog_maxvalues[pin]).ljust(8)
                    minvalue = ("MIN %d" % self._analog_minvalues[pin]).ljust(8)
                else:
                    normalize = ""
                    maxvalue = ""
                #in0, in1 = midi['input']
                in0, in1 = 0, MAX_ANALOG
                out0, out1 = 0, 127
                # out0, out1 = midi['output']
                l = "%s    | %s  CH %2d  CC %3d  (%3d - %4d) -> (%3d - %3d)  %s %s" % (label.ljust(3), normalize.ljust(col2),
                    midi['channel'], midi['cc'], in0, in1, out0, out1, maxvalue, minvalue)
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
        return ChangedDict(env)

    def _save_env(self, force=False):
        if force or self.env.changed:
            envpath = _envpath(self._envname)
            _jsondump(self.env, envpath)
            self.env['last_saved_env'] = envpath
            self.env.check()
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
        input_mapping = self.config['input_mapping'][label]
        midifunc = self._midi_mapping.construct_func(label)
        midiout = self._midiout
        # ----------------------
        # Digital
        # ----------------------
        osc_add_kind_to_address = self.config['osc_add_kind_to_address']
        labelpin = int(label[1:])
        if label[0] == "D":
            inverted = input_mapping['inverted']
            sendmidi = midiout.send_message
            def callback(value): #, addresses=self._osc_data_addresses):
                if inverted:
                    value = 1 - value
                sendmidi(midifunc(value))
                if osc_add_kind_to_address:
                    self._send_osc_data('/data/D', labelpin, value)
                else:
                    self._send_osc_data('/data', label, value)
                return value
            return callback
        # --------------
        # Analog
        # --------------
        if label[0] == "A":
            sendmidi = midiout.send_message
            kind, pin = self.label2pin(label)
            osc_datatype = self.config['osc_datatype']
            if osc_datatype not in 'fd':
                _error("The typetag for analog values should be either f or d. Using d")
            makefloat = osc_datatype == 'f'
            if osc_add_kind_to_address:
                def callback(value, pin=pin, normalize=self._analog_normalize, oscsend=self._oscserver.send, addresses=self._osc_data_addresses):
                    value = normalize(pin, value)
                    msg   = midifunc(value)
                    if msg:
                        sendmidi(msg)
                    if makefloat:
                        value = ('f', value)
                    for address in addresses:
                        oscsend(address, '/data/A', labelpin, value)
                    return value
            else:
                def callback(value, pin=pin, normalize=self._analog_normalize, oscsend=self._oscserver.send, addresses=self._osc_data_addresses):
                    value = normalize(pin, value)
                    msg   = midifunc(value)
                    if msg:
                        sendmidi(msg)
                    if makefloat:
                        value = ('f', value)
                    for address in addresses:
                        oscsend(address, '/data', label, value)
                    return value
            return callback
            
    def _update_dispatch_funcs(self):
        for label in self._input_labels:
            self._input_changed(label)

    def _input_changed(self, label):
        kind, pin = self.label2pin(label)
        self._dispatch_funcs_by_pin[(kind, pin)] = f = self._create_dispatch_func(label)
        if kind == 'A':
            self._analog_funcs[pin] = f
        else:
            self._digital_funcs[pin] = f

    def _update_handlers(self):
        for handler in self._handlers.items():
            handler.cancel()
        self._handlers = {}
        self._handlers['save_env'] = _call_regularly(11, self._save_env)
        time.sleep(0.5)
        autosave_config_period = self.config.setdefault('autosave_config_period', 21)
        if autosave_config_period:
            self._handlers['save_config'] = _call_regularly(autosave_config_period, self._save_config, kws={'autosave':False})

    # ***********************************************
    #
    # *           M A I N L O O P                   *
    #
    # ***********************************************

    def _mainloop(self, async):
        _debug("starting mainloop in %s mode" % ("async" if async else "sync"))
        if async:
            _error("This is currently not supported!!!")
            import threading
            self._thread = th = threading.Thread(target=self.start, kwargs={'async':False})
            th.daemon = True
            th.start()
            return

        self._midi_turnon()
        self._update_dispatch_funcs()
        self._update_handlers()

        midiout      = self._midiout
        midi_mapping = self._midi_mapping
        config       = self.config
        time_time    = time.time
        calibrate_digital = self._calibrate_digital

        osc_recv_inside_loop = not self._oscasync
        if osc_recv_inside_loop:
            oscrecv = self._oscserver.recv
        _debug("osc will be processed %s" % ("sync" if osc_recv_inside_loop else "async"))

        dlabels = [self.pin2label("D", i) for i in range(self.config['num_digital_pins'])]
        alabels = [self.pin2label("A", i) for i in range(self.config['num_analog_pins'])]
        
        self._running = True
        self._set_status('STARTING')

        bgtask_checkinterval = self.config['sync_bg_checkinterval']  # if the mainloop is active without time out for this interval, it will be interrupted
        forward_heartbeat    = self.config['osc_forward_heartbeat']
        idle_threshold       = self.config['idle_threshold'] # do background tasks after this time of idle (no data comming from the device)
        
        _info("\n>>> started listening!")
        while self._running:
            try:
                ok = self._connect()
                if not ok: 
                    self.stop()
                    break
                s = self._serialconnection
                s_read = s.read
                _ord = ord
                send_osc_ui   = self._send_osc_ui
                send_osc_data = self._send_osc_data
                last_heartbeat = bgtask_lastcheck = last_idle = time_time()
                connected = True
                sendraw = self._sendraw
                analog_funcs  = self._analog_funcs
                digital_funcs = self._digital_funcs
                while self._running:
                    now = time_time()
                    b = s_read(1)
                    if not len(b):
                        # Connection Timed Out. Time to do idle work
                        if (now - last_idle) > idle_threshold:
                            sendraw = self._sendraw
                            last_idle = now
                        # If we are doind the OSC in sync, we check at each timeout and after a checkinterval 
                        # whenever the connection is active
                        if osc_recv_inside_loop:
                            self._oscserver.recv(5)
                        if (now - last_heartbeat) > 4:
                            connected = False
                            self._notify_disconnected()              
                    else:
                        b = _ord(b)
                        if b & 0b10000000:  # got a message, read the next bytes according to which message
                            cmd = b & 0b01111111
                            # -------------
                            #   ANALOG
                            # -------------
                            if cmd == 65: # --> A(nalog)
                                try:
                                    msg   = s_read(3)
                                    param = _ord(msg[0])
                                    value = _ord(msg[1])*128 + _ord(msg[2])
                                    if sendraw:
                                        send_osc_data('/raw', alabels[param], value)
                                    func = analog_funcs[param]
                                    if func:
                                        func(value)
                                except IndexError:
                                    _debug('timed out while reading analog message, dropping it')
                            # -------------
                            #    DIGITAL
                            # -------------
                            elif cmd == 68: # --> D(igital)
                                try:
                                    msg   = s_read(2)
                                    param = _ord(msg[0])
                                    value = _ord(msg[1])
                                    if calibrate_digital[param]:
                                        label = self.pin2label('D', param)
                                        config.set("input_mapping/%s/inverted" % label, bool(value))
                                        calibrate_digital[param] = False
                                    else:
                                        if sendraw:
                                            send_osc_data('/raw', dlabels[param], value)
                                        func = digital_funcs[param]
                                        if func:
                                            func(value)
                                except IndexError: # this would happen if s_read(2) timed out.
                                    _debug('timed out while parsing digital message, dropping it')
                            # -------------
                            #   HEARTBEAT
                            # -------------
                            elif cmd == 72: # --> H(eartbeat)
                                last_heartbeat = now
                                if not connected:
                                    self._notify_connected()
                                    connected = True
                                if forward_heartbeat:
                                    send_osc_ui('/heartbeat')
                            # -------------
                            #    REPLY
                            # -------------
                            elif cmd == 82: # --> R(eply)
                                msg = s_read(3)
                                param = _ord(msg[0])
                                func = self._reply_funcs.get(param)
                                if func:
                                    _debug("replying to callback %s" % msg[0])
                                    value = _ord(msg[1])*128 + _ord(msg[2])
                                    try:
                                        func(value)
                                    except:
                                        _error("error in callback registered to {param}. error: {error}".format(param=param, error=sys.exc_info()))
                                    del self._reply_funcs[param]
                                else:
                                    # discard reply
                                    value = _ord(msg[1])*128 + _ord(msg[2])
                                    _debug('no callback for param %d ("%s"), value: %d' % (param, chr(param), value))
                            # -------------
                            #    ERROR
                            # -------------
                            elif cmd == 69: # --> E(rror)
                                errorcode = s_read(1)
                                _error("ERRORCODE: %d" % ord(errorcode))
                            # -------------
                            #   MESSAGE
                            # -------------
                            elif cmd == 77: # --> M(essage)
                                _debug('got MSG from device')
                                replyid = _ord(s_read(1))
                                msg = []
                                for i in xrange(127):
                                    ch = s_read(1)
                                    print "[", ch, "]"
                                    if len(ch) == 0 or _ord(ch) == 0 or _ord(ch) == 10:
                                        break
                                    msg.append(ch)
                                func = self._reply_funcs.get(replyid)
                                msg = ''.join(msg)
                                if func:
                                    func(self, replyid, msg)
                                    del self._reply_funcs[replyid]
                                else:
                                    print msg, i
                                    _info('>>>>>> ' + msg)
                        if (now - bgtask_lastcheck) > bgtask_checkinterval:
                            bgtask_lastcheck = now
                            if osc_recv_inside_loop:
                                self._oscserver.recv(5)
                # we stopped
                break

            except KeyboardInterrupt:   # poner una opcion en config para decidir si hay que interrumpir por ctrl-c
                break
            except OSError, serial.SerialException:
                # arduino disconnected -> s.read throws device not configured  
                # don't do anything here, it will reconnect on the next loop
                pass
        self._terminate()
                
    def _send_command(self, cmd, param=0, data=0):
        """
        Send data over to the device over serial (4 bytes protocol)

        cmd: a byte indicating the command
        param: an integer between 0-127
        data: an integer between  0-16383

        This function will only be called if we are _running
        """
        self.send_raw(cmd, param, *int14_to_bytes(data))

    def send_raw(self, *bytes):
        """
        send an arbitrary array of bytes to the device over serial

        bytes: a seq of bytes (either chars or numbers between 0-127)
        """
        intbytes = [(ord(b) if isinstance(b, str) else b) for b in bytes]
        if any(byte > 127 for byte in intbytes):
            _error("_send_raw: value outside of range (0-127). Data will NOT be sent")
        intbytes.append(128)
        bytes2 = map(chr, intbytes)
        s = ''.join(bytes2)
        _debug("_send_raw. received %d bytes (%s), sending %d bytes (%s)sending raw bytes -> %s" % (len(bytes), bytes, len(bytes2), bytes2, str(map(ord, bytes2))))
        self._serialconnection.write(s)

    def _send_osc_ui(self, path, *data):
        oscserver = self._oscserver
        if oscserver:
            for address in self._osc_ui_addresses:
                oscserver.send(address, path, *data)

    def _set_status(self, status=None):
        """
        set the status and notify it
        if None, just notify

        status must be a string
        """
        if status is not None:
            _debug('status: %s' % status)
        if status is not None:
            assert isinstance(status, basestring)
            self._status = status
            self._send_osc_ui('/status', status)
        else:
            self._send_osc_ui('/status', self._status)

    def _send_osc_data(self, path, *data):
        send = self._oscserver.send
        for address in self._osc_data_addresses:
            send(address, path, *data)

    def _save_config(self, newname=None, autosave=False):
        if not self.config.state['changed'] and self.config.state['saved']:
            _debug('config unchanged, skipping save')
            return
        def saveit(self=self):
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
            self.config.state['saved'] = True
            self.config.state['changed'] = False
        _call_later(0, saveit)

    def edit_config(self):
        if self.configfile and os.path.exists(self.configfile):
            _json_editor(self.configfile)
        else:
            _error("could not find a config file to edit")

    def _analog_normalize(self, pin, value):
        """
        pin here refers to the underlying arduino pin
        value returned is 0-1
        """
        maxvalue = self._analog_maxvalues[pin]
        minvalue = self._analog_minvalues[pin]
        if minvalue <= value <= maxvalue:
            value2 = (value - minvalue) / (maxvalue - minvalue)
        elif value > maxvalue:
            self._analog_maxvalues[pin] = value
            value2 = value / self._max_analog_value
        else:
            self._analog_minvalues[pin] = value
            value2 = value / self._max_analog_value
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
        self._set_status('DISCONNECTED')

    def _notify_connected(self):
        msg = "CONNECTED!"
        _info(msg)
        self._set_status('CONNECTED')

    def _connect(self):
        """
        attempt to connect.

        True if successful, False if no reconnection possible
        """
        reconnect_period = self.config['reconnect_period_seconds']
        _debug('attempting to connect')
        if not reconnect_period:
            self.stop()
            out = False
        else:
            self._notify_disconnected()
            conn_found = False
            while self._running:
                try:
                    _debug("....looking for port")
                    port = detect_port()
                    if port:
                        self._serialport = port
                        conn_found = True
                        break
                    else:
                        _debug("....port NOT FOUND. Attempting again in %.2f seconds" % reconnect_period)
                        time.sleep(reconnect_period)
                except KeyboardInterrupt:
                    break
        if conn_found:
            self._serialconnection = serial.Serial(self.serialport, baudrate=BAUDRATE, timeout=self._serialtimeout)
            self._notify_connected()
            _call_later(4, self._get_device_info)
            _call_later(8, lambda self: setattr(self, '_first_conn', False), (self,))
            if self.config['autocalibrate_digital']:
                _call_later(2, self.calibrate_digital)
            if self.config['reset_after_reconnect']:
                self.reset_state()
        return conn_found

    def _get_device_info(self, force=False):
        _debug('get_device_info. first_conn: {first_conn} force: {force} force_on_reconnect: {force_on_reconnect}'.format(
            first_conn=self._first_conn, force=force, force_on_reconnect=self.config['force_device_info_when_reconnect']
            ))   
        if self._first_conn or force or self.config['force_device_info_when_reconnect']:
            _debug('attempting to sync_maxanalog')
            self.sync_maxanalog()
            # TODO

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

    # /////////////////////////
    # IO with device

    def send_with_reply(self, bytes, callback, reply_id=None):
        if reply_id is None:
            reply_id = self._newreplyid()
        self._register_reply_func(reply_id, callback)
        bytes = tuple(bytes) + (reply_id,)
        self.send_raw(*bytes)
        
    def sync_maxanalog(self):
        def callback(outvalue):
            self._max_analog_value = outvalue
        self.send_with_reply(('G', 'A'), callback)

    def _newreplyid(self):
        self._replyid += 1
        self._replyid %= 127
        return self._replyid

    # -------------------------------------------
    # ::External API
    #
    # methodname: cmd_[cmdname]_[signature]
    #
    # -------------------------------------------
    def cmd_digitalinvert(self, label, value):
        """{si} invert a digital input. """
        labels = self._match_labels(label)
        for label in labels:
            path = "input_mapping/%s/inverted" % label
            value = bool(value)
            self.config.set(path, value)

    def cmd_smoothing_get(self, src, reply_id, analoginput):
        """returns the analog smoothing percentage"""
        if analoginput < 1:
            _error("analoginpurt should be 1 or more")
        def callback(outvalue, oscserver=self._oscserver, src=src, reply_id=reply_id):
            oscserver.send(src, '/reply', reply_id, outvalue)
        self.send_with_reply(('G', 'S', analoginput-1), callback)

    def cmd_midichannel_set(self, label, channel):
        """{si} set the channel of the input. label can be a wildcard"""
        labels = self._match_labels(label)
        if not 0 <= channel < 16:
            _error("midi channel should be between 0 and 15, got %d" % channel)
            return
        for label in labels:
            path = 'input_mapping/%s/midi/channel' % label
            self.config.set(path, channel)

    def cmd_midicc_set(self, label, cc):
        """{si} set the CC of input. label can be a wildcard"""
        path = 'input_mapping/%s/midi/cc' % label
        if not 0 <= cc < 128:
            _error("midi CC should be between 0 and 127, got %d" % cc)
            return
        self.config.set(path, cc)

    def cmd_midicc_get(self, label):
        cc = self.config.getpath('input_mapping/%s/midi/cc' % label)
        if cc is None:
            _error('could not get midicc for label: %s' % label)
            return
        return cc

    def cmd__testecho(self, path, args, types, src):
        print path, args, types, src

    def cmd_resetstate(self):
        """
        reset state, doesn't change config
        """
        self.reset_state()
        _debug('reset state!')

    def cmd_resetconfig(self):
        """reset config to default values"""
        self.config_restore_defaults()
        _debug('config reset to defaults')

    def cmd_calibrate(self):
        """calibrate digital inputs """
        _debug('calibrating...')
        self.calibrate_digital()
        self.report()

    def cmd_openlog(self, debug=0):
        """{i} if debug is 1, open the debug console"""
        _call_later(0.1, self.open_log, [bool(debug)])

    def cmd__registerui(self, path, args, types, src, report=True):
        """register for notifications. optional arg: address to register"""
        addresses = self.config.get('osc_ui_addresses', [])
        if args:
            addr = args
        else:
            addr = (src.hostname, src.port)
        addr = _sanitize_osc_address(*addr)
        if addr not in addresses:
            addresses.append(addr)
            self.config.set('osc_ui_addresses', addresses)
            if report:
                self.report()

    def cmd__registerdata(self, path, args, types, src, report=True):
        """register for data. optional arg: address to register"""
        addresses = self.config.get('osc_data_addresses', [])
        if args:
            addr = args
        else:
            addr = (src.hostname, src.port)
        addr = _sanitize_osc_address(*addr)
        if addr not in addresses:
            _debug('registering addr for data: %s' % str(addr))
            addresses.append(addr)
            self.config.set('osc_data_addresses', addresses)
            if report:
                self.report()

    def cmd__registerall(self, path, args, types, src):
        """ register to receive both data and notifications """
        self.cmd__registerdata(path, args, types, src, report=False)
        self.cmd__registerui(path, args, types, src, report=True)

    def cmd__signout(self, path, args, types, src):
        """remove observer from both data and or ui"""
        if args:
            addr = args
        else:
            addr = (src.hostname, src.port)
        addr = _sanitize_osc_address(*addr)
        ui_addresses = self.config['osc_ui_addresses']
        data_addresses = self.config['osc_data_addresses']
        if addr in ui_addresses:
            ui_addresses.remove(addr)
            self.config.set('osc_ui_addresses', ui_addresses)
        if addr in data_addresses:
            data_addresses.remove(addr)
            self.config.set('osc_data_addresses', data_addresses)


    def cmd_api_get(self, src, reply_id):
        """replies with a list of api commands of the form path#args#types#docstr"""
        args = []
        def sanitize(arg):
            if arg is None:
                arg = "-"
            else:
                arg = str(arg)
            return arg
        for cmd in self._osc_get_commands():
            path, types, docstr = [cmd.get(attr) for attr in ('path', 'signature', 'docstr')]
            print src.hostname, src.port, "-->", " ".join(map(repr, (path, types, docstr)))
            msg = "#".join(map(sanitize, (path, types, docstr)))
            args.append(msg)
        args.sort()
        self._oscserver.send(src, '/reply', reply_id, *args)

    def cmd_devinfo_get(self, src, reply_id):
        pass

    def cmd__help(self, path, args, types, src):
        self.report_oscapi()

    def cmd__echo(self, path, args, types, src):
        if args:
            self._oscserver.send(src, '/echo', *args)
        else:
            self._oscserver.send(src, '/echo')

    def cmd_dumpconfig(self):
        self.report()

    def cmd_getstatus(self):
        """sends the status to /status"""
        self._set_status()

    """
    ask protocol: <-- /ask/something reply_id *args
                  --> /reply reply_id answer
    handler:  func(src, reply_id):
                  return answer
    handler2: func(src, reply_id):
                  ... do something (for instance, ask a value from the device)
                  ... send the value yourself
                  return None

                  src will be either the src from where the request came
                  or the explicit address sent with the message as part
                  of the reply_id

    src: a tuple (hostname:str, port:int)
    reply_id: an int

    reply_id: - an integer --> the reply_id
                The reply will be sent to the address from which it came
              - a string of the form "hostname:port/reply_id" or "port/reply_id"
                The reply will be sent to the address (hostname, port)

    We do this because some software (pd, for example), dont let you 
    specify or even query the port from which you are sending osc,
    so that in order to receive the reply, you must hardcode the src
    """
    def cmd_heartperiod_get(self, src, reply_id):
        """returns heartbeat rate in ms"""
        def callback(outvalue, oscserver=self._oscserver, src=src, reply_id=reply_id):
            print "callback heartbeat. outvalue: ", outvalue
            try:
                oscserver.send(src, '/reply', reply_id, outvalue)
            except:
                _error("error by sending outvalue via OSC: %s" % sys.exc_info())
        self.send_with_reply(('G', 'H'), callback)
        

    def cmd_digitalmapstr_get(self, src, reply_id):
        return self._digitalmapstr()

    def cmd_midichannel_get(self, src, reply_id):
        """midichannel used to send data"""
        return self._midichannel

    def cmd_uiaddr_get(self, src, reply_id):
        """OSC addresses for UI information ==> uiaddresses : a space separated string of 'hostname:port'"""
        addresses = self.config['osc_ui_addresses']
        out = ["%s:%d" % (host, port) for hort, port in addresses]
        return "#".join(out)

    def cmd_dataaddr_get(self, src, reply_id):
        """OSC addresses for data information ==> a space separated string of 'hostname:port'"""
        addresses = self.config['osc_data_addresses']
        out = ["%s:%d" % (host, port) for host, port in addresses]
        return "#".join(out)
        
    def cmd_maxanalog_get(self, src, reply_id):
        return self._max_analog_value

    def cmd_maxanalog_set(self, value):
        """{i} Set the analog resolution. Between 255-1023"""
        self._max_analog_value = value
        self._send_command('S', 'A', value)

    def cmd_heartperiod_set(self, value):
        """{i} set the heartbeat period in ms"""
        _debug("setting heartperiod to %d" % value)
        def error_callback(err):
            if err:
                _debug("/heartperiod/set failed!")
            else:
                _debug("/heartperiod/set OK")
        self._register_reply_func(ord('E'), error_callback)
        self._send_command('S', 'H', value)

    def cmd_smoothing_set(self, analoginput, percent):
        """{ii} set the analog smoothing (0-100, deault=50). 0=no smoothing"""
        if analoginput < 1:
            _error("ValueError: /smoothing/set -> analoginput should be 1 or more")
        if not (0 <= percent <= 100):
            _error("/smoothing/set: ValueError -> percent should be between 0-100")
        _debug('/smoothing/set %d %d' % (analoginput, percent))
        self.send_raw('S', 'S', analoginput-1, percent)

    def cmd_filtertype_set(self, analoginput, value):
        """{ii}analoginput: the number of the analog input (beginning with 1). value:0-1"""
        value = bool(value) * 1
        if not( 1 <= analoginput < 6):
            _error("filter/set: analoginput out of range: %d" % analoginput)
        _debug("/filter/set -> input: %d  value: %d" % (analoginput, value))
        self.send_raw('S', 'F', analoginput-1, value)

    def cmd_filtertype_get(self, src, reply_id, analoginput):
        """{i} get filtertype for input. 0=no filter, 1=MEDIAN, 2=BESSEL1, 3=BESSEL2"""
        _debug('/filter/get reply_id: %d   analoginput: %d' % (reply_id, analoginput))
        def callback(outvalue, oscserver=self._oscserver, src=src, reply_id=reply_id):
            _debug('/filter/get callback: %d' % outvalue)
            try:
                oscserver.send(src, '/reply', reply_id, outvalue)
            except:
                _error("error by sending outvalue via OSC: %s" % sys.exc_info())
        self.send_with_reply(('G', 'F', analoginput-1), callback) 
        
    def cmd_quit(self):
        _debug('received /quit signal')
        self.stop()

    # ------------------------------------

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
    # ::OSC server
    # --------------------------------------------------------

    def _osc_get_commands(self):
        cmds = [(a, getattr(self, a)) for a in dir(self) if a.startswith('cmd_')]
        out = []
        def parse_cmd(methodname):
            if methodname.endswith('_get'):
                kind = 'ASK'
                path = "/%s/get" % methodname.split('_')[1]    
            elif 'cmd__' in methodname:
                kind = 'META'
                path = '/' + (methodname.split('__')[-1])
            else:
                kind = 'ORD'
                path = methodname.split('_')[1:]
                if len(path) == 1:
                    path = path[0]
                else:
                    path = '/'.join(path)
                path = "/" + path
            return path, kind
        def get_info(method):
            docstr = inspect.getdoc(method)
            if docstr and docstr.startswith("{"):
                    sig, docstr = docstr.split('}')
                    sig, docstr = sig[1:], docstr.strip()
            else:
                sig = None
            return sig, docstr
        for methodname, method in cmds:
            path, kind = parse_cmd(methodname)
            signature, docstr  = get_info(method)
            cmd = dict(methodname=methodname, method=method, path=path, kind=kind, signature=signature, docstr=docstr)
            out.append(cmd)
        return out

    def _create_oscserver(self):
        """Create the OSC server

        Populate the methods with all the commands defined in this class
        (methods beginning with cmd_)

        ==> (the osc-server, a list of added paths)
        """
        _debug("will attempt to create a server at port {port}: {kind}".format(port=self.config['osc_port'], kind="async" if self._oscasync else "sync"))
        if self._oscasync:
            s = liblo.ServerThread(self.config['osc_port'])
        else:
            s = liblo.Server(self.config['osc_port'])
        osc_commands = []
        for cmd in self._osc_get_commands():
            kind, method, path, signature = [cmd[attr] for attr in ('kind', 'method', 'path', 'signature')]
            assert method is not None
            if kind == 'META':
                # functions annotated as meta will be called directly
                _debug('registering osc %s --> %s' % (path, method))
                if self._oscasync:
                    s.add_method(path, None, method)
                else:
                    def handler(path, args, sig, src, callback):
                        _call_later(0, callback, (path, args, sig, src))
                    s.add_method(path, None, handler, method)
            elif cmd['kind'] == 'ASK':
                handler = self._new_getoschandler(method)
                s.add_method(path, None, handler, method)
            else:
                if self._oscasync:
                    def handler(path, args, sig, src, callback):
                        callback(*args)
                else:
                    def handler(path, args, sig, src, callback):
                        _call_later(0, callback, args)
                s.add_method(path, signature, handler, method)
            osc_commands.append(path)
        return s, osc_commands

    def report_oscapi(self):
        lines = []
        cmds = []
        ip, oscport = self.ip, self.config['osc_port']
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
        for cmd in self._osc_get_commands():
            method, path, kind, types, docstr = [cmd[attr] for attr in ('method', 'path', 'kind', 'signature', 'docstr')]
            sign_col_width = 26
            no_sig = " -".ljust(sign_col_width)
            if types and kind != "META":
                args = get_args(method, types)
                signature = ", ".join(args)
                signature = ("(%s)" % signature).ljust(sign_col_width) if signature else no_sig
            elif kind == 'ASK':
                signature = 'reply_id'
                # TODO
            else:
                signature = no_sig
            if not docstr:
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

    def _new_getoschandler(self, method):
        """
        wraps method in an osc-handler which parses the first argument
        as the reply address/id and sends the return value (if present)
        to the caller.
        """
        def wrapper(path, args, types, src, callback):
            if args:
                reply = args[0]
            else:
                reply = 0
            if isinstance(reply, int):
                addr = src
                replyid = reply
            elif isinstance(reply, basestring):
                if '/' not in reply:
                    _error('GET: the first arg. must be a reply id (either an int or of the form ADDRESS/ID). Got: %s' %reply)
                    return 
                else:
                    try:
                        addr, replyid = reply.split('/')
                    except:
                        _error('GET: could not parse reply arg: %s' % reply)
                        return
                    replyid = int(replyid)
                    if ':' in addr:
                        hostname, port = addr
                        port = int(port)
                        addr = (hostname, port)
                    else:
                        addr = (src.hostname, int(addr))
            out = callback(addr, replyid, *args[1:])
            if out is None:
                return
            elif not isinstance(out, (tuple, list)):
                self._oscserver.send(addr, '/reply', replyid, out)
            else:
                self._oscserver.send(addr, '/reply', replyid, *out)
        if self._oscasync:
            return wrapper
        else:
            def wrapper0(path, args, types, src):
                _call_later(0, wrapper, (path, args, types, src))
            return wrapper0

###############################
# ::Helper functions
###############################

def int14_to_bytes(int14):
    """encode int16 into two bytes"""
    b1 = int14 >> 7
    b2 = int14 & 0b1111111
    return b1, b2

def bytes_to_int14(b1, b2):
    return (b1 << 7) + b2

def _get_ip():
    import socket
    return socket.gethostbyname(socket.gethostname())

def _sanitize_osc_address(host, port=None):
    """
    ("hostname", port)
    (port)
    ("hostname:port")
    """
    if port is None:
        # one arg
        if isinstance(host, Number):
            host, port = '127.0.0.1', port
        elif isinstance(host, basestring):
            if ":" in host:
                host, port = host.split(":")
                port = int(port)
            else:
                host, port = '127.0.0.1', int(port)
        elif isinstance(host, tuple):
            host, port = host
        else:
            _error("could not sanitize address: [%s, %s]" % (str(host), str(port)))
            raise ValueError
    if host == "localhost":
        host = "127.0.0.1"
    assert isinstance(port, int)
    assert isinstance(host, basestring)

    return host, port

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

def _is_heartbeat_present(port):
    "return True if the given serial port is transmitting a heartbeat"
    timeout = 1 # this should be long enough so that a heartbeat is detected
    num_attempts = 10
    s = serial.Serial(port, baudrate=BAUDRATE, timeout=timeout)
    N = 10
    s_read = s.read
    while num_attempts >= 0:
        try:
            b = s_read(1)
            if len(b):
                b = ord(b)
                if (b & 0b10000000) and (b & 0b01111111) == 72:  # ord('H')
                    return True
            else:
                # timed out
                pass
        except serial.SerialException:
            # this happens when the device is in an unknown state, during firmware update, etc.
            num_attempts -= 1
        except:
            return False

def _jsondump(d, filename):
    d = util.sort_natural_dict(d)
    json.dump(d, open(filename, 'w'), indent=4)

def _call_regularly(period, function, args=(), kws={}):
    return _scheduler.apply_interval(period*1000, function, args, kws)

def _call_later(deltatime, function, args=(), kws={}):
    return _scheduler.apply_after(deltatime*1000, function, args, kws)

def _add_suffix(p, suffix):
    name, ext = os.path.splitext(p)
    return "".join((name, suffix, ext))

################################
#
# ::Logging
#
################################
class Log:
    def __init__(self, logname='PEDLBRD'):
        self.filename_debug = os.path.join(envir.configpath(), "%s--debug.log" % logname)
        self.filename_info  = os.path.join(envir.configpath(), "%s.log" % logname)
        
        debug_log = logging.getLogger('pedlbrd-debug')
        debug_log.setLevel(logging.DEBUG)
        debug_handler = logging.handlers.RotatingFileHandler(self.filename_debug, maxBytes=80*2000, backupCount=1)
        debug_handler.setFormatter( logging.Formatter('%(levelname)s: -- %(message)s') )
        debug_log.addHandler(debug_handler)
        class FilterDebug(object):
            def filter(self, rec):
                return rec.levelno != logging.INFO
        debug_log.addFilter(FilterDebug())

        info_log = logging.getLogger('pedlbrd-info')
        info_log.setLevel(logging.INFO)
        info_handler = logging.handlers.RotatingFileHandler(self.filename_info, maxBytes=80*500, backupCount=0)
        info_handler.setFormatter( logging.Formatter('%(message)s') )
        info_log.addHandler(info_handler)
        class FilterInfo(object):
            def filter(self, rec):
                return rec.levelno != logging.DEBUG
        info_log.addFilter(FilterInfo())
        
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
    "msg can only be one line"
    logger.info(msg)

def _debug(msg):
    logger.debug(msg)

def _error(msg):
    logger.error(msg)

def _json_editor(jsonfile):
    if sys.platform == 'darwin':
        os.system('open -a "Sublime Text 2" %s' % jsonfile)
    else:
        pass


#################################
# ::Init
#################################

envir.prepare()

try:
    _scheduler = timer2.Timer(precision=0.5)
except:
    raise RuntimeError("Could not start scheduler!")

logger = Log()

# -----------------------------------------------------

if __name__ == '__main__':
    raise RuntimeError("this module cannt be executed!")
