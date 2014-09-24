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

/* DEBUG Flags, comment out as appropriate. Don't use both */
//#define DEBUG
//#define STRESS_TEST

/* SETTINGS */
#define DEVICE_ID 5                 // The ID of this device
#define DEFAULT_DELAY 20  // Delay between each read, in ms (add also 1ms for each pin ~= +14ms)
#define HEARTBEAT   300   // Period of the Heartbeat, in ms (Default, can be changed via serial)
#define ANALOG_READ_DELAY 1  // delay between analog reads to stabilize impedence (see http://forums.adafruit.com/viewtopic.php?f=25&t=11597)
#define DIGITAL_READ_DELAY 0
#define DEFAULT_SMOOTHING_PERCENT 77
#define SEND_HEARTBEAT
//#define BLINK_LED_WHEN_DRIVER_NOT_PRESENT

/* PROTOCOL */
#define BAUDRATE 115200
//#define BAUDRATE 250000
#define CMD_DIGITAL  68      // D igital
#define CMD_ANALOG   65      // A nalog
#define CMD_HERTBEAT 72      // H eartbeat
#define CMD_REPLY    82	     // R eply
#define CMD_ERROR    69      // E rror
#define CMD_MESSAGE  77      // M essage
#define CMD_INFO     73      // I info
#define CMD_BUTTON   66      // B utton

/* INTERNAL */
#define MAX_ANALOG_PINS  4   // A4 and A5 are not used, left for future expansion using I2C        
#define MAX_DIGITAL_PINS 12  // D0, D1 and D13 are not used        
#define FIRST_DIGITAL_PIN 2  // The first two digital pins are used for Serial
#define COMMAND_MAXLENGTH 16 // Max length of a serial input command. Sender should wait between messages
#define BUTTONPIN 12
#define LEDPIN 13
#define ADC_MAXVALUE 1023      // the resolution of the ADC (Arduino Leonardo -> 10bit, Arduino Due -> 12bit)
#define ANALOG_RESOLUTION 1023  // should be equal or lower than ADC_MAXVALUE. This is to account for noise in the ADC
#define BLINK_PERIOD_MS    60 
#define BLINK_DURATION_MS 20
#define HEARTBEAT_MINPERIOD 200
#define HEARTBEAT_MAXPERIOD 800
#define ANALOG_ACTIVATE_THRESHOLD 200
#define MIN_MILLIS_SAME_DIRECTION 50		// these thresholds apply for changes <= 2
#define MIN_MILLIS_CHANGE_DIRECTION 4000
#define MIN_ANALOG_RESOLUTION 255
#define MAX_ANALOG_RESOLUTION 2047
#define MIN_DELAY 1
#define MAX_DELAY 100
#define ALLPINS 127
#define DENOISE_MIN_THRESHOLD 2

/* EEPROM */
#define EEPROM_EMPTY16 65535    // Uninitialized slot
#define EEPROM_EMPTY8  255
#define ADDR0 0               // Base EEPROM Address. 
#define ADDR_U2_HEARTBEAT 0      // EEPROM Address (2 bytes)
#define ADDR_U2_DELAY 2
#define ADDR_U1_BLINK_ENABLED 4

#define ADDR_U1_FILTERTYPE 30
#define ADDR_U1_SMOOTHING  40
#define ADDR_U1_RESOLUTIONBITS 50
#define ADDR_U1_DENOISE 60

/* FILTERS */
#define FILTER_UNSET -1
#define FILTER_LOWPASS 0
#define FILTER_MEDIAN  1 // Median
#define FILTER_BESSEL1 2 // Bessel order 1
#define FILTER_BESSEL2 3 // Bessel order 2
#define MAX_FILTERTYPES 3
#define DEFAULT_FILTERTYPE 1 // MEDIAN

/* ERROR CODES */
#define ERROR_COMMAND_BUF_OVERFLOW 1
#define ERROR_INDEX 2
#define ERROR_COMMAND_NUMBYTES 3
#define ERROR_VALUE 4

#ifndef DEBUG
	#define NORMAL
#endif

#define CHECK_CMD_LENGTH(n) if(command_length != n) { send_error(ERROR_COMMAND_NUMBYTES); break; }
#define sign(num) int((num>0)-(num<0))

const int enabled_pins_digital[] = {2, 3, 4, 5, 6, 7, 8, 9, 10, 11}; // pin 12 unused
const int enabled_pins_analog[]  = {0, 1, 2, 3};

int 
	analog_max[MAX_ANALOG_PINS],
	analog_min[MAX_ANALOG_PINS],
	analog_sentvalue[MAX_ANALOG_PINS],
	digital_state[MAX_DIGITAL_PINS],
	last_direction[MAX_ANALOG_PINS],
	analog_resolution_for_pin[MAX_ANALOG_PINS];

bool 
	apin_activated[MAX_ANALOG_PINS],
	apin_denoise[MAX_ANALOG_PINS],
	driver_present = false;

float analog_smoothing[MAX_ANALOG_PINS];
unsigned long last_millis[MAX_ANALOG_PINS];

unsigned
	measured_update_period = 20,
	heartbeat_period       = HEARTBEAT;

int command_length      = 0,
	blink_state         = 0,
	command_pointer     = 0,
	button_state        = 0,
	blink_enabled       = 1,
	delay_between_loops = 20;

const int
	num_digital_pins = sizeof(enabled_pins_digital)/sizeof(int),
	num_analog_pins  = sizeof(enabled_pins_analog) /sizeof(int);

unsigned long 
	last_heartbeat = 0,
	last_blink     = 0,
	last_incomming_heartbeat = 0,
	now;

char command[COMMAND_MAXLENGTH];

boolean 
	command_complete   = false,
	blink_led          = false,
	force_digital_read = false;

SignalFilter Filter[MAX_ANALOG_PINS];
int filtertypes[MAX_ANALOG_PINS];

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

byte eeprom_read_byte(unsigned addr, byte setdefault, byte minimum=0, byte maximum=254) {
	byte value = EEPROM.read(ADDR0+addr);
	if( value == EEPROM_EMPTY8 || value < minimum || value > maximum) {
		value = setdefault;
	}
	return value;
}

void set_heartbeat_period(unsigned value) {
	if(value != heartbeat_period && (value >= HEARTBEAT_MINPERIOD) && (value <= HEARTBEAT_MAXPERIOD)) {
		heartbeat_period = value;
		eeprom_write_uint(ADDR_U2_HEARTBEAT, value);
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

void send_error(int errorcode) {
	Serial.write(128 + CMD_ERROR);
	Serial.write(errorcode >> 7);
	Serial.write(errorcode & 0b1111111);
}

void set_smoothing_percent(int pin, int percent) {
	if( pin < 0 || pin >= MAX_ANALOG_PINS ) {
		send_error(ERROR_INDEX);
		return;
	}
	
	if( percent < 0 ) {
		percent = 0;
	} else if (percent >= 99) {
		percent = 99;
	}

	int current_percent = int(analog_smoothing[pin] * 100);
	if( percent != current_percent) {
		analog_smoothing[pin] = (float)(percent / 100.0);
		EEPROM.write(ADDR0 + ADDR_U1_SMOOTHING + pin, percent);	
	}
}

int read_smoothing_percent(int pin, int defaultvalue) {
	if( pin < 0 || pin >= MAX_ANALOG_PINS ) {
		send_error(ERROR_INDEX);
		return defaultvalue;
	}
	int percent = EEPROM.read(ADDR0 + ADDR_U1_SMOOTHING + pin);
	if( percent == EEPROM_EMPTY8 || percent < 0 || percent > 100 ) {
		percent = defaultvalue;
	}
	return percent;
}

int numbits(int num) {
	int bits = 0;
	while( num > 0 ) {
		bits++;
		num >>= 1;
	}
	return bits;
}

int read_analog_resolution_for_pin(int pin, int defaultvalue) {
	/*
	if( pin < 0 || pin >= MAX_ANALOG_PINS ) {
		send_error(ERROR_INDEX);
		return defaultvalue;
	}
	*/
	int defaultbits = numbits(defaultvalue);
	int bits = EEPROM.read(ADDR0+ADDR_U1_RESOLUTIONBITS+pin);
	if( bits == EEPROM_EMPTY8 || bits < 6 || bits > 12 ) {
		bits = defaultbits;
	}
	return (1 << bits) - 1;
}

int set_analog_resolution_for_pin(int pin, int resolution) {
	int bits = numbits(resolution);
	int quantized_resolution = (1 << bits) - 1;
	if( quantized_resolution != analog_resolution_for_pin[pin] && quantized_resolution >= 255 && quantized_resolution <= 2047 ) {
		analog_resolution_for_pin[pin] = quantized_resolution;
		EEPROM.write(ADDR0+ADDR_U1_RESOLUTIONBITS+pin, bits);
	}
}

void setup_filter(int pin, int preset) {
	char filtertype;
	int order;
	if( filtertypes[pin] == preset ) {
		return;
	}
	filtertypes[pin] = preset;
	if( preset == 0 ) {
		return;
	}
	switch( preset ) {
		case FILTER_MEDIAN:
			filtertype='m';
			order=2;
			break;	
		case FILTER_BESSEL1:
			filtertype='b';
			order=1;
			break;
		case FILTER_BESSEL2:
			filtertype='b';
			order=2;
			break;
	};
	Filter[pin].begin();
	Filter[pin].setFilter(filtertype);
	Filter[pin].setOrder(order);
}

void set_filtertype(int pin, int filtertype) {
	if( pin < 0 || pin >= MAX_ANALOG_PINS ) {
		send_error(ERROR_INDEX);
		return;
	}
	if( filtertype < 0 || filtertype > MAX_FILTERTYPES) {
		send_error(ERROR_VALUE);
		return;
	}
	if( filtertypes[pin] != filtertype ) {
		//setup_filter(pin, filtertype);
		EEPROM.write(ADDR0 + ADDR_U1_FILTERTYPE + pin, filtertype);
	}
}

int read_filtertype(int pin, int defaultvalue) {
	if( pin < 0 || pin >= MAX_ANALOG_PINS ) {
		return defaultvalue;
	}
	int filtertype = EEPROM.read(ADDR0 + ADDR_U1_FILTERTYPE + pin);
	if( filtertype == EEPROM_EMPTY8 || filtertype < 0 || filtertype > MAX_FILTERTYPES ) {
		filtertype = defaultvalue;
	}
	return filtertype;
}

void led_signal(int numblinks, int period_ms, int dur_ms) {
	// limit blink patterns to 10 seconds
	if( (period_ms < 0) || (period_ms * numblinks > 10000) ) {  
		send_error(ERROR_VALUE);
		return;
	}
	if( dur_ms >= period_ms ) {
		send_error(ERROR_VALUE);
		send_error('D');
		return;
	}
	for(int i=0; i<numblinks; i++) {
		digitalWrite(LEDPIN, LOW);
		delay(dur_ms);
		digitalWrite(LEDPIN, HIGH);
		delay(period_ms - dur_ms);
	}
}

////////////////////////////////////////////////////////////////////////
//   S E T U P
////////////////////////////////////////////////////////////////////////

void setup() {
	analogReference(EXTERNAL);
	
	Serial.begin(BAUDRATE); 
	while (! Serial); // Wait until Serial is ready - Leonardo

	#ifdef DEBUG
		Serial.println("Starting");
	#endif

	// heartbeat_period    = eeprom_read_uint(ADDR_U2_HEARTBEAT, HEARTBEAT, HEARTBEAT_MINPERIOD, HEARTBEAT_MAXPERIOD);
	heartbeat_period = HEARTBEAT;
	delay_between_loops = eeprom_read_uint(ADDR_U2_DELAY, DEFAULT_DELAY, MIN_DELAY, MAX_DELAY);
	blink_enabled       = eeprom_read_byte(ADDR_U1_BLINK_ENABLED, 1, 0, 1);
	
	pinMode(LEDPIN, OUTPUT);
	digitalWrite(LEDPIN, HIGH);
	blink_state = 1;

	pinMode(BUTTONPIN, INPUT_PULLUP);

	for( int i=0; i < MAX_ANALOG_PINS; i++) {
		analog_sentvalue[i] = 0;
		analog_smoothing[i] = read_smoothing_percent(i, DEFAULT_SMOOTHING_PERCENT) / 100.0;
		analog_min[i] = ADC_MAXVALUE;
		analog_max[i] = 0;
		analog_resolution_for_pin[i] = read_analog_resolution_for_pin(i, ANALOG_RESOLUTION);
		apin_activated[i] = false;
		apin_denoise[i] = eeprom_read_byte(ADDR_U1_DENOISE, 1, 0, 1);
		last_direction[i] = 0;
		last_millis[i]    = 0;
		setup_filter(i, read_filtertype(i, DEFAULT_FILTERTYPE));
	}

	for( int i=FIRST_DIGITAL_PIN; i < MAX_DIGITAL_PINS; i++) { 
		pinMode(i, INPUT);  
		digital_state[i] = 0;
	}

	// clear the serial input buffer
	for( int i=0; i < COMMAND_MAXLENGTH; i++ ) {
		command[i] = 0;
	}

	measured_update_period = 20; 
	now = millis();

	#ifdef DEBUG
		Serial.println("Finished setup");
	#endif
}

void reset() {
	int i;
	for( i=0; i < MAX_ANALOG_PINS; i++) {
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

/////////////////////////////////////////////////////////
//  MESSAGE PARSING
/////////////////////////////////////////////////////////

void act_on_command() {
	int value, i;
	byte pin, replyid;
	switch( command[0] ) {
		case 'H': // receive heartbeat from driver
			CHECK_CMD_LENGTH(1)
			last_incomming_heartbeat = now;
			break;
		case 'F': 
			CHECK_CMD_LENGTH(1)
			force_digital_read = true;
			break;
		case 'S': // SET
			switch( command[1] ) {
				case 'S': // SET SMOOTH
					CHECK_CMD_LENGTH(4)
					pin   = command[2];
					value = command[3];
					if( pin >= 0 && pin < MAX_ANALOG_PINS && value >= 0 && value <= 100 ) {
						set_smoothing_percent(pin, value);
					};
					break;
				case 'A': // SET ANALOG RESOLUTION FOR PIN
					CHECK_CMD_LENGTH(5)
					pin = command[2];
					value = (command[3] << 7) + command[4];
					if( value < MIN_ANALOG_RESOLUTION || value > MAX_ANALOG_RESOLUTION ) {
						send_error(ERROR_VALUE);
						break;
					}
					if( pin == ALLPINS ) {
						for(i=0; i < MAX_ANALOG_PINS; i++) {
							set_analog_resolution_for_pin(i, value);
						}
					} else {
						set_analog_resolution_for_pin(pin, value);
					}
					break;
				case 'F': // SET FILTER TYPE
					CHECK_CMD_LENGTH(4)
					pin   = command[2];
					value = command[3];
					if( value >= 0 && value <= MAX_FILTERTYPES && pin >= 0 && pin < MAX_ANALOG_PINS ) {
						set_filtertype(pin, value);
					} else {
						send_error(ERROR_VALUE);
					}
					break;
				case 'H': // SET HEARTBEAT
					CHECK_CMD_LENGTH(4)
					value = (command[2] << 7) + command[3];
					set_heartbeat_period(value);
					break;	
				case 'D': // SET DELAY
					CHECK_CMD_LENGTH(4)	
					value = (command[2] << 7) + command[3];
					if( delay_between_loops != value && value >= MIN_DELAY && value <= MAX_DELAY ) {
						delay_between_loops = value;
						eeprom_write_uint(ADDR_U2_DELAY, delay_between_loops);
					}
				case 'O': // DENOISE BY PREVENTING OSCILLATION
					CHECK_CMD_LENGTH(4)
					pin   = command[2];
					value = command[3];
					if( value < 0 && value > 1) {
						send_error(ERROR_VALUE);
					}
					if( value != apin_denoise[pin] ) {
						apin_denoise[pin] = value;
						EEPROM.write(ADDR0+ADDR_U1_DENOISE+pin, value);
					} 
					break;
				case 'B':  // BLINKING enable/disable
					CHECK_CMD_LENGTH(3);
					value = command[2];
					if( value < 0 && value > 1 ) {
						send_error(ERROR_VALUE);
					}
					if( value != blink_enabled ) {
						blink_enabled = value;
						EEPROM.write(ADDR0+ADDR_U1_BLINK_ENABLED, value);
					}
					break;
			}
			break;
		case 'G': // GET
			switch( command[1] ) {
				case 'A': // GET MAX ANALOG RESOLUTION FOR PIN
					CHECK_CMD_LENGTH(4)
					pin = command[2];
					replyid = command[3];
					send_reply(replyid, analog_resolution_for_pin[pin]);
					break;
				case 'H': // GET HEARTBEAT
					CHECK_CMD_LENGTH(3)
					replyid = command[2];
					send_reply(replyid, heartbeat_period);
					break;
				case 'I': // GET Device Info: ID, max_analog_pins, max_digital_pins, num_analog_pins, (pin+maxbits + smoothing + filterype + preventoscil) for each analog pin;
					CHECK_CMD_LENGTH(3)
					replyid = command[2];
					Serial.write(128 + CMD_INFO);
					Serial.write(replyid);
					Serial.write(DEVICE_ID);
					Serial.write(MAX_DIGITAL_PINS);
					Serial.write(MAX_ANALOG_PINS);
					Serial.write(num_digital_pins);
					Serial.write(num_analog_pins);
					for(int i=0; i<num_digital_pins; i++) {
						Serial.write(enabled_pins_digital[i]);
					}
					for(int i=0; i<num_analog_pins; i++) {
						Serial.write(enabled_pins_analog[i]);
					}
					for(i=0; i<num_analog_pins; i++) {
						pin = enabled_pins_analog[i];
						Serial.write(pin);
						Serial.write(numbits(analog_resolution_for_pin[pin]));
						Serial.write(int(analog_smoothing[pin] * 100));
						Serial.write(filtertypes[pin]);
						Serial.write(apin_denoise[pin]);
					}
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
						send_reply(replyid, filtertypes[pin]);
					} else {
						send_error(ERROR_INDEX);
					}
					break;
				case 'U': // UPDATE PERIOD
					CHECK_CMD_LENGTH(3)
					replyid = command[2];
					send_reply(replyid, measured_update_period);
					break;
				case 'D': // DELAY
					CHECK_CMD_LENGTH(3)
					replyid = command[2];
					send_reply(replyid, delay_between_loops);
					break;
				case 'O': // PREVENT OSCILLATION
					CHECK_CMD_LENGTH(4)
					pin = command[2];
					replyid = command[3];
					send_reply(replyid, apin_denoise[pin]);
					break;
				case 'B': // BLINK STATUS
					CHECK_CMD_LENGTH(3)
					replyid = command[2];
					send_reply(replyid, blink_enabled);
					break;

			}
			break;	
		case 'R': // Reset
			CHECK_CMD_LENGTH(1)
			reset();
			break;
		case 'L': // LED signal
			CHECK_CMD_LENGTH(7)
			// numblinks:uint, blinkperiod:uint, blinkdur:uint
			led_signal((command[1]<<7)+command[2], (command[3]<<7)+command[4], (command[5]<<7)+command[6]);
			break;
	}
}

/////////////////////////////////////////////////////////
//  L O O P
/////////////////////////////////////////////////////////

void loop() {
	int value, last_sentvalue, raw, lastraw, analog_resolution;
	float newvalue, smoothvalue, smoothing;
	int current_direction;
	int pin, i;
	unsigned long newnow;

	newnow = millis();
	measured_update_period = (measured_update_period >> 1) + ((newnow - now) >> 1);
	now = newnow;
	
	if( blink_led && (blink_state == 1) && (now - last_blink) > BLINK_PERIOD_MS) {
		last_blink = now;
		blink_state = 0;
		digitalWrite(LEDPIN, LOW);
		blink_led = false;
	} else if ( (blink_state == 0) && (now - last_blink) > BLINK_DURATION_MS ) {
		digitalWrite(LEDPIN, HIGH);
		blink_state = 1;
		last_blink = now;
	}

	// dispatch commands from serial. serial input is parsed in serialEvent
	if( command_complete ) {
		command_complete = false;
		act_on_command();
	}

	if( ((now - last_heartbeat) > heartbeat_period) || (last_heartbeat > now) ) {
		#ifdef NORMAL
			Serial.write(128 + CMD_HERTBEAT); 
			Serial.write(DEVICE_ID);
		#else
			Serial.println("HB");
		#endif
		last_heartbeat = now;
	}
	
	#ifdef BLINK_LED_WHEN_DRIVER_NOT_PRESENT
		driver_present = (now - last_incomming_heartbeat) < 1000;
		if( !driver_present ) {
			blink_led = true;
		}
	#endif
	
	////////////////////////
	// BUTTON

	// we use the pullup, so 0 is pressed. To stay coherent with the other digital inputs,
	// we invert the value, so 1 is pressed and 0 is normal
	value = 1 - digitalRead(BUTTONPIN);  
	if( value != button_state ) {
		button_state = value;
		#ifdef NORMAL
			Serial.write(128 + CMD_BUTTON);
			Serial.write(0);
			Serial.write(value);
		#else
			Serial.print("B1: ");
			Serial.println(value);
		#endif
	}

	///////////////////////
	// DIGITAL
	for(i=0; i < num_digital_pins; i++) {
		pin = enabled_pins_digital[i];
		value = digitalRead(pin);
		#if DIGITAL_READ_DELAY > 0
			delay(DIGITAL_READ_DELAY);
		#endif
		#ifdef STRESS_TEST
			value = random(2);
		#endif
		if( force_digital_read || (value != digital_state[pin]) ) {
			digital_state[pin] = value;
			if( blink_enabled ) {
				blink_led = true;	
			}
			#ifdef NORMAL
				Serial.write(128 + CMD_DIGITAL);
				//Serial.write(pin);
				Serial.write(i);
				Serial.write(value);
			#else
				Serial.print("D");
				Serial.print(pin);
				Serial.print(": ");
				Serial.println(value);			
			#endif
		};
	};
	force_digital_read = false;

	///////////////////
	// ANALOG 
	for(i=0; i < num_analog_pins; i++) {
		pin = enabled_pins_analog[i];
		last_sentvalue = analog_sentvalue[pin];
		smoothing = analog_smoothing[pin];
		#if ANALOG_READ_DELAY > 0
			delay(ANALOG_READ_DELAY);
		#endif

		raw = analogRead(pin);

		#ifdef STRESS_TEST
			raw = random(0, 1023);	
		#endif
		
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

		/*
		if( filtertypes[pin] > 0){
			raw = Filter[pin].run(raw);
		}
		*/
		
		smoothvalue = float(raw * (1.0f - smoothing) + analog_sentvalue[pin] * smoothing) / ADC_MAXVALUE;
		
		/*
		// smoothvalue will be normalized between 0-1
		if( filtertypes[pin] > 0 ) {
			smoothvalue = float(raw * (1.0f - smoothing) + (smoothing * Filter[pin].run(raw))) / ADC_MAXVALUE;
		} 
		else {
			smoothvalue = float(raw * (1.0f - smoothing) + analog_sentvalue[pin] * smoothing) / ADC_MAXVALUE;
		}
		*/

		analog_resolution = analog_resolution_for_pin[pin];
		if( smoothvalue < 0 ) {
			smoothvalue = 0;
		} else if (smoothvalue > 1) {
			smoothvalue = 1;
		}

		value = int(smoothvalue * analog_resolution);

		/*
		if( value > analog_resolution ) {
			value = analog_resolution;
		}
		*/
		
		int diff = abs(value - last_sentvalue);
		bool send_it = false;
		if(diff > 0 && apin_denoise[pin] && value > DENOISE_MIN_THRESHOLD) {
			switch( diff ) {
				case 0:
					break;
				case 1:
					current_direction = sign(value - last_sentvalue);
					if( last_direction[pin] == current_direction && abs(now - last_millis[pin]) > MIN_MILLIS_SAME_DIRECTION ) {
						last_millis[pin] = now;
						send_it = true;
					} 
					break;
				case 2:
					current_direction = sign(value - last_sentvalue);
					if( last_direction[pin] != current_direction && abs(now - last_millis[pin]) > MIN_MILLIS_CHANGE_DIRECTION ) {
						last_millis[pin] = now;
						send_it = true;
						last_direction[pin] = current_direction;
					} 
					break;
				default:
					send_it = true;
			}
		} else {
			if( diff > 0) {
				send_it = true;
			}
		}

		if( !send_it ) {
			continue;
		}

		analog_sentvalue[pin] = value;
		if( blink_enabled ) {
			blink_led = true;	
		}

		#ifdef NORMAL
			Serial.write(128 + CMD_ANALOG);
			//Serial.write(pin);
			Serial.write(i);
			Serial.write(value >> 7);
			Serial.write(value & 0b1111111);
		#else
			Serial.print("A");
			Serial.print(pin);
			Serial.print(": ");
			Serial.println(value);
		#endif
	};

	// if( blink_state == 0 ) {
	// 	digitalWrite(LEDPIN, HIGH);
	// 	blink_state = 1;
	// }

	delay(delay_between_loops);	
}

void serialEvent() {
	/* PROTOCOL: CMD PARAM VALUE1 VALUE2 ... SEP(0b10000000)
	   all values before SEP must be lower than 0b10000000 (0-127)
	   each command can interpret the values as it needs
	*/
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
				/* This should never happen, but we should never
				   fail here, so just roll-over and signal error */
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
