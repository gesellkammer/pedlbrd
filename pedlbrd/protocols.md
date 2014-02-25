# OSC API

* The pedlbrd driver (core) is a standalone process which communicates with the device via USB and with other processes via OSC and MIDI
* It runs on the same computer to which the USB device is attached to and listens to OSC in port **47120**.
* Without any further configuration, it sends all data to port **47121** on the same host.
* The UI is just another client, using the OSC api.

## Data API

These messages are sent by the core to all registered clients (and also to port 47121 on localhost)

/raw `label` `value`

    label: a string representing the input
        D1-D10, A1-A4
    value: an integer value. 
        digital: 0-1
        analog: 0-analog resolution (default:1024)

/data/D `index` `value`

    index: the digital pin of the input
    value: 0-1, 0 being always normal state (OFF), 1 being pushed state

/data/A `index` `value`

    index (int)    : the analog pin of the input
    value (float32): normalized value between 0.0 - 1.0 

## Messages accepted by CORE

### GET protocol. 

All paths ending with /get use the **GET** protocol

The client can tell the server where to send to reply, for the cases where the client has no control of the OSC port from which it sends the message.

/path/get `replyID` `[arg1 arg2 ...]`

    replyID:
	 
            will send {/reply path ID value1 ...} to source_address
         a string: 
            of type addr/ID will send {/reply ID value1 ...} to addr
            where addr is "port" or "host:port"
            Default host is localhost.

    Example:

    A client sends a request with ID=34

    pedlbrd is in a machine with IP 192.168.0.2
    client is in a machine with IP 192.168.0.10

    client : 192.168.0.2:47120 /midiports/get "192.168.0.6:6789/34"
    pedlbrd: 192.168.0.6:6789  /reply "midiports" 34 "port1:port2"

    This is the same as:

    client: 192.168.0.2:47120 /midiports/get 34
    The reply will be the same

### Register a client

/registerdata `[host]` `[port]`

    if no args:
        register the address where the msg came
    if 1 arg:
        port: register localhost:port
    if 2 args:
        register as host:port
    All data messages will be sent to this addr.

/registerui `[host]` `[port]`

    Same as /registerdata
    Only UI related messages will be sent.

/registerall `[host]` `[port]`

    Shortcut for /registerui /registerdata

/signout `[host]` `[port]`

    Same as /registerui, removes observer 


### Configure the device

/digitalinvert   `label` `invertStatus`

    Invert the polarity of the given digital input  
    label: D1-D10, or a wildcard (D*)
    invertStatus: int (0/1)

/smoothing/get `replyID` `analogIndex`

    analogIndex (int) | the index of the analog input (>= 1)

    Returns the smoothing percent

/smoothing/set `analogIndex` `percent`

    Set the smoothing percent for the analog input

/midichannel/get `replyID` `[label]``

    Returns the midichannel of the corresponding pin.
    If label is not given, returns the midichannel
    only if all pins share the same channel.
    If the pins do not share the same channel, it returns -1

/midichannel/set `label` `channel`

    Each output can have a midichannel
    label  : D1, A3, etc. (string)
    channel: 0-15 (int)

/midicc/get `replyID` `label` 

    Returns CC for the given input

/midicc/set `label` `cc`

    Each output has a CC 
    Default: Dx --> CCx   (CC01, CC02, ...)
             Ax --> CC10x (CC101, CC102, ...)

/resetstate

    Reset state to its original state, does not change config

/resetconfig

    Reset config to default values

/calibrate

    Calibrate digital inputs

/openlog `debug:int`

    Open the normal (0) or the debug log (1)

/logfile/get

    Returns "info:debug", path_info, path_debug

/api/get `replyID` `show`

    show: if 1, it will be also output to stdout

    Returns /reply ID cmd1 cmd2 ... cmdn
        where each cmd is a string of the form
        path#types#docstr

/devinfo/get `replyID`

    This call generates multiple replies
    
    /devinfo tags value_1 value_2 ... value_n
        where:
            tags    : a string of the form key1:key2:...
            value_x : the value for each key
    
    /devinfo/analogpin tags value1 value2 ...
        tags: "label:resolution:smoothing:filtertype:denoise:autorange:minvalue:maxvalue"
        value1, value2, ... : the value of each key

        This is sent for each analog pin

/analogminval/set `index` `value`

    Set the minimum raw value for analog input. autorange will be disabled

/autorange/get `replyID` `analogIndex`

    Reply if autorange for this analog pin is set

    Autorange is a feature implemented at the firmware level,
    where the maximum and minimum values for a given input
    are polled to apply normalization

/autorange/set `analogIndex` `value`

    Set autorange for analog input
    
    value: 0-1

/ping `ID` `[optional return addr]`

    ID: integer
    will reply to /pingback ID

    example:    /ping 34 localhost:9000 --> sends </pingback 34> to localhost:9000
                /ping 34                --> sends </pingback 34> to the addr. where this msg was send from
                /ping 34 9000           --> sends </pingback 34> to localhost:9000

/report `[optional return addr]`

    Sends a number of /println messages to caller

/status/gets `replyID`

    Returns the status of the device

/digitalmapstr/get `replyID`

    Returns a string representing the calibration of the digital
    inputs, where a 0 represents normal calibration, and 1
    represents inverted calibration

    Example: 0010000001 ==> The 3rd and 10th inputs are inverted

/addrui/get `replyID`

    Returns all addresses receiving ui info as a space separated string
    "host1:port1 host2:port2 host3:port3 ... "

/addrdata/get `replyID`

    Same as /addrui

/analogresolution/get `replyID` `analogIndex`

    analogIndex´: 1-x
    
    Returns the analog resolution of the pin,
    which is the number of steps the input can have

    Default: 1024

    NB: the /raw message always indicates a value between 
    0 and analogresolution, the /data message is always
    normalized to 0-1

/filtertype/get `replyID` `analogInput`

    Replies with 0=LOWPASS 1=MEDIAN 2=BESSEL1 3=BESSEL32

    These are filters implemented at the firmware level to
    minimize jitter.

/filtertype/set `analogIndex` `value`

    value: 0=LOWPASS 1=MEDIAN 2=BESSEL1 3=BESSEL32

/quit

    Quit core (and gui)

/midithrough/set `wildcard_or_index`

    Send all midi generated through to the device(s) indicated

    wildcard_or_index: a glob wildcard, like "IAC*", or
                       a numeric index to indicate a specific
                       device. This index corresponds to the reply
                       generated by /midioutports/get

/midioutports/get `replyID`

    Get a list of possible ports to connect to

## UI API (CORE → UI)

These are the messages accepted by the UI process of the driver

/heartbeat
    
    The heartbeat of the core

/status s

    The connection status

/reply

    Each function call (/*/get) gets a reply at /reply.
    request: /*/get ID *args
    reply  : /reply ID *args

/quit
    
    gui should quit

/notify/calibrate
    
    core has been calibrated

/notify/reset
    
    core has been reset, gui gets a "bang"