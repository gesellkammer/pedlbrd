/*
  PEDLBRD
 */

// #define DEBUG

#define ID 5       // The ID of this pedal board

#define MAX_ANALOG_PORTS  4   // A4 and A5 are not used, left for future expansion using I2C        
#define MAX_DIGITAL_PORTS 10  // D0, D1 and D13 are not used
#define BAUDRATE 57600
#define DELAY 20
#define HEARTBEAT 1000
#define CMD_DIGITAL 68 // D
#define CMD_ANALOG  65 // A
#define CMD_HERTBEAT 72 // H 

// int enabled_ports_digital[] = {2, 3, 4, 5};
int enabled_ports_digital[] = {2, 3, 4, 5, 6, 7, 8, 9, 10, 11};
int enabled_ports_analog[] = {0, 1, 2};
int analog_max[MAX_ANALOG_PORTS];
int analog_state[MAX_ANALOG_PORTS];
int digital_state[MAX_DIGITAL_PORTS];
int num_dig = sizeof(enabled_ports_digital) / sizeof(int);
int num_an  = sizeof(enabled_ports_analog)  / sizeof(int);
int time_between_reads = DELAY;
int weight_new_value = int(0.6 * 255);
float weight_new_value_f = weight_new_value / 255.0;
unsigned long last_heartbeat = 0;
unsigned long now = 0;

void setup() {
  Serial.begin(BAUDRATE);
  
  int i;
  for( i=0; i < MAX_ANALOG_PORTS; i++) {
    analog_state[i] = 0;
    analog_max[i] = 0;
  }
  for( i=2; i < MAX_DIGITAL_PORTS; i++) {  // we dont use the first 2 dig. ports
    pinMode(i, INPUT);  
    digital_state[i] = 0;
   
  }

  while (! Serial); // Wait until Serial is ready - Leonardo
  
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
    Serial.write(0);
    Serial.write(0);
  #else
    Serial.println("HRT");
  #endif
}

void loop() {
  now = millis();
  if( (now - last_heartbeat > HEARTBEAT) || (last_heartbeat > now) ) {
    // TODO: send heartbeat
    send_heartbeat();
    last_heartbeat = now;
  }

  // message format (binary): pin number, hibyte, lobyte
  // for digital pins, hibyte is always 0, lobyte is 0 or 1
  // pin number: 0-199 -> digital, 200- analog
  for(int i=0; i < num_dig; i++) {
    int port = enabled_ports_digital[i];
    int value = digitalRead(port);
    if( value != digital_state[port]) {
      digital_state[port] = value;
      #ifdef DEBUG
        Serial.print("D");
        Serial.print(port);
        Serial.println(value);
      #else
        Serial.write(128 + CMD_DIGITAL);
        Serial.write(port);
        Serial.write(0);
        Serial.write(value);
      #endif
    };
  };
  for(int i=0; i < num_an; i++) {
    int port = enabled_ports_analog[i];
    int lastvalue = analog_state[port];
    int raw_value = analogRead(port);
    int max_value = analog_max[port];
    if( raw_value > max_value ) {
      max_value = raw_value;
      analog_max[port] = max_value;
    }
    double value = raw_value * weight_new_value_f + lastvalue * (1.0 - weight_new_value_f);
    double norm_value = round((float(value) / float(max_value)) * 1023.0);

    // int value = v;
    if( value != lastvalue) {
      analog_state[port] = value;
      #ifdef DEBUG
        Serial.print("A");
        Serial.print(port);
        Serial.println(
      #else
        Serial.write(128 + CMD_ANALOG);
        Serial.write(port);
        Serial.write(0);
        Serial.write(value);
      #endif
    };
  };
  delay(time_between_reads);
}
