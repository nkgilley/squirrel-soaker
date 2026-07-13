# Wiring and Hardware

## Pi 5 camera and button

Use a Raspberry Pi Camera Module 3 on the CSI connector with the cable fully
inserted and the contacts facing the connector contacts. The manual button is
read as a dry contact using the GPIO configuration in `pi/capture.py`; confirm
the configured pull-up/pull-down matches whether the switch is normally open.

## Solenoid

Do not power a solenoid from a GPIO pin. Use a correctly rated relay module or
logic-level MOSFET driver, a separate supply, and flyback protection for a DC
coil. Tie grounds together only where the driver design requires it. Confirm
the valve's voltage and current rating before connecting power.

## IR camera plug

The optional TP-Link/Kasa plug supplies power to the NoIR camera or its
dedicated lighting hardware. Enter its LAN IP in Settings and enable control
only after testing the camera independently. The server turns it on for the
configured night period and off for the day period. It does not replace the
solenoid safety controls.

## Bring-up checklist

1. Test camera capture with the solenoid disconnected.
2. Test the button and relay with a meter or indicator load.
3. Use the Diagnostics page to capture a test image and video.
4. Set a short spray duration and test manually while observing the valve.
5. Enable automation only after the emergency-disable path is confirmed.
