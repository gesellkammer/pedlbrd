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
from collections import namedtuple
from Queue import Queue

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
BAUDRATE = 115200
OSCPORT = 47120
PEDLBRD_ID = 5

DEBUG = False


def _parse_errorcodes(s):
    out = {}
    for line in s.splitlines():
        if not line:
            continue
        try:
            _, line = line.split("#define")
        except ValueError:
            raise ValueError("could not parse errorcode: " + line)
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


class DeviceNotFound(BaseException):
    pass


class OSCPortUsed(BaseException): 
    pass

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
        _debug("searching for heartbeat on port %s" % port)
        if _is_heartbeat_present(port):
            _debug("found heartbeat!")
            return port
        else:
            _debug("found port %s, but no heartbeat detected" % port)
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


class Configuration(dict):
    """
    This implements a dictionary which will notify a registered callback
    when a change is made.
    It also supports subdictionaries (very similar to 'notifydict')
    """
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
        self._callback_enabled = True
        self.state = {'saved': False, 'changed': True}

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
            raise ValueError("the path must be a string of the type key1/key2/..."
                             " or a seq [key1, key2, ...]")
        d = self
        if len(keys) == 0:
            self[path] = value
            self.state['changed'] = True
            if self._callback_enabled:
                self.callback(path, value)
        else:
            for key in keys[:-1]:
                v = d.get(key)
                if isinstance(v, dict):
                    d = v
                else:
                    raise KeyError("set -- key not found: [%s]" % str(key))
            d[keys[-1]] = value
            self.state['changed'] = True
            if self._callback_enabled:
                self.callback(path, value)

    def midi_mapping_for_label(self, label):
        return self['input_mapping'].get(label).get('midi')

# -----------------------------------------------------------------------------------------------------


class Pedlbrd(object):
    def __init__(self, oscport=None, oscasync=None):
        """
        oscasync: run the osc loop async
        """
        envir.prepare()
        self.config, self.configfile = self._load_config()

        self._serialport = None
        self._running = False
        self._status = ''
        self._num_analog_pins = 6
        self._num_digital_pins = 12
        self._analog_resolution_per_pin = [DEFAULTS['max_analog_value'] for i in range(self._num_analog_pins)]
        self._midiout = None
        self._midioutports = set()
        self._oscasync = oscasync if oscasync is not None else self.config['osc_async']
        self._serialtimeout = self.config['serialtimeout_async'] if oscasync else self.config['serialtimeout_sync']
        self._dispatch_funcs_by_pin = {}
        self._analog_funcs  = [None for i in range(self._num_analog_pins)]
        self._digital_funcs = [None for i in range(self._num_digital_pins)]
        self._digital_inverted = [False for i in range(self._num_digital_pins)]
        self._handlers = {}
        self._serialconnection = None
        self._oscserver = None
        self._oscapi = None
        self._midithrough_ports = set()
        self._midithrough_index = 0  # <----- this reflects the last selected midithrough port
        self._ip = None
        self._callbackreg = {}
        self._first_conn = True
        self._digitalinput_needs_calibration = [False for i in range(self._num_digital_pins)]
        self._osc_data_addresses = []
        self._osc_ui_addresses = []
        self._replyid = 0
        self._osc_reply_addresses = set()
        self._device_info = {}
        self._midiout_openports = []
        self._midi_analog_lastvalues = [0 for i in range(self.config['num_analog_pins'])]

        self.logger = Log()
        self._scheduler = timer2.Timer(precision=0.5)
        self.reset_state()
        self._cache_update()
        self._oscserver, self._oscapi = self._create_oscserver()
        if self._oscserver is None:
            raise OSCPortUsed("Could not create OSC server."
                              "Check if a previous crash has not left a zombie")
        if self._oscasync:
            self.logger.debug("starting oscserver async")
            self._oscserver.start()
        else:
            self.logger.debug("osc is in sync mode")

        # Here we actually try to connect to the device.
        # If firsttime_retry_period is possitive, it will 
        # block and wait for device to show up
        self._prepare_connection()
        self.report()
        if self.config.get('open_log_at_startup', False):
            self.open_log()
        REG['logger'] = self.logger
        self.logger.debug("configfile: %s" % self.configfile)

    def _call_regularly(self, period, function, args=(), kws={}):
        return self._scheduler.apply_interval(period*1000, function, args, kws)

    def _call_later(self, deltatime, function, args=(), kws={}):
        return self._scheduler.apply_after(deltatime*1000, function, args, kws)

    #####################################################
    #
    #          P U B L I C    A P I
    #
    #####################################################

    def reset_state(self):
        """
        * Reset the normalization values for analog pins
        * Reset the polarity of the digital pins
        """
        self.logger.debug("reset_state --> resetting")
        self._analog_minvalues = [
            resolution for resolution in self._analog_resolution_per_pin]
        self._analog_maxvalues = [1] * self._num_analog_pins
        self._analog_autorange = [1] * self._num_analog_pins
        self._input_labels = self.config['input_mapping'].keys()
        self._send_osc_ui('/notify/reset')
        self._led_pattern(15, 50, 45)
        if self._running:
            self.logger.debug("putting RESET in the queue")
            self._msgqueue.put_nowait("RESET")
        else:
            self.logger.debug("finished reset, mainloop is not running")

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
                    self.logger.error(
                        'Device not found, retrying in %0.1f seconds' % retry_period)
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
        # this will exit the mainloop, and _terminate will be called
        self._running = False

    def _send_to_all(self, path, *args):
        addrs = self.config['osc_ui_addresses']
        for addr in addrs:
            libloaddr = liblo.Address(*addr)
            self._oscserver.send(libloaddr, path, *args)
        addrs = self.config['osc_data_addresses']
        for addr in addrs:
            libloaddr = liblo.Address(*addr)
            self._oscserver.send(libloaddr, path, *args)

    def _terminate(self):
        self.logger.debug("- - - - - - - - >>> TERMINATE <<< - - - - - - - - ")
        if self._oscasync:
            self._oscserver.stop()
            time.sleep(0.1)
            self._oscserver.free()

        if self._serialconnection:
            self._serialconnection.close()
        self._midi_turnoff()

        self._save_config()

        for handlername, handler in self._handlers.iteritems():
            self.logger.debug('cancelling handler: %s' % handlername)
            handler.cancel()

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
            self.logger.error(
                "attempted to calibrate digital inputs outside of main loop")
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
                self.logger.error(
                    "the reply_id should be a number of character between 0-127")
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
        self._osc_ui_addresses[:] = \
            [as_liblo_address(addr) for addr in self.config['osc_ui_addresses']]
        self._osc_data_addresses[:] = \
            [as_liblo_address(addr) for addr in self.config['osc_data_addresses']]

    def _cache_update(self):
        if self._running:
            wasrunning = True
            self.stop()
        else:
            wasrunning = False
        self._cache_osc_addresses()
        self._sendraw = self.config['osc_send_raw_data']
        if wasrunning:
            self.start()

    @property
    def ip(self):
        if self._ip is not None:
            return self._ip
        self._ip = ip = _get_ip()
        return ip

    def report(self, log=True):
        lines = [
            "\n\n",
            "- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - ",
            "MIDI       : %s" % self.config['midi_device_name'],
            "PORT       : %s" % self._serialport,
            "OSC IN     : %s, %d" % (self.ip, OSCPORT)
        ]
        osc_data = self.config['osc_data_addresses']
        osc_ui = self.config['osc_ui_addresses']

        def addr_to_str(addr):
            return ("%s:%d" % tuple(addr)).ljust(16)
        if osc_data:
            oscdata_addresses = map(addr_to_str, osc_data)
            lines.append(
                "OSC OUT    : data  ---------> %s" % " | ".join(oscdata_addresses))
        if osc_ui:
            oscui_addresses = map(addr_to_str, osc_ui)
            lines.append(
                "           : notifications -> %s" % " | ".join(oscui_addresses))
        lines.append("- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - ")
        lines.extend(self._lines_report_oscapi())
        lines.extend(self._lines_report_config())
        if log:
            report = '\n'.join(lines)
            self.logger.info(report)
        else:
            return lines

    def _digitalmapstr(self, chars="0X"):
        out = [chars[inverted] for inverted in self._digital_inverted]
        return ''.join(out)

    def _lines_report_config(self):
        return []

    def _load_config(self, restore_session=True):
        """
        returns a Configuration and the path loaded
        """
        configdict = DEFAULT_CONFIG
        savedconfig, configfile = envir.config_load()
        if savedconfig: 
            keys_to_retrieve = [
                "midichannel"
            ]   
            for key in keys_to_retrieve:
                value = savedconfig.get(key, None)
                if value is not None:
                    configdict[key] = value
        configuration = Configuration(configdict, callback=self._configchanged_callback)
        return configuration, configfile

    def __del__(self):
        self.stop()

    def _prepare_connection(self):
        retry_period = self.config['firsttime_retry_period']
        accept_fail = self.config['firsttime_accept_fail']
        if accept_fail:
            serialport = self.find_device(retry_period=0)
        else:
            serialport = self.find_device(retry_period=retry_period)
            if not serialport:
                raise DeviceNotFound('PEDLBRD device not found. Aborting')
        self._serialport = serialport

    def _create_dispatch_func(self, kind, pin):
        """
        returns a list of functions operating on the pin corresponding to
        the given label
        """
        midiout = self._midiout
        assert midiout is not None
        sendmidi = midiout.send_message
        # ----------------------
        # Digital
        # ----------------------
        if kind == "D":
            inverted = self._digital_inverted[pin]
            cc = pin + 1
            midichan = self.config['midichannel']
            byte1 = 176 + midichan

            def callback(value):
                if inverted:
                    value = 1 - value
                sendmidi((byte1, cc, value*127))
                self._send_osc_data('/data/D', pin, value)
                return value
            return callback

        # --------------
        # Analog
        # --------------
        if kind == "A":
            normalize = self._gen_normalize(pin)
            oscsend = self._oscserver.send
            addresses = self._osc_data_addresses
            midi_lastvalues = self._midi_analog_lastvalues
            midi_channel = self.config.get('midichannel', 0)
            byte1 = 176 + midi_channel
            cc = 101 + pin

            def callback(value):
                normvalue = normalize(value)
                # normalize returns -1 if the pin is not active
                if normvalue < 0:
                    return
                midivalue = int(normvalue*127+0.5)
                if midivalue > 127:
                    midivalue = 127
                if midivalue != midi_lastvalues[pin]:
                    midi_lastvalues[pin] = midivalue
                    sendmidi((byte1, cc, midivalue))

                # we send the normalized data as 32bit float, which is 
                # more than enough for the ADC resolution of any sensor, 
                # and ensures compatibility with osc implementations 
                # such as PD, which only interprets floats as 32 bits
                for address in addresses:
                    oscsend(address, '/data/A', pin, ('f', normvalue), ('i', value))
                return value
            return callback

    def _update_dispatch_funcs(self):
        for analog_pin in range(4):
            self._input_changed("A", analog_pin)
        for digital_pin in range(10):
            self._input_changed("D", digital_pin)

    def _input_changed(self, kind, pin):
        f = self._create_dispatch_func(kind, pin)
        if kind == 'A':
            self._analog_funcs[pin] = f
        else:
            self._digital_funcs[pin] = f

    def _update_handlers(self):
        for handler in self._handlers.items():
            handler.cancel()
        self._handlers = {}
        time.sleep(0.05)
        autosave_config_period = self.config.setdefault('autosave_config_period', 31)
        if autosave_config_period:
            self._handlers['save_config'] = \
                self._call_regularly(autosave_config_period, self._save_config)

    # ***********************************************
    #
    # *           M A I N L O O P                   *
    #
    # ***********************************************

    def _mainloop(self, async):
        self.logger.debug(
            "starting mainloop in %s mode" % ("async" if async else "sync"))
        if async:
            self.logger.error("This is currently not supported!!!")
            import threading
            self._thread = th = threading.Thread(target=self.start, kwargs={'async':False})
            th.daemon = True
            th.start()
            return

        self._msgqueue = Queue()
        self._midi_turnon()
        self._update_handlers()
        time_time = time.time
        digitalinput_needs_calibration = self._digitalinput_needs_calibration

        osc_recv_inside_loop = not self._oscasync
        self.logger.debug(
            "osc will be processed %s" % ("sync" if osc_recv_inside_loop else "async"))

        self._running = True
        self._set_status('STARTING')

        # if the mainloop is active without time out for this interval, 
        # it will be interrupted
        bgtask_checkinterval = self.config['sync_bg_checkinterval']
        # do background tasks after this time of idle (no data comming from the device)
        idle_threshold = self.config['idle_threshold'] 
        button_short_click = self.config['reset_click_duration']
        if osc_recv_inside_loop:
            oscrecv = self._oscserver.recv

        def serial_read(serial, numbytes):
            msg = serial.read(numbytes)
            if len(msg) != numbytes:
                raise IOError
            return msg
        self._update_dispatch_funcs()

        self.logger.info("\n>>> started listening!")
        while self._running:
            try:
                ok = self._connect()
                if not ok:
                    self.stop()
                    break
                # stupid optimizations
                s = self._serialconnection
                s_read = s.read
                _ord, _len = ord, len
                last_heartbeat = bgtask_lastcheck = last_idle = button_pressed_time = time_time()
                connected = True
                needs_reset = False

                # CACHE
                self._update_dispatch_funcs()
                analog_funcs = self._analog_funcs
                digital_funcs = self._digital_funcs

                while self._running:
                    b = s_read(1)
                    now = time_time()
                    if not _len(b):
                        # serial timedout: IDLE
                        if osc_recv_inside_loop:
                            oscrecv(0)
                        if (now - last_idle) > idle_threshold:
                            self._midioutports_check_changed()
                            last_idle = now
                        if not self._msgqueue.empty():
                            msg = self._msgqueue.get()
                            if msg == "RESET":
                                self.logger.debug("******** got RESET message")
                                needs_reset = True
                                break
                            elif msg == "UPDATE":
                                self._update_dispatch_funcs()
                                analog_funcs = self._analog_funcs
                                digital_funcs = self._digital_funcs
                            else:
                                self.logger.error("got unknown message in the msgqueue: %s" % str(msg))
                        continue
                    b = _ord(b)
                    if not(b & 0b10000000):
                        continue
                    # check also when on heavy load
                    if (now - bgtask_lastcheck) > bgtask_checkinterval:
                        bgtask_lastcheck = now
                        if osc_recv_inside_loop:
                            oscrecv(0)
                    cmd = b & 0b01111111
                    # -------------
                    #   ANALOG
                    # -------------
                    if cmd == 65:  # --> A(nalog)
                        msg = s_read(3)
                        if len(msg) != 3:
                            self.logger.debug('timed out while reading analog message, dropping it')
                            continue
                        param = _ord(msg[0])
                        value = _ord(msg[1])*128 + _ord(msg[2])
                        func = analog_funcs[param]
                        func(value)
                    # -------------
                    #    DIGITAL
                    # -------------
                    elif cmd == 68:  # --> D(igital)
                        msg = s_read(2)
                        if _len(msg) != 2:
                            self.logger.debug('timed out while parsing digital message, dropping it')
                            continue
                        param = _ord(msg[0])
                        value = _ord(msg[1])
                        if digitalinput_needs_calibration[param]:
                            self._digital_inverted[param] = bool(value)
                            digitalinput_needs_calibration[param] = False
                        else:
                            func = digital_funcs[param]
                            func(value)

                    # -------------
                    #   HEARTBEAT
                    # -------------
                    elif cmd == 72:  # --> H(eartbeat)
                        last_heartbeat = now
                        if not connected:
                            self._notify_connected()
                            self._get_device_info()
                            connected = True
                    # -------------
                    #   BUTTON
                    # -------------
                    elif cmd == 66:  # --> B(utton)
                        msg = s_read(2)
                        if _len(msg) != 2:
                            self.logger.debug('serial BUTTON: timed out while parsing button message, dropping it')
                            continue
                        param = _ord(msg[0])
                        value = _ord(msg[1])
                        if value == 1:
                            button_pressed_time = now
                        elif value == 0:
                            self.calibrate_digital()
                            if now - button_pressed_time > button_short_click:
                                self.reset_state()
                    # -------------
                    #    REPLY
                    # -------------
                    elif cmd == 82:  # --> R(eply)
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
                    elif cmd == 69:  # --> E(rror)
                        errorcode = _ord(s_read(1)) * 128 + _ord(s_read(1))
                        error = ERRORCODES.get(errorcode)
                        self.logger.error("ERRORCODE: %d %s" % (errorcode, str(error)))
                    # -------------
                    #     INFO
                    # -------------
                    elif cmd == 73:  # --> I(nfo)
                        try:
                            data = serial_read(s, 6)
                            replyid, dev_id, max_digital_pins, max_analog_pins, num_digital_pins, num_analog_pins = map(_ord, data)
                            enabled_pins_digital = map(_ord, serial_read(s, num_digital_pins))
                            enabled_pins_analog  = map(_ord, serial_read(s, num_analog_pins))
                            analog_pins = []
                            for pin in range(num_analog_pins):
                                analog_data = map(_ord, serial_read(s, 5))
                                analog_pins.append(AnalogPin(*analog_data))
                            info = dict(
                                dev_id=dev_id, max_digital_pins=max_digital_pins, max_analog_pins=max_analog_pins, analog_pins=analog_pins,
                                num_digital_pins=num_digital_pins, num_analog_pins=num_analog_pins,
                                enabled_pins_analog=enabled_pins_analog, enabled_pins_digital=enabled_pins_digital
                            )
                            self._device_info.update(info)
                            self._apply_callback(replyid, info)
                            for pin in analog_pins:
                                self._analog_resolution_per_pin[pin.pin] = pin.resolution
                            print info
                        except IOError:
                            self.logger.error("serial INFO: error reading from serial (probably timed out)")
                            continue
                    # -------------
                    #   MESSAGE
                    # -------------
                    elif cmd == 77:  # --> M(essage)
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
                            print map(_ord, msg)
                            self.logger.info('>>>>>> ' + msg)
                        except IOError:
                            self.logger.error("serial MESSAGE: error reading from serial (probably timed out)")
                self.logger.debug("---------------> out of inner loop")
                if needs_reset:
                    continue
                else:
                    break
            except KeyboardInterrupt:   # poner una opcion en config para decidir si hay que interrumpir por ctrl-c
                print "keyboard interrupt!"
                if self.config['stop_on_keyboard_interrupt']:
                    self.stop()
            except OSError:
                print "OSError!"
                self.logger.error("OSError!")
                continue
            except serial.SerialException:
                print "SerialException"
                self.logger.error("SerialException")
                # arduino disconnected -> s.read throws device not configured
                # don't do anything here, it will reconnect on the next loop
                continue
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
        try:
            self._serialconnection.write(s)
        except serial.SerialException:
            self.logger.error("could not write to device! SerialException")

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

    def _save_config(self):
        if not self.config.state['changed'] and self.config.state['saved']:
            self.logger.debug('config unchanged, skipping save')
            return

        def saveit(self=self):
            configfile = self.configfile
            assert configfile is not None
            _jsondump(self.config, configfile)
            self.logger.debug('save_config: saving to ' + configfile)
            self.config.state['saved'] = True
            self.config.state['changed'] = False
        saveit()

    def _gen_normalize(self, pin):
        maxvalues = self._analog_maxvalues
        minvalues = self._analog_minvalues
        if self._analog_autorange[pin]:
            def func(value):
                maxvalue = maxvalues[pin]
                minvalue = minvalues[pin]
                if value > maxvalue:
                    maxvalues[pin] = value
                    value = 1
                elif value >= minvalue:
                    value = (value - minvalue) / (maxvalue - minvalue)
                else:
                    minvalues[pin] = value
                    value = 0
                if maxvalue - minvalue > 10:
                    return value
                return -1
        else:
            def func(value):
                maxvalue = maxvalues[pin]
                minvalue = minvalues[pin]
                value2 = (value - minvalue) / (maxvalue - minvalue)
                if value2 > 1:
                    value2 = 1
                elif value2 < 0:
                    value2 = 0
                return value2
        return func

    def _normalize(self, pin, value):
        """
        pin here refers to the underlying arduino pin
        value returned is 0-1
        """
        maxvalue = self._analog_maxvalues[pin]
        minvalue = self._analog_minvalues[pin]
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
        for port in self._midithrough_ports:
            midiout.open_port(port)
        self._midiout = midiout
        self._midioutports = self._midiout.ports

    def _midi_turnoff(self):
        if self._midiout is not None:
            self._midiout.close_port()
            self._midiout = None

    def _midithrough_set(self, wildcard_or_index, value):
        self._midithrough_index = wildcard_or_index+1  # 0 is no ports selected
        if value == 1:
            if wildcard_or_index not in self._midithrough_ports:
                print "connecting to", wildcard_or_index
                self._midiout.open_port(wildcard_or_index)
                print "open ports:", self._midiout._openedports
                self._midithrough_ports.add(wildcard_or_index)
        else:
            if not isinstance(wildcard_or_index, int):
                self.logger.error("midithrough ports can only be unset by index")
            else:
                self._midithrough_ports.discard(wildcard_or_index)
                self._midiout.close_port()
                for port in self._midithrough_ports:
                    self._midiout.open_port(port)

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
        conn_found = False
        if not reconnect_period:
            self.stop()
        else:
            self._notify_disconnected()
            self.logger.debug("....looking for device")
            while self._running:
                try:
                    port = detect_port()
                    if port:
                        self._serialport = port
                        conn_found = True
                        break
                    else:
                        self.logger.debug("----> port NOT FOUND. Attempting again in %.2f seconds" % reconnect_period)
                        time.sleep(reconnect_period)
                except KeyboardInterrupt:
                    break
        if conn_found:
            self._serialconnection = serial.Serial(self.serialport, baudrate=BAUDRATE, timeout=self._serialtimeout)
            self._notify_connected()
            self._call_later(2, self._get_device_info)
            self._call_later(3, lambda self: setattr(self, '_first_conn', False), (self,))
            if self.config['autocalibrate_digital']:
                self._call_later(2.5, self.calibrate_digital)
            if self.config['reset_after_reconnect']:
                self.reset_state()
        return conn_found

    def _get_device_info(self):
        def callback(infodict):
            p = self.logger.info
            for k, v in infodict.iteritems():
                p("{0}: {1}".format(k, v))
        self.send_to_device(('G', 'I'), callback)

    def _configchanged_callback(self, key, value):
        self.logger.debug('changing config %s=%s' % (key, str(value)))
        paths = key.split("/")
        paths0 = paths[0]
        if paths0 == 'input_mapping':
            label = paths[1]
            kind = label[0]
            pin = int(label[1:])
            self._input_changed(kind, pin)
        elif paths0 == 'osc_send_raw_data':
            self._sendraw = value
            self.logger.debug('send raw data: %s' % (str(value)))
        elif paths0 == 'osc_data_addresses' or paths0 == 'osc_ui_addresses':
            self._cache_osc_addresses()

    def _midioutports_check_changed(self, notify=True):
        self.logger.debug(">>>> checking midioutports")
        portsnow = set(self._midiout.ports)
        if portsnow != self._midioutports:
            self._midioutports = portsnow
            self.logger.debug("midioutports changed: %s" % str(portsnow))
            if notify:
                #self._call_later(0.1, lambda:self._send_osc_data("/midioutports", *self._midioutports))
                self._send_osc_data("/midioutports", *self._midioutports)
            return True
        return False

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
            pin = int(label[1:])
            #path = "input_mapping/%s/inverted" % label
            path = "D%d/inverted" % pin
            value = bool(value)
            self.config.set(path, value)

    def cmd_smoothing_get(self, src, reply_id, analoginput):
        """{i} returns the analog smoothing percentage"""
        if analoginput < 1:
            self.logger.error("analoginpurt should be 1 or more")
            return
        return ForwardReply(('G', 'S', analoginput-1))


    def _update_mainloop(self):
        self._msgqueue.put_nowait("UPDATE")

    def cmd_midichannel_set(self, channel):
        """{i} Set the midichannel (0-15)"""
        if 0 <= channel <= 15:
            self._update_dispatch_funcs()
            self.config.set("midichannel", channel)
            self._send_osc_ui("/changed/midichannel", channel)
            self._update_mainloop()

    def cmd_midithrough_set(self, wildcard_or_index, value):
        """If int, the index of the midiport
           If string, the name of the midiport (or a wildcard to match)
           value: 1 to enable, 0 to disable
        """
        self._midithrough_set(wildcard_or_index, value)

    def cmd_midithrough_get(self, src, reply_id):
        self.logger.debug("/midithrough/get  --> %d" % self._midithrough_index)
        return self._midithrough_index

    def cmd_midioutports_get(self, src, reply_id):
        self.logger.debug("midioutports: %s" % ", ".join(self._midiout.ports))
        return self._midiout.ports

    def cmd_simulate(self, pin, value):
        """Simulate the value of a pin. A0-A3, D0-D9"""
        self.logger.debug("simulate got: %s" % str([pin, value]))
        self._update_dispatch_funcs()
        kind = pin[0]
        pin_number = int(pin[1:])
        if kind == 'A' and pin_number < (len(self._analog_funcs) - 1):
            func = self._analog_funcs[pin_number]
            if func:
                func(value)
            else:
                self.logger.debug("no analog function for the given pin")
        else:
            self.logger.debug("Wrong pin: got %s" % pin)

    def cmd_testblink(self, numblink, period, dur):
        """{iii}Produce a blink pattern on the device"""
        self._led_pattern(numblink, period, dur)

    def cmd_resetstate(self):
        """
        reset state, doesn't change config
        """
        self.calibrate_digital()
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

    def cmd_logfile_get(self, *args):
        """Returns the path to the logfile"""
        return self.logger.filename_debug

    def cmd__registerui(self, path, args, types, src, report=True):
        """register for notifications. optional arg: address to register"""
        addresses = self.config.get('osc_ui_addresses', [])
        addr = _oscmeta_get_addr(args, src)
        if addr not in addresses:
            addresses.append(addr)
            self.config.set('osc_ui_addresses', addresses)
            if report:
                self.report(log=True)

    def _registerdata(self, path, args, types, src, report=True):
        addresses = self.config.get('osc_data_addresses', [])
        addr = _oscmeta_get_addr(args, src)
        self.logger.debug("registering addr for data: %s" % str(addr))
        if addr not in addresses:
            self.logger.debug('registering addr for data: %s' % str(addr))
            addresses.append(addr)
            self.config.set('osc_data_addresses', addresses)
            if report:
                self.report(log=True)

    def cmd__registerdata(self, path, args, types, src, report=True):
        """Register for data. Optional arg: address to register. Call /signout to stop receiving data"""
        return self._registerdata(path, args, types, src, report=report)

    def cmd__register(self, path, args, types, src, report=True):
        """Alias to /registerdata. Call /signout to stop"""
        return self._registerdata(path, args, types, src, report=report)

    def cmd__signout(self, path, args, types, src):
        """Remove observer. Optional: port to signout (defaults to sending port)."""
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
        """{i} Replies with a list of api commands"""
        args = []
        print("/api/get")

        def sanitize(arg):
            if arg is None:
                arg = "-"
            else:
                arg = str(arg)
            return arg
        for cmd in self._osc_get_commands():
            path, types, docstr = [cmd.get(attr) for attr in ('path', 'signature', 'docstr')]
            if show:
                print "{path} {sig} {doc}".format(
                    path=path.ljust(20), sig=(types if types is not None else "-").ljust(6), doc=docstr)
            msg = "#".join(map(sanitize, (path, types, docstr)))
            args.append(msg)
        args.sort()
        return args

    def cmd_devinfo_get(self, src, reply_id):
        def callback(devinfo, src=src, reply_id=reply_id):
            tags = 'dev_id:max_digital_pins:max_analog_pins:num_digital_pins:num_analog_pins'
            info = [devinfo.get(tag) for tag in tags.split(':')]
            self._oscserver.send(src, '/devinfo', tags, *info)
            tags = 'label:resolution:smoothing:filtertype:denoise:autorange:minvalue:maxvalue'
            for pin in devinfo['analog_pins']:
                self._oscserver.send(
                    src, '/devinfo/analogpin', tags,
                    "A%d" % pin.pin, pin.resolution, pin.smoothing, pin.filtertype, pin.denoise,
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
        PING protocol: /ping [optional-return-addr] ID:int
        will always reply to path /pingback on the
        src address if no address is given.
        /pingback should return the ID given in /ping
        Examples: /ping localhost:9000 3456
                  /ping 9000 3456 (uses src.hostname:9000)
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
        analoginput : int --> 0-3
        value : bool
        """
        if value not in (True, False):
            self.logger.error("_analog_autorange_set: value should be a bool")
            return
        pin = analoginput
        label = "A%d" % pin
        if not pintuplet:
            self.logger.error("_analog_autorange_set: analoginput out of range")
            return
        self._analog_autorange[pin] = value
        self.config.set('/input_mapping/{label}/autorange'.format(label=label), value)

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
        if value > self._analog_resolution_per_pin[pin]:
            self.logger.error("analogmaxval: Value outside range")
            return
        try:
            self._analog_maxvalues[pin] = value
            self._analog_autorange_set(analoginput, False)
        except IndexError:
            self.logger.error("Analog input outside range")

    def cmd__report(self, path, args, types, src):
        addr = _oscmeta_get_addr(args, src)
        lines = self.report(log=False)
        for line in lines:
            print line
        self._oscserver.send(addr, '/println', *lines)

    def cmd_status_get(self, src, replyid):
        return self._status

    """
    get protocol: <-- /something/get reply_id *args
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

    def cmd_midichannel_get(self, src, reply_id):
        """{s} midichannel used to send data"""
        return self.config.getpath('midichannel')

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
        return self._analog_resolution_per_pin[pin]

    def cmd_updateperiod_get(self, src, reply_id):
        return ForwardReply(('G', 'U'))

    def cmd_analogresolution_set(self, analoginput, value):
        """{ii} Set the analog resolution of a pin (value between 255-2047)"""
        pin = analoginput - 1
        if 255 <= value <= 2047:
            self._analog_resolution_per_pin[pin] = value
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
        if not(1 <= analoginput < 6):
            self.logger.error("filtertype/set: analoginput out of range: %d" % analoginput)
            return
        self.logger.debug("/filtertype/set -> input: %d  value: %d" % (analoginput, value))
        if isinstance(value, basestring):
            value = {
                'LOWPASS': 0,
                'MEDIAN' : 1,
                'BESSEL1': 2,
                'BESSEL2': 3
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
        if not(0 <= value <= 1):
            self.logger.error("denoise/set: value out of reange")
            return
        self.send_to_device(('S', 'O', analoginput-1, value))

    def cmd_denoise_get(self, src, replyid, analoginput):
        return ForwardReply(('G', 'O', analoginput-1))

    def cmd_quit(self):
        print("comd_quit")
        self.logger.debug('received /quit signal')
        self.stop()

    # ------------------------------------

    def open_log(self, debug=True):
        if sys.platform == 'darwin':
            os.system("open -a Console %s" % self.logger.filename_debug)
        elif sys.platform == 'linux2':
            os.system("xdg-open %s" % self.logger.filename_debug)

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
            signature, docstr = get_info(method)
            cmd = dict(basename=basename, method=method, path=path,
                       kind=kind, signature=signature, docstr=docstr,
                       methodname=methodname)
            out.append(cmd)
        return out

    def _create_oscserver(self, timeout=5):
        """Create the OSC server

        Populate the methods with all the commands defined in this class
        (methods beginning with cmd_)

        ==> (the osc-server, a list of added paths)
        """
        self.logger.debug("will attempt to create a server at port {port}: {kind}".format(
            port=OSCPORT, kind="async" if self._oscasync else "sync"))
        try:
            if self._oscasync:
                s = liblo.ServerThread(OSCPORT)
            else:
                s = liblo.Server(OSCPORT)
        except liblo.ServerError:
            return None, None
        osc_commands = []
        for cmd in self._osc_get_commands():
            kind, method, path, signature, basename = \
                [cmd[attr] for attr in ('kind', 'method', 'path', 'signature', 'basename')]
            assert method is not None
            if kind == 'META':
                # functions annotated as meta will be called directly
                # self.logger.debug('registering osc %s --> %s' % (path, method))
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
            method, path, kind, types, docstr = \
                [cmd[attr] for attr in ('method', 'path', 'kind', 'signature', 'docstr')]
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
        return lines

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
                        self.logger.error(
                            "GET: the first arg. must be a reply id "
                            "(either an int or of the form ADDRESS/ID). Got: %s" 
                            % reply)
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
                    self.logger.error(
                        "GET: expecting a replyid or a string defining " 
                        "the address and replyid, got %s" 
                        % str(reply))
                    return

            self.logger.debug("GET: calling method with addr={addr}"", replyid={replyid}, args={args}".format(
                addr=(addr.hostname, addr.port), replyid=replyid, args=args))
            try:
                out = callback(addr, replyid, *args[1:])
            except:
                error = str(sys.exc_info()[1])
                self.logger.error("error during OSC callback: %s" % str(error))
                self._oscserver.send(addr, '/error', path, error)
                return
            if out is None:
                return

            replypath = '/reply'
            if isinstance(out, ForwardReply):
                def callback(outvalue, addr=addr, replyid=replyid, postfunc=out.postfunc):
                    outvalue = postfunc(outvalue)
                    self._oscserver.send(addr, replypath, methodname, replyid, outvalue)
                self.send_to_device(out.bytes, callback)
            else:
                if not isinstance(out, (tuple, list)):
                    out = (out,)
                self._oscserver.send(addr, replypath, methodname, replyid, *out)
                self._osc_reply_addresses.add(addr)
        return wrapper

###############################
# ::Helper functions
###############################


class ForwardReply(object):
    def __init__(self, bytes, postfunc=None):
        if postfunc is None:
            def postfunc(arg): return arg
        self.bytes = bytes
        self.postfunc = postfunc


class AnalogPin(namedtuple('AnalogPin', 'pin resolutionbits smoothing filtertype denoise')):
    @classmethod
    def fromdata(cls, data):
        pin, resolutionbits, smoothing, filtertype, denoise = map(ord, data)
        return cls(pin=pin, resolutionbits=resolutionbits, smoothing=smoothing, filtertype=filtertype, denoise=denoise)

    @property
    def resolution(self):
        return (1 << self.resolutionbits) - 1


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
        0: 'LOWPASS',
        1: 'MEDIAN',
        2: 'BESSEL1',
        3: 'BESSEL2'
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
    """
    Return True if the given serial port is transmitting a heartbeat

    timeout: how much time to look for the heartbeat
    wait: when a connection is done, the device starts over. Here we account
          for this time, the timeout starts counting after the wait time
    """
    _debug("opening device %s" % port)
    timeout = 3  # wait so much for a heartbeat
    try:
        # This is a hack for some arduino UNOs, which are in an 
        # unknown baudrate sate when the serial interface
        # with the OS crashes
        s = serial.Serial(port, baudrate=57600)
        time.sleep(0.1)
        s.flush()
        s.close()

        s = serial.Serial(port, baudrate=BAUDRATE, timeout=1)
    except OSError:
        # device is busy, probably open by another process
        return False
    _debug("giving time for the device to restart")
    time.sleep(0.75)
    s.flush()
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            b = s.read(1)
            if not len(b):
                _debug("Device is not sending anything...")
                continue  
            b = ord(b)
            _debug("** got char %d" % b)
            if (b & 0b10000000) and (b & 0b01111111) == 72:  # ord('H')
                _debug("got heartbeat, checking ID")
                b = s.read(1)
                if len(b) and ord(b) < 128:
                    device_id = ord(b)
                    if device_id == PEDLBRD_ID:
                        return True
                    else:
                        _debug("Got heartbeat, but identity is %d" % device_id)    
        except serial.SerialException:
            # this happens when the device is in an unknown state, 
            # during firmware update, etc.
            _debug("SerialException!")
            return False
    _debug("Device timed out")


def _jsondump(d, filename):
    d = util.sort_natural_dict(d)
    # json.dump(d, open(filename, 'w'), indent=4)
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
        self.filename_debug = os.path.join(envir.basepath(), "%s--debug.log" % logname)
        debug_log = logging.getLogger('pedlbrd-debug')
        debug_log.setLevel(logging.DEBUG)
        debug_handler = logging.handlers.RotatingFileHandler(self.filename_debug, maxBytes=80*2000, backupCount=1)
        debug_handler.setFormatter(logging.Formatter('%(levelname)s: -- %(message)s'))
        debug_log.addHandler(debug_handler)
        self.logger = debug_log

    def debug(self, msg):
        self.logger.debug(msg)

    def info(self, msg):
        self.logger.info(msg)

    def error(self, msg):
        self.logger.error(msg)


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
