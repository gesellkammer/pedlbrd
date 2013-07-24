/*
	PEDLBRD

	Protocol:

	- the protocol follows very nearly the MIDI protocol of 1+7 bits
	- each byte uses the 7 least significant bits as data information
	- the most significant bit (0b10000000) is used as message flag, to
		know signal when a message begins.
	- At the moment all messages are assumed to be 4 bytes, but this 
		could be changed to allow longer or shorted messages
	- Suported commands are:
		Heartbit -> a message sent periodically to signal life at the arduino side
								it is also used by the autodetection method on the client side
								to determine presence and detect the correct serial port
		Digital  -> 0b1000000 + 68 (D)
								Pin number (0-127)
								0
								Value (0-1)
		Analog   -> 0b1000000 + 65 (A)
								Pin number (0-127)
								ValueHigh
								ValueLow

								Value = (ValueHigh * 128 + ValueLow)  -> range 0-16383 
								(14 bits ADC)
 */

// #define DEBUG

#define BAUDRATE 57600
#define ID 5                  // The ID of this pedal board

#define MAX_ANALOG_PORTS  4   // A4 and A5 are not used, left for future expansion using I2C        
#define MAX_DIGITAL_PORTS 12  // D0, D1 and D13 are not used        
#define DELAY 20             // Delay between each read, in ms
#define HEARTBEAT 1000        // Period of the Heartbeat, in ms
#define CMD_DIGITAL 68        // D
#define CMD_ANALOG  65        // A
#define CMD_HERTBEAT 72       // H 
#define COMMAND_MAXLENGTH 32

int enabled_pins_digital[] = {2, 3, 4, 5, 6, 7, 8, 9, 10, 11};
int enabled_pins_analog[] = {0, 1, 2};
int analog_state[MAX_ANALOG_PORTS];
int digital_state[MAX_DIGITAL_PORTS];
int num_dig = sizeof(enabled_pins_digital) / sizeof(int);
int num_an  = sizeof(enabled_pins_analog)  / sizeof(int);
int time_between_reads = DELAY;
float weight_new_value_f = floor(0.6 * 255.0) / 255.0;
unsigned long last_heartbeat = 0;
unsigned long now = 0;
int force_digital_read;
char command[COMMAND_MAXLENGTH];
boolean command_complete = false;
int command_length = 0;
int command_pointer = 0;

void setup() {
	Serial.begin(BAUDRATE);
	while (! Serial); // Wait until Serial is ready - Leonardo

	int i;
	for( i=0; i < MAX_ANALOG_PORTS; i++) {
		analog_state[i] = 0;
	}
	for( i=2; i < MAX_DIGITAL_PORTS; i++) {  // we dont use the first 2 dig. ports
		pinMode(i, INPUT);  
		digital_state[i] = 0;
	}

	for( i=0; i < COMMAND_MAXLENGTH; i++ ) {
		command[i] = 0;
	}

	#ifdef DEBUG
		Serial.println("\n\n# ------------------------------------");
		Serial.print  ("# number of enabled digital ports: ");
		Serial.println(num_dig);
		Serial.print  ("# number of enabled analog ports:  ");
		Serial.println(num_an);
		Serial.println("# ------------------------------------\n\n\n");
	#endif

}

char itohex(int i) {
	return "0123456789ABCDEF"[i];
}

void send_heartbeat() {
	#ifndef DEBUG
		Serial.write(128 + CMD_HERTBEAT); 
		Serial.write(ID);
	#else
		Serial.println("HRT");
	#endif
}

void loop() {
	int value, valuehigh, valuelow;
	int pin;
	if( command_complete ) {
		command_complete = false;
		if(command[0] == 'F') {
			force_digital_read = true;
		}
	}

	now = millis();
	if( (now - last_heartbeat > HEARTBEAT) || (last_heartbeat > now) ) {
		send_heartbeat();
		last_heartbeat = now;
	}

	for(int i=0; i < num_dig; i++) {
		pin = enabled_pins_digital[i];
		value = digitalRead(pin);
		if((value != digital_state[pin]) || force_digital_read ) {
			digital_state[pin] = value;
			#ifdef DEBUG
				Serial.print("D");
				Serial.print(pin);
				Serial.println(value);
			#else
				Serial.write(128 + CMD_DIGITAL);
				Serial.write(pin);
				Serial.write(0);
				Serial.write(value);
			#endif
		};
	};
	force_digital_read = 0;

	for(int i=0; i < num_an; i++) {
		pin = enabled_pins_analog[i];
		int lastvalue = analog_state[pin];
		int raw_value = analogRead(pin);
		
		value = int(raw_value * weight_new_value_f + lastvalue * (1.0 - weight_new_value_f));
		valuehigh = value >> 7;
		valuelow  = value & 0b1111111;
		
		if( value != lastvalue) {
			analog_state[pin] = value;
			#ifdef DEBUG
				Serial.print("A");
				Serial.print(pin);
				Serial.println(value);
			#else
				Serial.write(128 + CMD_ANALOG);
				Serial.write(pin);
				Serial.write(valuehigh);
				Serial.write(valuelow);
			#endif
		};
	};
	delay(time_between_reads);
}

void serialEvent() {
	/* PROTOCOL
	CMD PARAM VAL1 VAL2 SEP

	where SEP is 0b10000000

	all other values must be lower than 0b10000000
	*/
	while( Serial.available() ) {
		byte ch = (byte)Serial.read();
		if( ch > 127 ) {
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
				command_pointer = 0;
			}
			#ifdef DEBUG
				Serial.print("command pointer: ");
				Serial.println(command_pointer);
			#endif
		}
	}
}
