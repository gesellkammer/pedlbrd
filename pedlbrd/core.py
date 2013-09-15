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
import json
from collections import namedtuple as _namedtuple

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
# this can't be configured because it is hardwired in other parts
# (baudrate is defined in the firmware, the oscport is hardwired in the clients)
# The oscport could be configurable if we implemented some sort of
# zeroconf support, which is overkill for this project
#################################
BAUDRATE = 250000
OSCPORT  = 47120

DEBUG = False

def _parse_errorcodes(s):
    out = {}
    for line in s.splitlines():
        _, line = line.split("#DEFINE")
        cmd, value = line.split()
        out[cmd] = int(value)
    return out

# this code is copy-paste from firmware.ino
ERRORCODES = _parse_errorcodes("""
#define ERROR_COMMAND_BUF_OVERFLOW 1
#define ERROR_INDEX 2
#define ERROR_COMMAND_NUMBYTES 3
#define ERROR_VALUE 4
""")

# this works as a registry for global state (the global logger, for instance)
REG = {}

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
    _debug("possible ports: %s" % str(possible_ports))

    if not possible_ports:
        return None
    for port in possible_ports:
        if _is_heartbeat_present(port):
            return port
        else:
            print "found port %s, but the device is not sending its heartbeat.\nIt is either another device, or the device is in debug mode" % port
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
                    self.logger.error("set -- key not found: %s" % key)
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

class Pedlbrd(object):
    def __init__(self, config=None, env=None, restore_session=None, oscasync=None, **kws):
        """
        config: (str) The name of the configuration file
                None to use the default

        restore_session: (bool) Override the directive in config
        """
        envir.prepare()

        self.env = self._load_env(env)
        if restore_session is None:
            restore_session = self.env['restore_session']
        self.config, self.configfile = self._load_config(config, kws, restore_session=restore_session)

        self._labels = self.config['input_definition'].keys()
        self._running = False
        self._status  = ''
        self._analog_resolution = [DEFAULTS['max_analog_value'] for i in range(16)]
        self._midiout = None
        self._oscasync = oscasync if oscasync is not None else self.config['osc_async']
        self._serialtimeout = self.config['serialtimeout_async'] if oscasync else self.config['serialtimeout_sync']
        self._dispatch_funcs_by_pin = {}
        self._analog_funcs  = [None for i in range(16)]
        self._digital_funcs = [None for i in range(64)]
        self._handlers = {}
        self._serialconnection = None
        self._oscserver = None
        self._oscapi = None
        self._midichannel = -1
        self._ip = None
        self._callbackreg = {}
        self._first_conn = True
        self._digitalinput_needs_calibration = [False for i in range(64)]
        self._osc_data_addresses = []
        self._osc_ui_addresses = []
        self._replyid = 0
        self._osc_reply_addresses = set()
        self._device_info = {}

        self.logger = Log()
        self._scheduler = timer2.Timer(precision=0.5)
        self.reset_state()
        self._cache_update()
        self._oscserver, self._oscapi = self._create_oscserver()
        if self._oscasync:
            self._oscserver.start()

        # Here we actually try to connect to the device.
        # If firsttime_retry_period is possitive, it will block and wait for device to show up
        self._prepare_connection()
        self.report()
        if self.config.get('open_log_at_startup', False):
            self.open_log()
        REG['logger'] = self.logger
        
    def _call_regularly(self, period, function, args=(), kws={}):
        return self._scheduler.apply_interval(period*1000, function, args, kws)

    def _call_later(self, deltatime, function, args=(), kws={}):
        return self._scheduler.apply_after(deltatime*1000, function, args, kws)

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
        # TODO: load state from json file
        self._midi_mapping = MIDI_Mapping(self.config)
        self._analog_minvalues = [analog_resolution for analog_resolution in self._analog_resolution]
        self._analog_maxvalues = [1 for i in range(len(self._analog_minvalues))]
        self._analog_autorange = [1 for i in range(len(self._analog_minvalues))]
        self._input_labels = self.config['input_mapping'].keys()
        self._send_osc_ui('/notify/reset')
        self._led_pattern(15, 50, 45)

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
                    self.logger.error('Device not found, retrying in %0.1f seconds' % retry_period)
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
            self.logger.debug("already stopped!")
            return
        self.logger.info("stopping...")
        self._set_status('QUIT')
        self._send_to_all('/quit')
        self._running = False
        # after exiting the mainloop, _terminate will be called

    def _send_to_all(self, path, *args):
        addrs = set()
        addrs.update(self.config['osc_ui_addresses'])
        addrs.update(self.config['osc_data_addresses'])
        addrs.update(self._osc_reply_addresses)
        for addr in addrs:
            self._oscserver.send(addr, path, *args)

    def _terminate(self):
        if self._oscasync:
            self._oscserver.stop()
            time.sleep(0.1)
            self._oscserver.free()

        if self._serialconnection:
            self._serialconnection.close()
        self._midi_turnoff()

        for handlername, handler in self._handlers.iteritems():
            self.logger.debug('cancelling %s' % handlername)
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
            for i in range(len(self._digitalinput_needs_calibration)):
                self._digitalinput_needs_calibration[i] = True
            self.send_to_device(('F'))
        else:
            self.logger.error("attempted to calibrate digital inputs outside of main loop")
            return
        self._send_osc_ui('/notify/calibrate')
        time.sleep(0.2)
        self._led_pattern(3, 110, 100)


    ####################################################
    #
    #          P R I V A T E
    #
    ####################################################

    def _register_callback(self, key, func):
        """
        key: a number from 0-127
        func : a function taking one integer argument
        """
        if isinstance(key, basestring):
            if not len(key) == 1:
                self.logger.error("the reply_id should be a number of character between 0-127")
                return
            key = ord(key[0])
        self._callbackreg[key] = func

    def _apply_callback(self, key, *args):
        func = self._callbackreg.get(key)
        if func:
            func(*args)
            del self._callbackreg[key]

    def _cache_osc_addresses(self):
        def as_liblo_address(addr):
            if isinstance(addr, (tuple, list)):
                return liblo.Address(*addr)
            else:
                return liblo.Address(addr)
        self._osc_ui_addresses[:]   = [as_liblo_address(addr) for addr in self.config['osc_ui_addresses']]
        self._osc_data_addresses[:] = [as_liblo_address(addr) for addr in self.config['osc_data_addresses']]

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

    def report(self, log=True):
        lines = []
        lines.append("\n\n")
        lines.append("- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - ")
        lines.append("MIDI       : %s" % self.config['midi_device_name'])
        lines.append("PORT       : %s" % self._serialport)
        lines.append("OSC IN     : %s, %d" % (self.ip, OSCPORT))
        osc_data = self.config['osc_data_addresses']
        osc_ui   = self.config['osc_ui_addresses']
        def addr_to_str(addr):
            return ("%s:%d" % tuple(addr)).ljust(16)
        if osc_data:
            oscdata_addresses = map(addr_to_str, osc_data)
            lines.append("OSC OUT    : data  ---------> %s" % " | ".join(oscdata_addresses))
        if osc_ui:
            oscui_addresses = map(addr_to_str, osc_ui)
            lines.append("           : notifications -> %s" % " | ".join(oscui_addresses))
        if self.config == DEFAULT_CONFIG:
            lines.append("CONFIG     : default")
        if self.configfile is not None:
            found, configfile_fullpath = envir.config_find(self.configfile)
            if found:
                configstr = configfile_fullpath
            else:
                configstr = "cloned default config with name: %s (will be saved to %s)" % (self.configfile, configfile_fullpath)
            lines.append("CONFIGFILE : %s" % configstr)
        lines.append("- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - ")
        lines.extend(self._lines_report_oscapi())
        lines.extend(self._lines_report_config())
        if log:
            report = '\n'.join(lines)
            self.logger.info(report)
        else:
            return lines

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

    def _lines_report_config(self):
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
                kind, pin = self.label2pin(label)
                # pin_normalized = mapping['normalized']
                normalize = "NORM" if pin_normalized else ""
                if pin_normalized:
                    normalize = "NORM"

                    maxvalue = ("MAX %d" % self._analog_maxvalues[pin]).ljust(8)
                    minvalue = ("MIN %d" % self._analog_minvalues[pin]).ljust(8)
                else:
                    normalize = ""
                    maxvalue = ""
                in0, in1 = 0, self._analog_resolution[pin]
                out0, out1 = 0, 127
                l = "%s    | %s  CH %2d  CC %3d  (%3d - %4d) -> (%3d - %3d)  %s %s" % (label.ljust(3), normalize.ljust(col2),
                    midi['channel'], midi['cc'], in0, in1, out0, out1, maxvalue, minvalue)
            lines.append(l)
        lines.append("")
        return lines

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
            try:
                env = json.load(open(envpath))
            except ValueError:
                env = DEFAULT_ENV
                _jsondump(env, envpath)
        env['last_loaded_env'] = envpath
        return ChangedDict(env)

    def _save_env(self, force=False):
        if force or self.env.changed:
            envpath = _envpath(self._envname)
            _jsondump(self.env, envpath)
            self.env['last_saved_env'] = envpath
            self.env.check()
            self.logger.debug("saved env to " + envpath)

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
        if not input_mapping:
            self.logger.error("Trying to create a dispatch function for an undefined input (label=%s)" % label)
            return None
        midifunc = self._midi_mapping.construct_func(label)
        midiout = self._midiout
        # ----------------------
        # Digital
        # ----------------------
        labelpin = int(label[1:])
        if label[0] == "D":
            inverted = input_mapping['inverted']
            byte1 = 176 + mapping['channel']

            sendmidi = midiout.send_message
            def callback(value):
                if inverted:
                    value = 1 - value
                sendmidi(midifunc(value))
                self._send_osc_data('/data/D', labelpin, value)
                return value
            return callback

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

        # --------------
        # Analog
        # --------------
        if label[0] == "A":
            sendmidi = midiout.send_message
            kind, pin = self.label2pin(label)
            def callback(value, pin=pin, normalize=self._normalize, oscsend=self._oscserver.send, addresses=self._osc_data_addresses):
                value = normalize(pin, value)
                msg   = midifunc(value)
                if msg:
                    sendmidi(msg)
                # we send the normalized data as 32bit float, which is more than enough for the 
                # ADC resolution of any sensor
                for address in addresses:
                    oscsend(address, '/data/A', labelpin, ('f', value))
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
        self._handlers['save_env'] = self._call_regularly(11, self._save_env)
        time.sleep(0.5)
        autosave_config_period = self.config.setdefault('autosave_config_period', 21)
        if autosave_config_period:
            self._handlers['save_config'] = self._call_regularly(autosave_config_period, self._save_config, kws={'autosave':False})

    # ***********************************************
    #
    # *           M A I N L O O P                   *
    #
    # ***********************************************

    def _mainloop(self, async):
        self.logger.debug("starting mainloop in %s mode" % ("async" if async else "sync"))
        if async:
            self.logger.error("This is currently not supported!!!")
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
        digitalinput_needs_calibration = self._digitalinput_needs_calibration

        osc_recv_inside_loop = not self._oscasync
        if osc_recv_inside_loop:
            oscrecv = self._oscserver.recv
        self.logger.debug("osc will be processed %s" % ("sync" if osc_recv_inside_loop else "async"))

        dlabels = [self.pin2label("D", i) for i in range(self.config['num_digital_pins'])]
        alabels = [self.pin2label("A", i) for i in range(self.config['num_analog_pins'])]

        self._running = True
        self._set_status('STARTING')

        bgtask_checkinterval = self.config['sync_bg_checkinterval']  # if the mainloop is active without time out for this interval, it will be interrupted
        idle_threshold       = self.config['idle_threshold'] # do background tasks after this time of idle (no data comming from the device)
        button_short_click   = self.config['reset_click_duration']

        self.logger.info("\n>>> started listening!")
        def serial_read(serial, numbytes):
            msg = serial.read(numbytes)
            if len(msg) != numbytes:
                raise IOError
            return msg
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
                last_heartbeat = bgtask_lastcheck = last_idle = button_pressed_time = time_time()
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
                        continue
                    b = _ord(b)
                    if not(b & 0b10000000):
                        continue
                    if (now - bgtask_lastcheck) > bgtask_checkinterval:
                        bgtask_lastcheck = now
                        if osc_recv_inside_loop:
                            self._oscserver.recv(5)
                    cmd = b & 0b01111111
                    # -------------
                    #   ANALOG
                    # -------------
                    if cmd == 65: # --> A(nalog)
                        msg = s_read(3)
                        if len(msg) != 3:
                            self.logger.debug('timed out while reading analog message, dropping it')
                            continue
                        param = _ord(msg[0])
                        value = _ord(msg[1])*128 + _ord(msg[2])
                        if sendraw:
                            send_osc_data('/raw', alabels[param], value)
                        func = analog_funcs[param]
                        if func:
                            func(value)
                    # -------------
                    #    DIGITAL
                    # -------------
                    elif cmd == 68: # --> D(igital)
                        msg = s_read(2)
                        if len(msg) != 2:
                            self.logger.debug('timed out while parsing digital message, dropping it')
                            continue
                        param = _ord(msg[0])
                        value = _ord(msg[1])
                        if digitalinput_needs_calibration[param]:
                            label = self.pin2label('D', param)
                            config.set("input_mapping/%s/inverted" % label, bool(value))
                            digitalinput_needs_calibration[param] = False
                        else:
                            if sendraw:
                                send_osc_data('/raw', dlabels[param], value)
                            func = digital_funcs[param]
                            if func:
                                func(value)
                    # -------------
                    #   HEARTBEAT
                    # -------------
                    elif cmd == 72: # --> H(eartbeat)
                        last_heartbeat = now
                        if not connected:
                            self._notify_connected()
                            self._get_device_info()
                            connected = True
                        send_osc_ui('/heartbeat')
                    # -------------
                    #   BUTTON
                    # -------------
                    elif cmd == 66: # --> B(utton)
                        msg = s_read(2)
                        if len(msg) != 2:
                            self.logger.debug('serial BUTTON: timed out while parsing button message, dropping it')
                            continue
                        param = _ord(msg[0])
                        value = _ord(msg[1])
                        if value == 1:
                            button_pressed_time = now
                        elif value == 0:
                            if now - button_pressed_time < button_short_click:
                                self.calibrate_digital()
                            else:
                                self.calibrate_digital()
                                self.reset_state()
                        send_osc_ui('/button', param, value)
                        send_osc_data('/button', param, value)

                    # -------------
                    #    REPLY
                    # -------------
                    elif cmd == 82: # --> R(eply)
                        try:
                            msg = serial_read(s, 3)
                            param = _ord(msg[0])
                            func = self._callbackreg.get(param)
                            if func:
                                value = _ord(msg[1])*128 + _ord(msg[2])
                                try:
                                    func(value)
                                except:
                                    self.logger.error("error in callback registered to {param}. error: {error}".format(param=param, error=sys.exc_info()))
                                del self._callbackreg[param]
                            else:
                                # discard reply
                                value = _ord(msg[1])*128 + _ord(msg[2])
                                self.logger.debug('no callback for param %d, value: %d' % (param, value))
                        except IOError:
                            self.logger.error("serial REPLY: error reading from serial (probably timed out)")
                    # -------------
                    #    ERROR
                    # -------------
                    elif cmd == 69: # --> E(rror)
                        errorcode = _ord(s_read(1)) * 128 + _ord(s_read(1))
                        error  = ERRORCODES.get(errorcode)
                        self.logger.error("ERRORCODE: %d %s" % (errorcode, str(error)))
                    # -------------
                    #     INFO
                    # -------------
                    elif cmd == 73: # --> I(nfo)
                        try:
                            data = serial_read(s, 6)
                            replyid, dev_id, max_digital_pins, max_analog_pins, num_digital_pins, num_analog_pins = map(_ord, data)
                            enabled_pins_digital = map(_ord, serial_read(s, num_digital_pins))
                            enabled_pins_analog  = map(_ord, serial_read(s, num_analog_pins))
                            analog_pins = []
                            for pin in range(num_analog_pins):
                                analog_data = map(_ord, serial_read(s, 5))
                                analog_pins.append( AnalogPin(*analog_data) )
                            info = dict(
                                dev_id=dev_id, max_digital_pins=max_digital_pins, max_analog_pins=max_analog_pins, analog_pins=analog_pins, 
                                num_digital_pins=num_digital_pins, num_analog_pins=num_analog_pins,
                                enabled_pins_analog=enabled_pins_analog, enabled_pins_digital=enabled_pins_digital
                            )
                            self._device_info.update(info)
                            self._apply_callback(replyid, info)
                            for pin in analog_pins:
                                self._analog_resolution[pin.pin] = pin.resolution
                            print info
                        except IOError:
                            self.logger.error("serial INFO: error reading from serial (probably timed out)")
                            continue
                    # -------------
                    #   MESSAGE
                    # -------------
                    elif cmd == 77: # --> M(essage)
                        try:
                            numchars = _ord(serial_read(1))
                            msg = []
                            for i in xrange(numchars):
                                ch = serial_read(1)
                                if _ord(ch) > 127:
                                    self.logger.error("Message two short!")
                                    continue
                                msg.append(ch)
                            msg = ''.join(msg)
                            print ">>>> ", msg
                            print map(_ord, msg)
                            self.logger.info('>>>>>> ' + msg)
                        except IOError:
                            self.logger.error("serial MESSAGE: error reading from serial (probably timed out)")
                # we stopped
                break
            except KeyboardInterrupt:   # poner una opcion en config para decidir si hay que interrumpir por ctrl-c
                break
            except OSError, serial.SerialException:
                # arduino disconnected -> s.read throws device not configured
                # don't do anything here, it will reconnect on the next loop
                pass
        self._terminate()

    def send_to_device(self, bytes, callback=None):
        """
        send an arbitrary array of bytes to the device over serial

        * If you add a callback, it will be registered to the replyid given
        (the replyid is always the last byte in a message expecting a replyid)

        bytes: a seq of bytes (either chars or numbers between 0-127)
        """
        if not self._serialconnection:
            self.logger.error("tried to write to serial before the connection was established")
            return
        intbytes = [(ord(b) if isinstance(b, str) else b) for b in bytes]
        if any(not(0 <= byte <= 127) for byte in intbytes):
            self.logger.error("send_to_device: value outside of range (0-127). Data will NOT be sent. %s" % str(intbytes))
            return
        if callback is not None:
            replyid = self._new_replyid()
            self._register_callback(replyid, callback)
            intbytes.append(replyid)
        intbytes.append(128)
        bytes2 = map(chr, intbytes)
        s = ''.join(bytes2)
        self.logger.debug("send_to_device. received %d bytes (%s), sending %d bytes (%s)sending raw bytes -> %s" % (len(bytes), bytes, len(bytes2), bytes2, str(map(ord, bytes2))))
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
            self.logger.debug('status: %s' % status)
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
            self.logger.debug('config unchanged, skipping save')
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
            self.logger.debug('saving to ' + abspath)
            self.config.state['saved'] = True
            self.config.state['changed'] = False
        self._call_later(0, saveit)

    def edit_config(self):
        if self.configfile and os.path.exists(self.configfile):
            _json_editor(self.configfile)
        else:
            self.logger.error("could not find a config file to edit")

    def _normalize(self, pin, value):
        """
        pin here refers to the underlying arduino pin
        value returned is 0-1
        """
        maxvalue  = self._analog_maxvalues[pin]
        minvalue  = self._analog_minvalues[pin]
        if self._analog_autorange[pin]:
            if minvalue <= value <= maxvalue:
                value2 = (value - minvalue) / (maxvalue - minvalue)
            elif value > maxvalue:
                self._analog_maxvalues[pin] = value
                value2 = 1
            else:
                self._analog_minvalues[pin] = value
                value2 = 0
            return value2 
        else:
            value2 = (value - minvalue) / (maxvalue - minvalue)
            if value2 > 1: 
                value2 = 1
            elif value2 < 0:
                value2 = 0
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
        self.logger.info(msg)
        self._set_status('DISCONNECTED')

    def _notify_connected(self):
        msg = "CONNECTED!"
        self.logger.info(msg)
        self._set_status('CONNECTED')

    def _connect(self):
        """
        attempt to connect.

        True if successful, False if no reconnection possible
        """
        reconnect_period = self.config['reconnect_period_seconds']
        self.logger.debug('attempting to connect')
        if not reconnect_period:
            self.stop()
            out = False
        else:
            self._notify_disconnected()
            conn_found = False
            while self._running:
                try:
                    self.logger.debug("....looking for port")
                    port = detect_port()
                    if port:
                        self._serialport = port
                        conn_found = True
                        break
                    else:
                        self.logger.debug("....port NOT FOUND. Attempting again in %.2f seconds" % reconnect_period)
                        time.sleep(reconnect_period)
                except KeyboardInterrupt:
                    break
        if conn_found:
            self._serialconnection = serial.Serial(self.serialport, baudrate=BAUDRATE, timeout=self._serialtimeout)
            self._notify_connected()
            self._call_later(4, self._get_device_info)
            self._call_later(8, lambda self: setattr(self, '_first_conn', False), (self,))
            if self.config['autocalibrate_digital']:
                self._call_later(2, self.calibrate_digital)
            if self.config['reset_after_reconnect']:
                self.reset_state()
        return conn_found

    def _get_device_info(self):
        self.send_to_device(('G', 'I'))

    def _configchanged_callback(self, key, value):
        self.logger.debug('changing config %s=%s' % (key, str(value)))
        paths = key.split("/")
        paths0 = paths[0]
        if paths0 == 'input_mapping':
            label = paths[1]
            self._input_changed(label)
            if "channel" in key:
                self._update_midichannel()
        elif paths0 == 'osc_send_raw_data':
            self._sendraw = value
            self.logger.debug('send raw data: %s' % (str(value)))
        elif paths0 == 'osc_data_addresses' or paths0 == 'osc_ui_addresses':
            self._cache_osc_addresses()

    def _new_replyid(self):
        self._replyid += 1
        self._replyid = (self._replyid % 127) + 1 # numbers between 1 and 127
        return self._replyid

    def _led_pattern(self, numblink, period_ms, dur_ms):
        """
        blink the device
        """
        msg = ['L']
        msg.extend(int14tobytes(numblink))
        msg.extend(int14tobytes(period_ms))
        msg.extend(int14tobytes(dur_ms))
        self.send_to_device(msg)

    # -------------------------------------------
    # ::External API
    #
    # methodname: cmd_cmdname_[get/set]
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
        """{i} returns the analog smoothing percentage"""
        if analoginput < 1:
            self.logger.error("analoginpurt should be 1 or more")
            return
        return ForwardReply(('G', 'S', analoginput-1))

    def cmd_midichannel_set(self, label, channel):
        """{si} Set the midichannel. label can be a wildcard
                --use "*" to set the channel for all inputs, "A?" to change all analog inputs
        """
        labels = self._match_labels(label)
        if not 0 <= channel < 16:
            self.logger.error("channel should be between 0-15, got %d" % channel)
            return
        for label in labels:
            path = 'input_mapping/%s/midi/channel' % label
            self.config.set(path, channel)

    def cmd_midicc_set(self, label, cc):
        """{si}set the CC of input"""
        path = 'input_mapping/%s/midi/cc' % label
        if not 0 <= cc < 128:
            self.logger.error(
                "midi CC should be between 0 and 127, got %d" % cc
            )
            return
        if not label in self._labels:
            self.logger.error("/midicc/set: label must be one of %s" % str(self._labels))
            return
        self.config.set(path, cc)

    def cmd_midicc_get(self, src, reply_id, label):
        """{s} Get the CC mapping for label"""
        self.logger.debug("/midicc/get %s" % label)
        cc = self.config.getpath('input_mapping/%s/midi/cc' % label)
        if cc is None:
            self.logger.error('could not get midicc for label: %s' % label)
            return
        return cc

    def cmd_testblink(self, numblink, period, dur):
        """{iii}Produce a blink pattern on the device"""
        self._led_pattern(numblink, period, dur)

    def cmd_resetstate(self):
        """
        reset state, doesn't change config
        """
        self.reset_state()
        self.logger.debug('reset state!')

    def cmd_resetconfig(self):
        """reset config to default values"""
        self.config_restore_defaults()
        self.logger.debug('config reset to defaults')

    def cmd_calibrate(self):
        """calibrate digital inputs"""
        self.logger.debug('calibrating...')
        self.calibrate_digital()
        self.report(log=True)

    def cmd_openlog(self, debug=0):
        """{i} if debug is 1, open the debug console"""
        self._call_later(0.1, self.open_log, [bool(debug)])

    def cmd_logfile_get(self):
        """Returns a tagged tuplet with the paths of the logfiles"""
        return "info:debug", self.logger.filename_info, self.logger.filename_debug

    def cmd__registerui(self, path, args, types, src, report=True):
        """register for notifications. optional arg: address to register"""
        addresses = self.config.get('osc_ui_addresses', [])
        addr = _oscmeta_get_addr(args, src)
        if addr not in addresses:
            addresses.append(addr)
            self.config.set('osc_ui_addresses', addresses)
            if report:
                self.report(log=True)

    def cmd__registerdata(self, path, args, types, src, report=True):
        """Register for data. optional arg: address to register"""
        addresses = self.config.get('osc_data_addresses', [])
        addr = _oscmeta_get_addr(args, src)
        if addr not in addresses:
            self.logger.debug('registering addr for data: %s' % str(addr))
            addresses.append(addr)
            self.config.set('osc_data_addresses', addresses)
            if report:
                self.report(log=True)

    def cmd__registerall(self, path, args, types, src):
        """Register to receive data and notifications. Optional: port to register (defaults to sending port)"""
        self.cmd__registerdata(path, args, types, src, report=False)
        self.cmd__registerui(path, args, types, src, report=True)

    def cmd__signout(self, path, args, types, src):
        """Remove observer. Optional: port to signout (defaults to sending port)"""
        addr = _oscmeta_get_addr(args, src)
        ui_addresses = self.config['osc_ui_addresses']
        data_addresses = self.config['osc_data_addresses']
        if addr in ui_addresses:
            ui_addresses.remove(addr)
            self.config.set('osc_ui_addresses', ui_addresses)
        if addr in data_addresses:
            data_addresses.remove(addr)
            self.config.set('osc_data_addresses', data_addresses)

    def cmd_api_get(self, src, reply_id, show=0):
        """{i} replies with a list of api commands"""
        args = []
        def sanitize(arg):
            if arg is None:
                arg = "-"
            else:
                arg = str(arg)
            return arg
        for cmd in self._osc_get_commands():
            path, types, docstr = [cmd.get(attr) for attr in ('path', 'signature', 'docstr')]
            if show:
                print "{path} {sig} {doc}".format(path=path.ljust(20), sig=(types if types is not None else "-").ljust(6), doc=docstr)
            msg = "#".join(map(sanitize, (path, types, docstr)))
            args.append(msg)
        args.sort()
        return args

    def cmd_devinfo_get(self, src, reply_id):
        self.logger.debug("devinfo/get {src} {reply_id}".format(src=src, reply_id=reply_id))
        def callback(devinfo, src=src, reply_id=reply_id):
            tags = 'dev_id:max_digital_pins:max_analog_pins:num_digital_pins:num_analog_pins'
            info = [devinfo.get(tag) for tag in tags.split(':')]
            self._oscserver.send(src, '/devinfo', tags, *info)
            tags = 'label:resolution:smoothing:filtertype:denoise:autorange:minvalue:maxvalue'
            for pin in devinfo['analog_pins']:
                self._oscserver.send(src, '/devinfo/analogpin', tags, 
                    self.pin2label('A', pin.pin), pin.resolution, pin.smoothing, pin.filtertype, pin.denoise, 
                    self._analog_autorange[pin.pin], self._analog_minvalues[pin.pin], self._analog_maxvalues[pin.pin]
                )
        self.send_to_device(('G', 'I'), callback)

    def cmd_analogminval_set(self, analoginput, value):
        """{ii}set minimum raw value for analog input. autorange for this input is disabled"""
        self._analogminval_set(analoginput, value)

    def cmd_autorange_get(self, src, replyid, analoginput):
        return self._analog_autorange[analoginput - 1]

    def cmd__ping(self, path, args, types, src):
        """
        PING protocol: /ping [optional return addr] ID --> will always reply to path /pingback on the 
        src address if no address is given. /pingback should return the ID given in /ping

        ID is an integer 

        /ping 3456 localhost:9000
        /ping 3456 9000 (use src.hostname:9000)
        /ping 3456 (use src.hostname:src.port)
        """
        addr = _oscmeta_get_addr(args, src)
        self._oscserver.send(addr, '/pingback')

    def cmd_pingback(self, ID):
        """{i} ID should be the same received by /ping"""
        if not args:
            self.logger.error("/pingback should return the ID sent by /ping")
            return
        ID = args[0]
        func = self._pingback_registry.get(ID)
        if func:
            try:
                func((src.hostname, src.port))
            except:
                self.logger.error("pingback: error while calling callback function: %s" % str(sys.exc_info))
                return
        else:
            self.logger.debug("pingback: received a pingback but no callback was registered")
            return

    def send_ping(self, addr, callback):
        """
        callback: a function without arguments (just a continuation)
        """
        ID = self._new_pingbackid()
        self._pingback_registry[ID] = callback
        self._oscserver.send(addr, '/ping', ID)

    def _new_pingbackid(self):
        try:
            self._last_pingbackid = (self._last_pingbackid + 1) % 100000
        except AttributeError:
            self._last_pingbackid = 0
        return self._last_pingbackid

    def cmd_autorange_set(self, analoginput, value):
        if value < 0 or value > 1:
            self.logger.error("autorange: value outside range")
            return
        self._analog_autorange_set("A%d" % analoginput, bool(value))

    def _analog_autorange_set(self, analoginput, value):
        """
        analoginput : int --> 1-4 (as in A1-A4)
        value : bool
        """
         if value not in (True, False):
            self.logger.error("_analog_autorange_set: value should be a bool")
            return
        label = "A%d" % analoginput
        pintuplet = self.label2pin(label)
        if not pintuplet:
            self.logger.error("_analog_autorange_set: analoginput out of range")
            return
        _, pin = pintuplet
        self._analog_autorange[pin] = value
        self.config.set('/input_mapping/{label}/autorange'.format(label=label, value)

    def _analogminval_set(self, analoginput, value):
        pin = analoginput - 1
        if value < 0:
            return
        try:
            self._analog_minvalues[pin] = value
            self._analog_autorange_set(analoginput, False)
        except IndexError:
            self.logger.error("Analog input outside range")

    def cmd_analogmaxval_set(self, analoginput, value):
        self._analogmaxval_set(analoginput, value)

    def _analogmaxval_set(self, analoginput, value):
        pin = analoginput - 1
        if value > self._analog_resolution[pin]:
            self.logger.error("analogmaxval: Value outside range")
            return
        try:
            self._analog_maxvalues[pin] = value
            self._analog_autorange_set(analoginput, False)
        except IndexError:
            self.logger.error("Analog input outside range")

    def cmd__echo(self, path, args, types, src):
        """responds to the path '/echo' of the caller with the same arguments"""
        if args:
            self._oscserver.send(src, '/echo', *args)
        else:
            self._oscserver.send(src, '/echo')

    def cmd__report(self, path, args, types, src):
        addr = _oscmeta_get_addr(args, src)
        lines = self.report(log=False)
        self._oscserver.send(addr, '/println', *lines)

    def cmd_status_get(self, src, replyid):
        return self._status

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
        return ForwardReply(('G', 'H'))

    def cmd_digitalmapstr_get(self, src, reply_id):
        """{i}Returns the digital calibration as str"""
        return self._digitalmapstr()

    def cmd_midichannel_get(self, src, reply_id, label):
        """{s} midichannel used to send data for the given input"""
        self.logger.debug("/midichannel/get %s" % label)
        ch = self.config.getpath('input_mapping/%s/midi/channel' % label)
        if ch is None:
            self.logger.error('could not get midichannel for label: %s' % label)
            return
        return ch

    def cmd_addrui_get(self, src, reply_id):
        """OSC addresses for UI information ==> uiaddresses : a space separated string of 'hostname:port'"""
        addresses = self.config['osc_ui_addresses']
        out = ["%s:%d" % (host, port) for hort, port in addresses]
        return out

    def cmd_addrdata_get(self, src, reply_id):
        """OSC addresses for data information ==> a space separated string of 'hostname:port'"""
        addresses = self.config['osc_data_addresses']
        out = ["%s:%d" % (host, port) for host, port in addresses]
        return out

    def cmd_analogresolution_get(self, src, reply_id, analoginput):
        pin = analoginput - 1
        return self._analog_resolution[pin]

    def cmd_updateperiod_get(self, src, reply_id):
        return ForwardReply(('G', 'U'))

    def cmd_analogresolution_set(self, analoginput, value):
        """{ii} Set the analog resolution of a pin (value between 255-2047)"""
        pin = analoginput - 1
        if 255 <= value <= 2047:
            self._analog_resolution[pin] = value
            self.send_to_device(('S', 'A', pin) + int14tobytes(value))

    def cmd_blinking_get(self, src, replyid):
        """blink for each value sent, 0: dont blink"""
        return ForwardReply(('G', 'B'))

    def cmd_blinking_set(self, value):
        """{i}1: blink for each value sent, 0: dont blink"""
        if value == 0 or value == 1:
            self.send_to_device(('S', 'B', value))

    def cmd_delay_get(self, src, replyid):
        """Delay between cycles in the device (ms)"""
        return ForwardReply(('G', 'D'))

    def cmd_delay_set(self, value):
        """{i}Delay between cycles in the device (ms)"""
        self.send_to_device(('S', 'D') + int14tobytes(value))

    def cmd_heartperiod_set(self, value):
        """{i} set the heartbeat period in ms"""
        self.send_to_device(('S', 'H')+int14tobytes(value))

    def cmd_smoothing_set(self, analoginput, percent):
        """{ii} set the analog smoothing (0-100, deault=50). 0=no smoothing"""
        if analoginput < 1:
            self.logger.error("ValueError: /smoothing/set -> analoginput should be 1 or more")
            return
        if not (0 <= percent <= 100):
            self.logger.error("/smoothing/set: ValueError -> percent should be between 0-100")
            return
        self.logger.debug('/smoothing/set %d %d' % (analoginput, percent))
        self.send_to_device(('S', 'S', analoginput-1, percent))

    def cmd_filtertype_set(self, analoginput, value):
        """{ii}Set filtertype for input. 0=LOWPASS 1=MEDIAN 2=BESSEL1 3=BESSEL2"""
        if not( 1 <= analoginput < 6):
            self.logger.error("filtertype/set: analoginput out of range: %d" % analoginput)
            return
        self.logger.debug("/filtertype/set -> input: %d  value: %d" % (analoginput, value))
        if isinstance(value, basestring):
            value = {
                'LOWPASS':0,
                'MEDIAN' :1,
                'BESSEL1':2,
                'BESSEL2':3
            }.get(value.upper())
            if not value:
                self.logger.debug('filtertype must be an int or one of LOWPASS, MEDIAN, BESSEL1 and BESSEL2')
                return
        self.send_to_device(('S', 'F', analoginput-1, value))

    def cmd_filtertype_get(self, src, reply_id, analoginput):
        """{i} get filtertype for input. 0=LOWPASS, 1=MEDIAN, 2=BESSEL1, 3=BESSEL2"""
        self.logger.debug('/filtertype/get reply_id: %d   analoginput: %d' % (reply_id, analoginput))
        return ForwardReply(('G', 'F', analoginput-1), postfunc=_filtertype_as_string)

    def cmd_denoise_set(self, analoginput, value):
        if not(1 <= analoginput < 6):
            self.logger.error("preventosc/set: analoginput out of range: %d" % analoginput)
            return
        if not(0<=value<=1):
            self.logger.error("denoise/set: value out of reange")
            return
        self.send_to_device(('S', 'O', analoginput-1, value))

    def cmd_denoise_get(self, src, replyid, analoginput):
        return ForwardReply(('G', 'O', analoginput-1))

    def cmd_quit(self):
        self.logger.debug('received /quit signal')
        self.stop()

    # ------------------------------------

    def open_log(self, debug=False):
        if sys.platform == 'darwin':
            os.system("open -a Console %s" % self.logger.filename_info)
            if debug:
                os.system("open -a Console %s" % self.logger.filename_debug)
        else:
            self.logger.error("...")
            return

    # --------------------------------------------------------
    # ::OSC server
    # --------------------------------------------------------

    def _osc_get_commands(self):
        cmds = [(a, getattr(self, a)) for a in dir(self) if a.startswith('cmd_')]
        out = []
        def parse_cmd(methodname):
            if methodname.endswith('_get'):
                kind = 'GET'
                basename = methodname.split('_')[1]
                path = "/%s/get" % basename
            elif 'cmd__' in methodname:
                kind = 'META'
                basename = methodname.split('__')[-1]
                path = '/' + basename
            else:
                kind = 'ORD'
                basename = '_'.join(methodname.split('_')[1:])
                path = methodname.split('_')[1:]
                if len(path) == 1:
                    path = path[0]
                else:
                    path = '/'.join(path)
                path = "/" + path
            return path, kind, basename
        def get_info(method):
            docstr = inspect.getdoc(method)
            if docstr and docstr.startswith("{"):
                    sig, docstr = docstr.split('}')
                    sig, docstr = sig[1:], docstr.strip()
            else:
                sig = None
            return sig, docstr
        for methodname, method in cmds:
            path, kind, basename = parse_cmd(methodname)
            signature, docstr  = get_info(method)
            cmd = dict(basename=basename, method=method, path=path,
                       kind=kind, signature=signature, docstr=docstr,
                       methodname=methodname)
            out.append(cmd)
        return out

    def _create_oscserver(self):
        """Create the OSC server

        Populate the methods with all the commands defined in this class
        (methods beginning with cmd_)

        ==> (the osc-server, a list of added paths)
        """
        self.logger.debug("will attempt to create a server at port {port}: {kind}".format(port=OSCPORT, kind="async" if self._oscasync else "sync"))
        if self._oscasync:
            s = liblo.ServerThread(OSCPORT)
        else:
            s = liblo.Server(OSCPORT)
        osc_commands = []
        for cmd in self._osc_get_commands():
            kind, method, path, signature, basename = [cmd[attr] for attr in ('kind', 'method', 'path', 'signature', 'basename')]
            assert method is not None
            if kind == 'META':
                # functions annotated as meta will be called directly
                self.logger.debug('registering osc %s --> %s' % (path, method))
                if self._oscasync:
                    s.add_method(path, None, method)
                else:
                    def handler(path, args, sig, src, callback):
                        self._call_later(0, callback, (path, args, sig, src))
                    s.add_method(path, None, handler, method)
            elif cmd['kind'] == 'GET':
                handler = self._newoschandler_GET(method, basename)
                s.add_method(path, None, handler, method)
            else:
                if self._oscasync:
                    def handler(path, args, sig, src, callback):
                        callback(*args)
                else:
                    def handler(path, args, sig, src, callback):
                        self._call_later(0, callback, args)
                s.add_method(path, signature, handler, method)
            osc_commands.append(path)
        return s, osc_commands

    def _lines_report_oscapi(self):
        lines = []
        cmds = []
        ip, oscport = self.ip, OSCPORT
        msg = "    OSC Input    |    IP %s    PORT %d    " % (ip, oscport)
        lines.append("=" * len(msg))
        lines.append(msg)
        lines.append("=" * len(msg))
        lines2 = []
        def get_args(method, types, exclude=[]):
            argnames = [arg for arg in inspect.getargspec(method).args if arg not in exclude]
            if not types:
                return []
            osc2arg = {
                's': 'str',
                'i': 'int',
                'd': 'double',
                'f': 'float'
            }
            out = ["%s:%s" % (argname, osc2arg.get(argtype, '?')) for argtype, argname in zip(types, argnames)]
            return out
        sign_col_width = 26
        no_sig = " -".ljust(sign_col_width)
        for cmd in self._osc_get_commands():
            method, path, kind, types, docstr = [cmd[attr] for attr in ('method', 'path', 'kind', 'signature', 'docstr')]
            if types and kind != "META":
                args = get_args(method, types, exclude=('self',))
            elif kind == 'GET':
                args = get_args(method, types, exclude=('self', 'src'))
            else:
                args = None
            signature = signature = ("(%s)" % ', '.join(args)).ljust(sign_col_width) if args else no_sig
            docstr = str(docstr)
            l = "%s %s | %s" % (path.ljust(16), signature, docstr)
            lines2.append(l)
        lines2.sort()
        lines.extend(lines2)
        lines.append("\nlabel: identifies the input. Valid labels are: D1-D10, A1-A4")
        lines.append("Example: oscsend %s %d /midicc D2 41" % (ip, oscport))
        lines.append("         oscsend %s %s /midichannel * 2" % (ip, oscport))
        return lines

    def _match_labels(self, pattr):
        out = []
        for label in self._labels:
            if fnmatch.fnmatch(label, pattr):
                out.append(label)
        return out

    def _newoschandler_GET(self, method, methodname=None):
        """
        wraps method in an osc-handler which parses the first argument
        as the reply address/id and sends the return value (if present)
        to the caller.
        """
        def wrapper(path, args, types, src, callback):
            if not args:
                addr = src
                replyid = 0
            else:
                reply = args[0]
                if isinstance(reply, int):
                    addr, replyid = src, reply
                elif isinstance(reply, basestring):
                    if '/' not in reply:
                        self.logger.error('GET: the first arg. must be a reply id (either an int or of the form ADDRESS/ID). Got: %s' %reply)
                        return
                    else:
                        try:
                            addr, replyid = reply.split('/')
                            replyid = int(replyid)
                        except:
                            self.logger.error('GET: could not parse reply arg: %s' % reply)
                            return
                        if ':' in addr:
                            hostname, port = addr.split(':')
                            port = int(port)
                            addr = liblo.Address(hostname, port)
                        else:
                            addr = liblo.Address(src.hostname, int(addr))
                else:
                    self.logger.error("GET: expecting a replyid or a string defining the address and replyid, got %s" % str(reply))
                    return

            self.logger.debug("GET: calling method with addr={addr}, replyid={replyid}, args={args}".format(addr=(addr.hostname, addr.port), replyid=replyid, args=args))
            try:
                out = callback(addr, replyid, *args[1:])
            except:
                error = sys.exc_info()
                self.logger.error("error during OSC callback: %s" % error)
                self._oscserver.send(addr, '/error', path, error)
                return
            if out is None:
                return

            replypath = '/reply/' + methodname
            if isinstance(out, ForwardReply):
                def callback(outvalue, addr=addr, replyid=replyid, postfunc=out.postfunc):
                    outvalue = postfunc(outvalue)
                    self._oscserver.send(addr, replypath, replyid, outvalue)
                self.send_to_device(out.bytes, callback)
            else:
                if not isinstance(out, (tuple, list)):
                    out = (out,)
                self._oscserver.send(addr, replypath, replyid, *out)
                self._osc_reply_addresses.add(addr)
        return wrapper
        
###############################
# ::Helper functions
###############################

def runcore():
    p = Pedlbrd(autostart=False, restore_session=False)
    p.start(async=True)

class ForwardReply(object):
    def __init__(self, bytes, postfunc=None):
        if postfunc is None:
            postfunc = lambda _:_
        self.bytes = bytes
        self.postfunc = postfunc

class AnalogPin(_namedtuple('AnalogPin', 'pin resolutionbits smoothing filtertype denoise')):
    @classmethod
    def fromdata(cls, data):
        pin, resolutionbits, smoothing, filtertype, denoise = map(ord, data)
        return cls(pin=pin, resolutionbits=resolutionbits, smoothing=smoothing, filtertype=filtertype, denoise=denoise)
    @property
    def resolution(self):
        return (1<<self.resolutionbits) - 1

def int14tobytes(int14):
    """encode int16 into two bytes"""
    b1 = int14 >> 7
    b2 = int14 & 0b1111111
    return b1, b2

def bytes_to_int14(b1, b2):
    return (b1 << 7) + b2

def _get_ip():
    import socket
    return socket.gethostbyname(socket.gethostname())

def _oscmeta_get_addr(args, src):
    """
    args, src as passed by liblo to a callback

    allways returns a tuple (hostname, port)
    """
    addr = _sanitize_osc_address(*args)
    if not addr:
        addr = src.hostname, src.port
    return addr

def _filtertype_as_string(filtertype):
    out = {
        0:'LOWPASS',
        1:'MEDIAN',
        2:'BESSEL1',
        3:'BESSEL2'
    }.get(filtertype)
    if out:
        return out
    return filtertype

def _sanitize_osc_address(*args):
    """
    ("hostname", port)
    (port)
    ("hostname:port")

    returns a tuple (host:str, port:number) or None
    """
    if not args:
        return None
    if len(args) == 1:
        if isinstance(args[0], int):
            host, port = "127.0.0.1", args[0]
        elif isinstance(args[0], basestring):
            if ":" in args[0]:
                host, port = args[0].split(":")
                port = int(port)
        else:
            self.logger.error("Expected port, (host, port), or 'host:port'")
            return None
    elif len(args) == 2:
        host, port = args
    else:
        self.logger.error("Too many arguments!")
        return None

    if host == 'localhost':
        host = '127.0.0.1'

    if not isinstance(port, int):
        self.logger.error(
            "port should be int, got {cls}".format(cls=port.__class__)
        )
        return None
    if not isinstance(host, basestring):
        self.logger.error(
            "hostname should be a string, got {cls}".format(cls=host.__class__)
        )
        return None
    return (host, port)

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
    #json.dump(d, open(filename, 'w'), indent=4)
    json.dump(d, open(filename, 'w'))

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
        self.loggers = (debug_log, info_log)

    def debug(self, msg):
        for logger in self.loggers:
            logger.debug(msg)

    def info(self, msg):
        for logger in self.loggers:
            logger.info(msg)

    def error(self, msg):
        for logger in self.loggers:
            logger.error(msg)

def _debug(msg):
    logger = REG.get('logger')
    if logger:
        logger.debug(msg)
    else:
        print "[ DEBUG ]", msg

def _error(msg):
    logger = REG.get('logger')
    if logger:
        logger.error(msg)
    else:
        print "[ ERROR ]", msg

# -----------------------------------------------------

if __name__ == '__main__':
    raise RuntimeError("this module cannt be executed!")
