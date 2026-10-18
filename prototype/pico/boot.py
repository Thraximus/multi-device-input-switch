# Runs once at power-up. Changes here need an unplug/replug to take effect.
import usb_cdc
import usb_hid
import usb_midi

# One serial port for incoming mouse packets (no REPL console, so only one COM port shows up on Windows).
usb_cdc.enable(console=False, data=True)
# Present only a mouse.
usb_hid.enable((usb_hid.Device.MOUSE,))
# Drop the default MIDI device.
usb_midi.disable()
