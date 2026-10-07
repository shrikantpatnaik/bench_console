/* Use device-recipient EP0 requests; never claim or detach the scope interface.
 * This protocol is private to the bundled firmware. Check its signature before
 * sending a SET request, so stock firmware cannot silently accept a no-op.
 */
#include "calibration.h"
#include <stdio.h>
#include <string.h>

int calibration_code(int frequency) {
    static const int frequencies[] = {0, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000};
    for (unsigned i = 0; i < sizeof(frequencies) / sizeof(frequencies[0]); i++)
        if (frequencies[i] == frequency) return (int)i;
    return -1;
}

static int read_code(libusb_device_handle *handle) {
    unsigned char reply[6] = {0};
    int n = libusb_control_transfer(handle, 0xc0, 0xe7, 0, 0, reply, sizeof(reply), 1000);
    if (n != 6 || memcmp(reply, "BCAL", 4) || reply[4] != 1 || reply[5] > 9) {
        fprintf(stderr, "Calibration firmware unavailable: unplug the 6022BL USB cable, "
                "reconnect in scope mode and scan again to load the bundled RAM firmware.\n");
        return -1;
    }
    return reply[5];
}

int calibration_transfer(libusb_device_handle *handle, int code) {
    if (code < 0 || code > 9) return -1;
    int current = read_code(handle);
    if (current < 0) return -1;
    if (current == code) return 0;
    unsigned char value = (unsigned char)code;
    if (libusb_control_transfer(handle, 0x40, 0xe8, 0, 0, &value, 1, 1000) != 1) {
        fprintf(stderr, "Calibration frequency command failed; output state is unverified\n");
        return -1;
    }
    if (read_code(handle) != code) {
        fprintf(stderr, "Calibration frequency readback mismatch; output state is unverified\n");
        return -1;
    }
    return 0;
}

int calibration_apply(const char *connection, int frequency) {
    unsigned bus, address;
    char trailing;
    int code = calibration_code(frequency);
    if (!connection || sscanf(connection, "%u.%u%c", &bus, &address, &trailing) != 2 ||
        bus > 255 || address > 127 || code < 0) {
        fprintf(stderr, "Invalid calibration device or frequency\n"); return -1;
    }
    libusb_context *ctx = NULL;
    libusb_device **devices = NULL;
    libusb_device_handle *handle = NULL;
    int result = -1;
    if (libusb_init(&ctx) < 0) {
        fprintf(stderr, "Cannot initialize calibration USB control\n"); return -1;
    }
    ssize_t count = libusb_get_device_list(ctx, &devices);
    for (ssize_t i = 0; i < count; i++) {
        struct libusb_device_descriptor descriptor;
        if (libusb_get_bus_number(devices[i]) != bus ||
            libusb_get_device_address(devices[i]) != address) continue;
        if (libusb_get_device_descriptor(devices[i], &descriptor) < 0 ||
            descriptor.idVendor != 0x1d50 || descriptor.idProduct != 0x608e ||
            descriptor.bcdDevice != 0x0003) break;
        if (libusb_open(devices[i], &handle) == 0)
            result = calibration_transfer(handle, code);
        break;
    }
    if (!handle) fprintf(stderr, "Cannot open the connected 6022BL for calibration USB control\n");
    if (handle) libusb_close(handle);
    if (devices) libusb_free_device_list(devices, 1);
    libusb_exit(ctx);
    return result;
}
