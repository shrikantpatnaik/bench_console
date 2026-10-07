Hantek 6022BL calibration extension (BCAL protocol version 1)
==========================================================

Based on sigrok-firmware-fx2lafw 0.1.7, with changes only to the 6022BL
target. The stock BL target's calibration command is a no-op. This version
adds selectable nominal square-wave output frequencies on the CAL OUT pin:
Off, 50, 100, 200, 500 Hz; 1, 2, 5, 10, 20 kHz. Default: 1 kHz.
Off holds the calibration pin low. Amplitude is fixed by the hardware.

The image is loaded by libsigrok into volatile FX2 RAM. No EEPROM writes
or permanent firmware updates are performed. The scope-mode helper sets
SIGROK_FIRMWARE_DIR to this package's sigrok-firmware directory. Logic mode
continues using the distribution's firmware. Close other scope programs,
unplug USB power once after upgrading, reconnect in scope mode and scan.
An already running stock RAM firmware is not automatically overwritten.

USB device-recipient vendor requests, wValue/wIndex = 0:
  IN  0xE7, length 6: ASCII BCAL, version byte 1, frequency index.
  OUT 0xE8, length 1: frequency index 0..9 in the order listed above.
The helper verifies the signature before SET and reads the index back after
SET. An unchanged setting is read without restarting the output timer.
Upstream acquisition requests 0xE0..0xE6 are unchanged. The output setting
is a USB acknowledgement, not a physical measurement of the signal.

Timer 2 runs at the 12 MHz CPU clock / 12 = 1 MHz. The half-period counts
are 10000, 5000, 2500, 1000, 500, 250, 100, 50, 25 ticks. Actual accuracy
and interrupt jitter depend on the device and USB activity. Connect CAL OUT
to a scope channel and use the live frequency measurement to check it.
The extension has been compiled and its host protocol tested with USB stubs;
physical 6022BL timing and USB behavior have not been bench tested here.

Complete corresponding source, copyright notices and upstream licenses:
  sigrok-firmware-fx2lafw-0.1.7-bench-source.tar.gz
  COPYING (GPL v2), COPYING.LESSER (LGPL v2.1)
The firmware source and descriptors retain their original license notices.
The modified archive includes BENCH-CALIBRATION.txt describing the changes.

Rebuild (only needed to modify firmware; not needed on the Raspberry Pi):
  sudo apt install sdcc make binutils
  tar -xzf sigrok-firmware-fx2lafw-0.1.7-bench-source.tar.gz
  cd sigrok-firmware-fx2lafw-0.1.7
  ./configure
  make hw/hantek-6022bl/fx2lafw-hantek-6022bl.fw
  # Replace the packaged .fw image with the resulting file.
Build tested with SDCC 4.2.0. The 8051 binary is independent of host CPU.

Upstream sources:
https://github.com/sigrokproject/sigrok-firmware-fx2lafw
https://deb.debian.org/debian/pool/main/s/sigrok-firmware-fx2lafw/sigrok-firmware-fx2lafw_0.1.7.orig.tar.gz
https://github.com/sigrokproject/libsigrok/blob/libsigrok-0.5.2/src/resource.c
