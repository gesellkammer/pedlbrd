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

/* SETTINGS */
#define ID 5                 // The ID of this pedal board
#define DELAY 20             // Delay between each read, in ms
#define HEARTBEAT   500      // Period of the Heartbeat, in ms (Default, can be changed via serial)
#define SMOOTH 50            // (0-100). 0=no smoothing

/* DEBUG Flags, comment out as appropriate */
//#define DEBUG


/* PROTOCOL */
#define BAUDRATE 57600
#define CMD_DIGITAL  68      // D
#define CMD_ANALOG   65      // A
#define CMD_HERTBEAT 72      // H 
#define CMD_REPLY    82	     // R
#define CMD_ERROR    69      // E

/* INTERNAL */
#define MAX_ANALOG_PINS  4   // A4 and A5 are not used, left for future expansion using I2C        
#define MAX_DIGITAL_PINS 12  // D0, D1 and D13 are not used        
#define FIRST_DIGITAL_PIN 2  // The first two digital pins are used for Serial
#define COMMAND_MAXLENGTH 32  // Max length of a serial input command
#define ADDR0 0               // Base EEPROM Address. TODO: implement way to rotate over time
#define ADDR_HEARTBEAT 0      // EEPROM Address (2 bytes)
#define EEPROM_EMPTY 65535    // Uninitialized slot
#define LEDPIN 13

/* ERROR CODES */
#define ERROR_COMMAND_BUF_OVERFLOW 7

int enabled_pins_digital[] = {2, 3, 4, 5, 6, 7, 8, 9, 10, 11}; // pin 12 unused
int enabled_pins_analog[]  = {0, 1, 2}; // TODO: connect also pin A3
int analog_state[MAX_ANALOG_PINS];
int digital_state[MAX_DIGITAL_PINS];
int num_dig = sizeof(enabled_pins_digital) / sizeof(int);
int num_an  = sizeof(enabled_pins_analog)  / sizeof(int);
float weight_new_value_f = floor((100-SMOOTH)/100.0 * 255.0) / 255.0;
unsigned heartbeat_period = HEARTBEAT;
unsigned long last_heartbeat = 0;
unsigned long now = 0;
boolean force_digital_read = false;
char command[COMMAND_MAXLENGTH];
boolean command_complete = false;
int command_length  = 0;
int command_pointer = 0;

// ============= 
//	  HELPERS
// ============= 

unsigned eeprom_write_uint(unsigned addr, unsigned value) {
	EEPROM.write(addr, value >> 8);
	EEPROM.write(addr+1, value & 0b11111111);
}

unsigned eeprom_read_uint(unsigned addr, unsigned setdefault=0) {
	// setdefault is only used if the address read has not
	// been initialized before
	int hi = EEPROM.read(addr);
	int lo = EEPROM.read(addr + 1);
	unsigned value = (hi << 8) + lo;
	if(value == EEPROM_EMPTY) {
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

void set_heartbeat(unsigned value) {
	if( value != heartbeat_period ) {
		heartbeat_period = value;
		eeprom_write_uint(ADDR0+ADDR_HEARTBEAT, value);
	}	
}

void send_reply(byte cmd, int value) {
	int hi, lo;
	hi = value >> 7;
	lo = value & 0b1111111;
	Serial.write(128 + CMD_REPLY);
	Serial.write(cmd);
	Serial.write(hi);
	Serial.write(lo);
}

void send_error(int errorcode) {
	Serial.write(128 + CMD_ERROR);
	Serial.write(errorcode);
}

void setup() {
	int i;
	Serial.begin(BAUDRATE);
	while (! Serial); // Wait until Serial is ready - Leonardo
	heartbeat_period = eeprom_read_uint(ADDR0+ADDR_HEARTBEAT, HEARTBEAT);

	for( i=0; i < MAX_ANALOG_PINS; i++) {
		analog_state[i] = 0;
	}

	for( i=FIRST_DIGITAL_PIN; i < MAX_DIGITAL_PINS; i++) { 
		pinMode(i, INPUT);  
		digital_state[i] = 0;
	}

	pinMode(LEDPIN, OUTPUT);

	// clear the serial input buffer
	for( i=0; i < COMMAND_MAXLENGTH; i++ ) {
		command[i] = 0;
	}

	digitalWrite(LEDPIN, HIGH);

	#ifdef DEBUG
		Serial.print("HEARTBEAT period: ");
		Serial.println(heartbeat_period);
		Serial.print  ("# number of enabled digital pins: ");
		Serial.println(num_dig);
		Serial.print  ("# number of enabled analog pins:  ");
		Serial.println(num_an);
	#endif
}

void loop() {
	int value, valuehigh, valuelow, lastvalue, rawvalue;
	int pin, i;
	boolean blink_led = false;
	// dispatch commands from serial. serial input is parsed in serialEvent
	if( command_complete ) {
		command_complete = false;
		switch( command[0] ) {
			case 'F': 
				force_digital_read = true;
				break;
			case 'S': // SET
				switch( command[1] ) {
					case 'H': // SET HEARTBEAT
						value = command[2] << 7 + command[3];
						set_heartbeat(value);
				}
			case 'G': // GET
				switch( command[1] ) {
					case 'H': // GET HEARTBEAT
						send_reply('H', heartbeat_period);
				}
		}
	}

	// Heartbeat
	now = millis();
	
	#ifndef DEBUG
		if( (now - last_heartbeat > heartbeat_period) || (last_heartbeat > now) ) {
			send_heartbeat();
			last_heartbeat = now;
		}
	#endif

	///////////////////////
	// DIGITAL
	for(i=0; i < num_dig; i++) {
		pin = enabled_pins_digital[i];
		value = digitalRead(pin);
		if( force_digital_read || (value != digital_state[pin]) ) {
			digital_state[pin] = value;
			blink_led = true;
			#ifndef DEBUG
				Serial.write(128 + CMD_DIGITAL);
				Serial.write(pin);
				Serial.write(0);
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
	for(i=0; i < num_an; i++) {
		pin = enabled_pins_analog[i];
		lastvalue = analog_state[pin];
		rawvalue = analogRead(pin);
		// smooth it
		value = int(rawvalue * weight_new_value_f + lastvalue * (1.0 - weight_new_value_f));
		if( value != lastvalue) {
			analog_state[pin] = value;
			if( !blink_led && abs(value - lastvalue) > 1) {
				blink_led = true;
			}

			#ifndef DEBUG
				valuehigh = value >> 7;
				valuelow  = value & 0b1111111;
				Serial.write(128 + CMD_ANALOG);
				Serial.write(pin);
				Serial.write(valuehigh);
				Serial.write(valuelow);
			#else
				Serial.print("A");
				Serial.print(pin);
				Serial.print(": ");
				Serial.println(value);
			#endif
		};
	};
	if( blink_led ) {
		digitalWrite(LEDPIN, LOW);
		delay(DELAY);
		digitalWrite(LEDPIN, HIGH);
	} else {
		delay(DELAY);		
	}
}

void serialEvent() {
	// PROTOCOL: CMD PARAM VAL_HI VAL_LO SEP(0b10000000)
	// all values before SEP must be lower than 0b10000000 (0-127)
	while( Serial.available() ) {
		byte ch = (byte)Serial.read();
		if( ch > 127 ) {  // SEP
			if( command_pointer > 0 ) {
				command_complete = true;
				command_length = command_pointer;
				command_pointer = 0;
				#ifdef DEBUG
					Serial.print("-- sending. command length: ");
					Serial.println(command_length);
				#endif
			}
		} 
		else { 
			command[command_pointer] = ch;
			command_pointer++;
			if(command_pointer > COMMAND_MAXLENGTH) {
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
