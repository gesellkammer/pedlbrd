/*
PEDLBRD

Protocol
========

OUTPUT

- the protocol follows very nearly the MIDI protocol of 1+7 bits
- each byte uses the 7 least significant bits as data information
- the most significant bit (0b10000000) is used as message flag, to
	know signal when a message begins.
- Supported commands are:
	Heartbit --> 2 bytes | 0b10000000 + H, ID
	             a message sent periodically to signal life at the arduino side
				 It is also used by the autodetection method on the client side
				 to determine presence and detect the correct serial port
	Digital  --> 4 bytes | 0b10000000 + D (68), Pin (0-127), 0, Value (0-1)
	Analog   --> 4 bytes | 0b10000000 + A (65), Pin (0-127), ValueHI, ValueLO
							
						   Value = (ValueHI << 7) + ValueLO
						   Range: 0-16383 (14 bits)
	Reply    --> 4 bytes | 0b10000000 + R (82), Param (0-127), ValueHI, ValueLO
	             Used to respond to GET messages got through Serial.
	Error    --> 2 bytes | 0b10000000 + E (69), errorcode (0-127)

INPUT
 
	CMD PARAM VALUE SEP
	1   1     2     1    bytes

	CMD:   0-127 
	PARA:  0-127
	VALUE: 0-16383
	SEP:   0b10000000

	Commands defined

	F 0 0   --> force digital read
	S H ms  --> Set Heartbeat to value (in ms)
*/

#include <EEPROM.h>
#include <SignalFilter.h>

/* DEBUG Flags, comment out as appropriate */
//#define DEBUG
//#define STRESS_TEST

/* SETTINGS */
#define ID 5                 // The ID of this pedal board
#define MINDELAY 30          // Delay between each read, in ms (add also 1ms for each pin ~= +14ms)
#define HEARTBEAT   300      // Period of the Heartbeat, in ms (Default, can be changed via serial)
#define SMOOTH 50            // (0-100). 0=no smoothing
#define ANALOG_READ_DELAY 1  // delay between analog reads to stabilize impedence (see http://forums.adafruit.com/viewtopic.php?f=25&t=11597)
#define DIGITAL_READ_DELAY 1
#define HEARTBEAT_SEND_ID
#define DEFAULT_SMOOTHING_PERCENT 77

/* PROTOCOL */
//#define BAUDRATE 115200
#define BAUDRATE 250000
#define CMD_DIGITAL  68      // D
#define CMD_ANALOG   65      // A
#define CMD_HERTBEAT 72      // H 
#define CMD_REPLY    82	     // R
#define CMD_ERROR    69      // E
#define CMD_MESSAGE  77      // M
#define CMD_INFO     73      // I

/* INTERNAL */
#define MAX_ANALOG_PINS  4   // A4 and A5 are not used, left for future expansion using I2C        
#define MAX_DIGITAL_PINS 12  // D0, D1 and D13 are not used        
#define FIRST_DIGITAL_PIN 2  // The first two digital pins are used for Serial
#define COMMAND_MAXLENGTH 32  // Max length of a serial input command
#define LEDPIN 13
#define ADC_MAXVALUE 1023      // the resolution of the ADC (Arduino Leonardo -> 10bit, Arduino Due -> 12bit)
#define ANALOG_RESOLUTION 1023  // should be equal or lower than ADC_MAXVALUE. This is to account for noise in the ADC
#define MIN_BLINK 50 // ms
#define HEARTBEAT_MINPERIOD 200
#define HEARTBEAT_MAXPERIOD 800
#define ANALOG_ACTIVATE_THRESHOLD 200

/* EEPROM */
#define EEPROM_EMPTY16 65535    // Uninitialized slot
#define EEPROM_EMPTY8  255
#define ADDR0 0               // Base EEPROM Address. 
#define ADDR_HEARTBEAT 0      // EEPROM Address (2 bytes)
#define ADDR_SMOOTH    2
#define ADDR_ANALOGRESOLUTION 4
#define ADDR_SMOOTHING 100

/* ERROR CODES */
#define ERROR_COMMAND_BUF_OVERFLOW 1
#define ERROR_INDEX 2
#define ERROR_COMMAND_NUMBYTES 3

#define CHECK_CMD_LENGTH(n) if(command_length != n) { send_error(ERROR_COMMAND_NUMBYTES); break; }

int enabled_pins_digital[] = {2, 3, 4, 5, 6, 7, 8, 9, 10, 11}; // pin 12 unused
int enabled_pins_analog[]  = {0, 1, 2}; // TODO: connect also pin A3
int analog_max[MAX_ANALOG_PINS];
int analog_min[MAX_ANALOG_PINS];
float analog_state[MAX_ANALOG_PINS];
int analog_sentvalue[MAX_ANALOG_PINS];
int digital_state[MAX_DIGITAL_PINS];
float analog_smoothing[MAX_ANALOG_PINS];
bool apin_activated[MAX_ANALOG_PINS];
int analog_resolution;
const int num_dig = sizeof(enabled_pins_digital) / sizeof(int);
const int num_an  = sizeof(enabled_pins_analog)  / sizeof(int);

unsigned heartbeat_period = HEARTBEAT;
float weight_newvalue;
int smoothing_percent;
boolean force_digital_read = false;

unsigned long last_heartbeat = 0;
unsigned long now = 0;
unsigned long last_blink = 0;

char command[COMMAND_MAXLENGTH];
boolean command_complete = false;
int command_length  = 0;
int command_pointer = 0;

boolean blink_led = false;
int blink_state = 0;

#define FILTER_MEDIAN  1 // Median
#define FILTER_BESSEL1 2 // Bessel order 1
#define FILTER_BESSEL2 3 // Bessel order 2
#define MAX_FILTER 3
SignalFilter Filter[MAX_ANALOG_PINS];
int filter_status[MAX_ANALOG_PINS];

const int delay_between_loops = max(MINDELAY, num_an*ANALOG_READ_DELAY + num_dig*DIGITAL_READ_DELAY);

// ============= 
//	  HELPERS
// =============

unsigned eeprom_write_uint(unsigned addr, unsigned value) {
	EEPROM.write(ADDR0 + addr, value >> 8);
	EEPROM.write(ADDR0 + addr+1, value & 0b11111111);	
}

unsigned eeprom_read_uint(unsigned addr, unsigned setdefault, unsigned minimum=0, unsigned maximum=65534) {
	int hi = EEPROM.read(ADDR0 + addr);
	int lo = EEPROM.read(ADDR0 + addr + 1);
	unsigned value = (hi << 8) + lo;
	if(value == EEPROM_EMPTY16 || (value < minimum) || (value > maximum)) {
		value = setdefault;
		eeprom_write_uint(addr, value);
	}
	return value;
}

void send_heartbeat() {
	#ifndef DEBUG
		Serial.write(128 + CMD_HERTBEAT); 
		Serial.write(ID);
	#else
		Serial.println("HRT");
	#endif
}

void set_heartbeat_period(unsigned value) {
	if(value != heartbeat_period && (value >= HEARTBEAT_MINPERIOD) && (value <= HEARTBEAT_MAXPERIOD)) {
		heartbeat_period = value;
		eeprom_write_uint(ADDR_HEARTBEAT, value);
	}
}

void send_reply(byte reply_id, int value) {
	int hi, lo;
	hi = value >> 7;
	lo = value & 0b1111111;
	Serial.write(128 + CMD_REPLY);
	Serial.write(reply_id);
	Serial.write(hi);
	Serial.write(lo);
}

// void send_msg(byte id, char *buf) {
// 	// format: msg flag, id, numchars, chars
// 	int numchars = 0;
// 	char ch;
// 	if( id > 127 ) {
// 		return;
// 	}
// 	for(int i=0; i < 127; i++) {
// 		if(buf[i] == 0) {
// 			break;
// 		}
// 		numchars += 1;
// 	}
// 	if( numchars > 127 ) {
// 		return;
// 	}
// 	Serial.write(128 + CMD_MESSAGE);
// 	Serial.write(id);
// 	Serial.write(numchars);
// 	for(int i=0; i < numchars; i++) {
// 		ch = buf[i];
// 		if( ch > 0 and ch <= 127 ) {
// 			Serial.write(ch);
// 		}
// 	}
// }

void send_error(int errorcode) {
	Serial.write(128 + CMD_ERROR);
	Serial.write(errorcode);
}

void set_smoothing_percent(int pin, int percent) {
	int current_percent = int(analog_smoothing[pin] * 100);
	if( percent != current_percent) {
		analog_smoothing[pin] = (float)(percent / 100.0);
		EEPROM.write(ADDR0 + ADDR_SMOOTHING + pin, percent);	
	}
}

int read_smoothing_percent(int pin, int defaultpercent) {
	int percent = EEPROM.read(ADDR0 + ADDR_SMOOTHING + pin);
	if( percent == EEPROM_EMPTY8 || percent < 0 || percent > 100 ) {
		percent = defaultpercent;
	}
	return percent;
}

void set_analog_resolution(int resolution) {
	if ( (resolution != analog_resolution) && (resolution >= 255) && (resolution <= 1023) ) {
		analog_resolution = resolution;
		eeprom_write_uint(ADDR_ANALOGRESOLUTION, resolution);
	}
}

////////////////////////////////////////////////////////////////////////
//   S E T U P
////////////////////////////////////////////////////////////////////////

void setup() {
	int i;
	Serial.begin(BAUDRATE);
	while (! Serial); // Wait until Serial is ready - Leonardo

	heartbeat_period  = eeprom_read_uint(ADDR_HEARTBEAT, HEARTBEAT, HEARTBEAT_MINPERIOD, HEARTBEAT_MAXPERIOD);
	analog_resolution = eeprom_read_uint(ADDR_ANALOGRESOLUTION, ANALOG_RESOLUTION, 255, 1023);
	
	pinMode(LEDPIN, OUTPUT);
	digitalWrite(LEDPIN, HIGH);
	blink_state = 1;

	for( i=0; i < MAX_ANALOG_PINS; i++) {
		analog_state[i] = 0.0;
		analog_sentvalue[i] = 0;
		analog_smoothing[i] = read_smoothing_percent(i, DEFAULT_SMOOTHING_PERCENT) / 100.0;
		setup_filter(i, FILTER_BESSEL1);
		analog_min[i] = ADC_MAXVALUE;
		analog_max[i] = 0;
		apin_activated[i] = false;
	}

	for( i=FIRST_DIGITAL_PIN; i < MAX_DIGITAL_PINS; i++) { 
		pinMode(i, INPUT);  
		digital_state[i] = 0;
	}

	// clear the serial input buffer
	for( i=0; i < COMMAND_MAXLENGTH; i++ ) {
		command[i] = 0;
	}

	#ifdef DEBUG
		Serial.print("HEARTBEAT period: ");
		Serial.println(heartbeat_period);
		Serial.print  ("# number of enabled digital pins: ");
		Serial.println(num_dig);
		Serial.print  ("# number of enabled analog pins:  ");
		Serial.println(num_an);
	#endif
}

void reset() {
	int i;
	for( i=0; i < MAX_ANALOG_PINS; i++) {
		analog_state[i] = 0.0;
		analog_sentvalue[i] = 0;
		analog_min[i] = ADC_MAXVALUE;
		analog_max[i] = 0;
		apin_activated[i] = false;
	}

	// clear the serial input buffer
	for( i=0; i < COMMAND_MAXLENGTH; i++ ) {
		command[i] = 0;
	}
}

void setup_filter(int pin, int preset) {
	char filtertype;
	int order;
	filter_status[pin] = preset;
	if( preset > 0 ) {
		switch( preset ) {
			case FILTER_BESSEL1:
				filtertype='b';
				order=1;
				break;
			case FILTER_BESSEL2:
				filtertype='b';
				order=2;
				break;
			case FILTER_MEDIAN:
				filtertype='m';
				order=2;
				break;	
		};
		Filter[pin].begin();
		Filter[pin].setFilter(filtertype);
		Filter[pin].setOrder(order);
	}
}

/////////////////////////////////////////////////////////
//  MESSAGE PARSING
/////////////////////////////////////////////////////////

void act_on_command() {
	int value;
	byte pin;
	byte replyid;
	switch( command[0] ) {
		case 'F': 
			force_digital_read = true;
			break;
		case 'S': // SET
			switch( command[1] ) {
				case 'H': // SET HEARTBEAT
					CHECK_CMD_LENGTH(4)
					value = (command[2] << 7) + command[3];
					set_heartbeat_period(value);
					break;
				case 'S': // SET SMOOTH
					CHECK_CMD_LENGTH(5)
					pin   = command[2];
					value = command[3];
					if( pin >= 0 && pin < MAX_ANALOG_PINS && value >= 0 && value <= 100 ) {
						set_smoothing_percent(pin, value);
					};
					break;
				case 'A': // SET ANALOG RESOLUTION
					CHECK_CMD_LENGTH(4)
					value = (command[2] << 7) + command[3];
					if( (value >= 255) && (value <= 1023) ) {
						set_analog_resolution(value);
					};
					break;
				case 'F': // SET FILTER TYPE (0=naive low-pass)
					CHECK_CMD_LENGTH(4)

					pin   = command[2];
					value = command[3];
					if( value >= 0 && value <= MAX_FILTER && pin >= 0 && pin < MAX_ANALOG_PINS ) {
						filter_status[pin] = value;
						if( value > 0 ) 
							setup_filter(pin, value);
					}
					break;
			}
			break;
		case 'G': // GET
			switch( command[1] ) {
				case 'A': // GET MAX ANALOG VALUE
					CHECK_CMD_LENGTH(3)
					replyid = command[2];
					send_reply(replyid, analog_resolution);
					break;
				case 'H': // GET HEARTBEAT
					CHECK_CMD_LENGTH(3)
					replyid = command[2];
					send_reply(replyid, heartbeat_period);
					break;
				case 'I': // GET Device Info: ID, max_analog_pins, max_digital_pins
					CHECK_CMD_LENGTH(2)
					Serial.write(128 + CMD_INFO);
					Serial.write(3);  // three items
					Serial.write(1);  // each item is 7 bits
					Serial.write(ID);
					Serial.write(MAX_DIGITAL_PINS);
					Serial.write(MAX_ANALOG_PINS);
					break;
				case 'S': // GET SMOOTHING (percent)
					CHECK_CMD_LENGTH(4)
					pin = command[2];
					replyid = command[3];
					if( pin >= 0 && pin < MAX_ANALOG_PINS ) {
						send_reply(replyid, int(analog_smoothing[pin] * 100));	
					}
					break;
				case 'F': // GET PREFILTER STATUS
					CHECK_CMD_LENGTH(4)
					pin = command[2];
					replyid = command[3];
					if( pin >= 0 && pin < MAX_ANALOG_PINS ) {
						send_reply(replyid, filter_status[pin]);
					} else {
						send_error(ERROR_INDEX);
					}
					break;
			}
			break;	
		case 'R': // Reset
			CHECK_CMD_LENGTH(1)
			reset();
			break;
	}
}

/////////////////////////////////////////////////////////
//  L O O P
/////////////////////////////////////////////////////////

void loop() {
	int value, last_sentvalue, raw;
	float lastvalue, newvalue, smoothvalue, smoothing;
	int pin, i;
	
	if( blink_led && (blink_state = 1) && ((now - last_blink) > MIN_BLINK) ) {
		digitalWrite(LEDPIN, LOW);
		blink_state = 0;
		blink_led = false;	
		last_blink = now;
	};

	// dispatch commands from serial. serial input is parsed in serialEvent
	if( command_complete ) {
		command_complete = false;
		act_on_command();
	}

	now = millis();
	#ifndef DEBUG
		if( ((now - last_heartbeat) > heartbeat_period) || (last_heartbeat > now) ) {
			Serial.write(128 + CMD_HERTBEAT); 
			#ifdef HEARTBEAT_SEND_ID
				Serial.write(ID);
			#endif
			last_heartbeat = now;
		}
	#endif

	///////////////////////
	// DIGITAL
	for(i=0; i < num_dig; i++) {
		pin = enabled_pins_digital[i];
		value = digitalRead(pin);
		delay(DIGITAL_READ_DELAY);
		if( force_digital_read || (value != digital_state[pin]) ) {
			digital_state[pin] = value;
			blink_led = true;
			#ifndef DEBUG
				Serial.write(128 + CMD_DIGITAL);
				Serial.write(pin);
				Serial.write(value);
			#else
				Serial.print("D");
				Serial.print(pin);
				Serial.print(": ");
				Serial.println(value);			
			#endif
		};
		#ifdef STRESS_TEST
			Serial.write(128 + CMD_DIGITAL);
			Serial.write(pin);
			Serial.write(random(2));
		#endif
	};
	force_digital_read = false;

	///////////////////
	// ANALOG 
	for(i=0; i < num_an; i++) {
		pin = enabled_pins_analog[i];
		lastvalue = analog_state[pin];
		#ifdef ANALOG_READ_DELAY
			delay(ANALOG_READ_DELAY);
		#endif
		raw = analogRead(pin);
		if( !apin_activated[pin] ) {
			if( raw > analog_max[pin] ) {
				analog_max[pin] = raw;
			} 
			else if( raw < analog_min[pin] ) {
				analog_min[pin] = raw;
			}
			if( (analog_max[pin] - analog_min[pin]) >= ANALOG_ACTIVATE_THRESHOLD ) {
				apin_activated[pin] = true;
			}
			continue;
		}

		last_sentvalue = analog_sentvalue[pin];
		smoothing = analog_smoothing[pin];

		if( filter_status[pin] > 0 ) {
			newvalue = float(raw);
			newvalue = float(newvalue * (1 - smoothing) + (smoothing * Filter[pin].run(raw)));
			smoothvalue = newvalue / ADC_MAXVALUE;
		} else {
			newvalue = float(raw) / ADC_MAXVALUE;
			smoothvalue = newvalue * (1 - smoothing) + lastvalue * (1 - smoothing);
		}
		
		value = int(smoothvalue * analog_resolution + 0.5);
		value = constrain(value, 0, analog_resolution);

		if( value != last_sentvalue ) {
			analog_sentvalue[pin] = value;
			analog_state[pin] = smoothvalue;
			blink_led = true;

			#ifndef DEBUG
				Serial.write(128 + CMD_ANALOG);
				Serial.write(pin);
				Serial.write(value >> 7);
				Serial.write(value & 0b1111111);
			#else
				Serial.print("A");
				Serial.print(pin);
				Serial.print(": ");
				Serial.println(value);
			#endif
		};

		#ifdef STRESS_TEST
			Serial.write(128 + CMD_ANALOG);
			Serial.write(pin);
			Serial.write(random(0, 10));
			Serial.write(random(0, 127));
		#endif

	};
	if( blink_state == 0 ) {
		digitalWrite(LEDPIN, HIGH);
		blink_state = 1;
	}

	delay(delay_between_loops);		
}

void serialEvent() {
	// PROTOCOL: CMD PARAM VALUE1 VALUE2 ... SEP(0b10000000)
	// all values before SEP must be lower than 0b10000000 (0-127)
	// each command can interpret the values as it needs
	while( Serial.available() ) {
		byte ch = (byte)Serial.read();
		if( ch > 127 ) {  // SEP
			if( command_pointer > 0 ) {
				command_complete = true;
				command_length   = command_pointer;
				command_pointer  = 0;
				#ifdef DEBUG
					Serial.print("-- sending. command length: ");
					Serial.println(command_length);
				#endif
			}
		} 
		else { 
			command[command_pointer] = ch;
			command_pointer++;
			if(command_pointer >= COMMAND_MAXLENGTH) {
				// This should never happen, but we should never
				// fail here, so just roll-over and signal error
				command_pointer = 0;
				send_error(ERROR_COMMAND_BUF_OVERFLOW);
			}
			#ifdef DEBUG
				Serial.print("command pointer: ");
				Serial.println(command_pointer);
			#endif
		}
	}
}
