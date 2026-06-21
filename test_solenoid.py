#!/usr/bin/env python3
# test_solenoid.py
# Simple script to test the MOSFET and 12V solenoid valve wiring.

import time

try:
    import RPi.GPIO as GPIO
except ImportError:
    print("Error: RPi.GPIO library not found. Please run this script on the Raspberry Pi.")
    exit(1)

# --- Configuration ---
# GPIO pin (BCM numbering) connected to the Control/Gate input of the MOSFET
SOLENOID_PIN = 17  
# How long to keep the solenoid open during the test (in seconds)
DURATION_SECONDS = 3.0

def main():
    # Use Broadcom SOC channel numbers (BCM pin numbering)
    GPIO.setmode(GPIO.BCM)
    
    # Configure the pin as an output
    GPIO.setup(SOLENOID_PIN, GPIO.OUT)
    
    # Ensure it starts OFF (LOW)
    GPIO.output(SOLENOID_PIN, GPIO.LOW)
    
    print("Testing Solenoid on GPIO {0}...".format(SOLENOID_PIN))
    print("Starting in 2 seconds (stand back!)...")
    time.sleep(2.0)
    
    try:
        print("Solenoid: OPEN (HIGH)")
        GPIO.output(SOLENOID_PIN, GPIO.HIGH)
        time.sleep(DURATION_SECONDS)
        
        print("Solenoid: CLOSED (LOW)")
        GPIO.output(SOLENOID_PIN, GPIO.LOW)
        
    except KeyboardInterrupt:
        print("\nTest interrupted. Ensuring solenoid is closed...")
        GPIO.output(SOLENOID_PIN, GPIO.LOW)
        
    finally:
        # Reset GPIO settings to safe state
        GPIO.cleanup()
        print("GPIO cleaned up. Test complete.")

if __name__ == '__main__':
    main()
