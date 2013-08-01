PEDLBRD
=======

PEDLBRD is a device to connect Pedals to a computer and convert 
its output to both MIDI and OSC. It supports 10 Digital pedals 
(switch pedals like the sustain pedals used in keyboards or 
program-change pedals ofter used for guitars) and 4 Analog 
pedals (any expression or volume pedal).

Installation
------------

* Drop the "Pedlbrd" app onto your Applications folder
* OPTIONAL: To be able to monitor the MIDI output, drop the 
  "MIDI Monitor" app into your Applications folder. 


Usage
-----

* Open the "Pedlbrd" app.
* Connect the Device to your USB port
* Connect pedals to the device

MIDI Output
-----------

When the application is open, a MIDI device named "PEDLBRD" 
will be created. In any MIDI capable program you can read 
events from this device. By default, all pedal actions are 
passed as Control Change events on channel 1.
                
Digital Pedals: Channel 1, CC 1-10
Analog Pedals:  Channel 1, CC 101-104

OSC Output
----------

All actions are also output as OSC to the port 47121. 
The Pedlbrd device accepts OSC commands at port 47120.
To have a look at all the possible OSC commands, open the
Pedlbrd app and click on the CONSOLE button to list all 
input and output commands.

OSC commands:

/data pedal value
    pedal (str)   : D1-D10 or A1-A4
    value (float) : 0/1 for Digital, 0-1 for Analog
    Digital pedals are autocalibrated to output 0 when not pressed,
    1 when pressed.
    Analog pedals are autocalibrated to output in the range 0-1,
    independent of their configuration

/raw pedal value
    pin (str)   : The internal pin which fired the event
    value (int) : The value as read by the device (before any calibration)



