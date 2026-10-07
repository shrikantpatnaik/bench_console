/* BCAL v1 control for the bundled, RAM-loaded 6022BL firmware. */
#ifndef BENCH_CALIBRATION_H
#define BENCH_CALIBRATION_H
#include <libusb.h>
int calibration_code(int frequency);
int calibration_transfer(libusb_device_handle *handle, int code);
int calibration_apply(const char *connection, int frequency);
#endif
