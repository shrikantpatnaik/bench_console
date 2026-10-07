/* Binary capture bridge for libsigrok 0.5.2+. No vendor DLL or desktop needed.
 * Build: python -m bench_console.hantek.build
 * Each process owns one bounded capture; the Python worker serializes access.
 */
#include <libsigrok/libsigrok.h>
#include <glib.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include "calibration.h"

struct capture {
    struct sr_session *session;
    FILE *analog[2], *logic;
    uint64_t counts[2], logic_count, limit;
    int error;
};

static void quote(const char *s) {
    putchar('"');
    if (s) for (const unsigned char *p = (const unsigned char *)s; *p; p++) {
        if (*p == '"' || *p == '\\') { putchar('\\'); putchar(*p); }
        else if (*p < 32) printf("\\u%04x", *p);
        else putchar(*p);
    }
    putchar('"');
}

static void datafeed(const struct sr_dev_inst *sdi,
                     const struct sr_datafeed_packet *packet, void *arg) {
    (void)sdi;
    struct capture *cap = arg;
    if (packet->type == SR_DF_ANALOG) {
        const struct sr_datafeed_analog *a = packet->payload;
        size_t channels = g_slist_length(a->meaning->channels);
        if (!channels || channels > 64 || a->num_samples > 16777216) {
            cap->error = 1; sr_session_stop(cap->session); return;
        }
        float *values = g_try_malloc_n(a->num_samples * channels, sizeof(float));
        if (!values || sr_analog_to_float(a, values) != SR_OK) {
            g_free(values); cap->error = 1; sr_session_stop(cap->session); return;
        }
        size_t column = 0;
        for (GSList *l = a->meaning->channels; l; l = l->next, column++) {
            struct sr_channel *ch = l->data;
            int index = -1;
            if (!strcmp(ch->name, "CH1") || !strcmp(ch->name, "A0")) index = 0;
            if (!strcmp(ch->name, "CH2") || !strcmp(ch->name, "A1")) index = 1;
            if (index < 0) continue;
            uint64_t n = MIN((uint64_t)a->num_samples, cap->limit - cap->counts[index]);
            if (channels == 1) {
                if (fwrite(values, sizeof(float), n, cap->analog[index]) != n) cap->error = 1;
            } else {
                float *one = g_try_malloc_n(n, sizeof(float));
                if (!one) cap->error = 1;
                else {
                    for (uint64_t i = 0; i < n; i++) one[i] = values[i * channels + column];
                    if (fwrite(one, sizeof(float), n, cap->analog[index]) != n) cap->error = 1;
                    g_free(one);
                }
            }
            cap->counts[index] += n;
        }
        g_free(values);
        if (cap->error || (cap->counts[0] >= cap->limit && cap->counts[1] >= cap->limit))
            sr_session_stop(cap->session);
    } else if (packet->type == SR_DF_LOGIC) {
        const struct sr_datafeed_logic *l = packet->payload;
        if (l->unitsize < 1 || l->unitsize > 64) {
            cap->error = 1; sr_session_stop(cap->session); return;
        }
        uint64_t n = MIN(l->length / l->unitsize, cap->limit - cap->logic_count);
        const uint8_t *p = l->data;
        /* Expose the first eight digital input bits only. */
        if (l->unitsize == 1) {
            if (fwrite(p, 1, n, cap->logic) != n) cap->error = 1;
        } else {
            for (uint64_t i = 0; i < n; i++)
                if (fwrite(&p[i * l->unitsize], 1, 1, cap->logic) != 1) cap->error = 1;
        }
        cap->logic_count += n;
        if (cap->error || cap->logic_count >= cap->limit) sr_session_stop(cap->session);
    }
}

static int set(struct sr_dev_inst *sdi, struct sr_channel_group *cg,
               uint32_t key, GVariant *value) {
    g_variant_ref_sink(value);
    int result = sr_config_set(sdi, cg, key, value);
    g_variant_unref(value);
    return result;
}

int main(int argc, char **argv) {
    gchar *driver_name = NULL, *conn = NULL, *mode = NULL, *folder = NULL;
    gchar *rate_text = NULL, *samples_text = NULL;
    gdouble vdiv1 = .5, vdiv2 = .5;
    gboolean scan = FALSE, calibration_only = FALSE;
    gint calibration_frequency = -1;
    GOptionEntry options[] = {
        {"driver", 0, 0, G_OPTION_ARG_STRING, &driver_name, "Driver", NULL},
        {"conn", 0, 0, G_OPTION_ARG_STRING, &conn, "USB connection", NULL},
        {"mode", 0, 0, G_OPTION_ARG_STRING, &mode, "scope or logic", NULL},
        {"scan", 0, 0, G_OPTION_ARG_NONE, &scan, "Discover", NULL},
        {"calibration-only", 0, 0, G_OPTION_ARG_NONE, &calibration_only, "Set output without acquiring", NULL},
        {"calibration-frequency", 0, 0, G_OPTION_ARG_INT, &calibration_frequency, "Calibration output Hz; 0 is off", NULL},
        {"output", 0, 0, G_OPTION_ARG_STRING, &folder, "Output directory", NULL},
        {"rate", 0, 0, G_OPTION_ARG_STRING, &rate_text, "Sample rate", NULL},
        {"samples", 0, 0, G_OPTION_ARG_STRING, &samples_text, "Record length", NULL},
        {"vdiv1", 0, 0, G_OPTION_ARG_DOUBLE, &vdiv1, "CH1 front-end V/div", NULL},
        {"vdiv2", 0, 0, G_OPTION_ARG_DOUBLE, &vdiv2, "CH2 front-end V/div", NULL},
        {NULL, 0, 0, 0, NULL, NULL, NULL}
    };
    GOptionContext *opt = g_option_context_new(NULL);
    g_option_context_add_main_entries(opt, options, NULL);
    GError *error = NULL;
    if (!g_option_context_parse(opt, &argc, &argv, &error)) {
        fprintf(stderr, "%s\n", error->message); return 2;
    }
    if (!driver_name || !mode || (strcmp(mode, "scope") && strcmp(mode, "logic")) ||
        (strcmp(driver_name, "hantek-6xxx") && strcmp(driver_name, "fx2lafw") && strcmp(driver_name, "demo"))) {
        fprintf(stderr, "Select a supported driver and mode\n"); return 2;
    }
    int is_scope = !strcmp(mode, "scope");
    uint64_t rate = rate_text ? g_ascii_strtoull(rate_text, NULL, 10) : 0;
    uint64_t limit = samples_text ? g_ascii_strtoull(samples_text, NULL, 10) : 0;
    if (calibration_frequency != -1 && (!is_scope || calibration_code(calibration_frequency) < 0)) {
        fprintf(stderr, "Calibration requires scope mode and a listed frequency\n"); return 2;
    }
    if (calibration_only && (scan || calibration_frequency == -1 || strcmp(driver_name, "hantek-6xxx"))) {
        fprintf(stderr, "Calibration-only requires a 6022BL scope and frequency\n"); return 2;
    }
    if (!scan && !calibration_only && (!folder || !rate || !limit || limit > 2097152)) {
        fprintf(stderr, "Invalid bounded capture parameters\n"); return 2;
    }
    sr_log_loglevel_set(SR_LOG_WARN);
    struct sr_context *ctx = NULL;
    if (sr_init(&ctx) != SR_OK) { fprintf(stderr, "Cannot initialize libsigrok\n"); return 3; }
    struct sr_dev_driver *driver = NULL;
    for (struct sr_dev_driver **d = sr_driver_list(ctx); *d; d++)
        if (!strcmp((*d)->name, driver_name)) { driver = *d; break; }
    if (!driver || sr_driver_init(ctx, driver) != SR_OK) {
        fprintf(stderr, "Requested sigrok driver is not available\n"); sr_exit(ctx); return 3;
    }
    struct sr_config connection = {SR_CONF_CONN, NULL};
    GSList *scan_options = NULL;
    if (conn && *conn) {
        connection.data = g_variant_ref_sink(g_variant_new_string(conn));
        scan_options = g_slist_append(NULL, &connection);
    }
    GSList *devices = sr_driver_scan(driver, scan_options);
    g_slist_free(scan_options);
    if (connection.data) g_variant_unref(connection.data);
    struct sr_dev_inst *selected = NULL;
    if (scan) putchar('[');
    int emitted = 0;
    for (GSList *l = devices; l; l = l->next) {
        struct sr_dev_inst *sdi = l->data;
        const char *model = sr_dev_inst_model_get(sdi);
        if (is_scope && strcmp(driver_name, "demo") && (!model || strcmp(model, "6022BL"))) continue;
        if (!selected) selected = sdi;
        if (scan) {
            /* Opening completes FX2 firmware re-enumeration. CONN is the USB
             * bus.address accepted by the scan option, while connid is a
             * physical port path and must not be passed as that option. */
            GVariant *bus_address = NULL;
            if (sr_dev_open(sdi) != SR_OK ||
                sr_config_get(driver, sdi, NULL, SR_CONF_CONN, &bus_address) != SR_OK) {
                fprintf(stderr, "Cannot open detected device; check USB access and firmware\n");
                g_slist_free(devices); sr_exit(ctx); return 4;
            }
            sr_dev_close(sdi);
            if (emitted++) putchar(',');
            printf("{\"driver\":"); quote(driver_name);
            printf(",\"conn\":"); quote(g_variant_get_string(bus_address, NULL));
            g_variant_unref(bus_address);
            printf(",\"port\":"); quote(sr_dev_inst_connid_get(sdi));
            printf(",\"vendor\":"); quote(sr_dev_inst_vendor_get(sdi));
            printf(",\"model\":"); quote(model);
            printf(",\"mode\":"); quote(mode);
            printf(",\"channels\":%d}", is_scope ? 2 : 8);
        }
    }
    if (scan) {
        printf("]\n"); g_slist_free(devices); sr_exit(ctx); return 0;
    }
    if (!selected || g_slist_length(devices) != 1) {
        fprintf(stderr, "No unique matching device; rescan after selecting the physical H/P mode\n");
        g_slist_free(devices); sr_exit(ctx); return 4;
    }
    if (sr_dev_open(selected) != SR_OK) {
        fprintf(stderr, "Cannot open USB device; check permissions, firmware and other applications\n");
        g_slist_free(devices); sr_exit(ctx); return 4;
    }
    int ret = 0;
    struct capture cap = {0};
    cap.limit = limit;
    if (calibration_frequency != -1 && strcmp(driver_name, "demo")) {
        GVariant *bus_address = NULL;
        if (sr_config_get(driver, selected, NULL, SR_CONF_CONN, &bus_address) != SR_OK) {
            fprintf(stderr, "Cannot resolve calibration USB device\n"); ret = 5; goto cleanup;
        }
        ret = calibration_apply(g_variant_get_string(bus_address, NULL), calibration_frequency);
        g_variant_unref(bus_address);
        if (ret) { ret = 5; goto cleanup; }
    }
    if (calibration_only) {
        printf("{\"calibration_frequency\":%d}\n", calibration_frequency);
        goto cleanup;
    }
    if (set(selected, NULL, SR_CONF_SAMPLERATE, g_variant_new_uint64(rate)) != SR_OK ||
        set(selected, NULL, SR_CONF_LIMIT_SAMPLES, g_variant_new_uint64(limit)) != SR_OK) {
        fprintf(stderr, "Unsupported sample rate or record length\n"); ret = 5; goto cleanup;
    }
    GVariant *actual_rate = NULL;
    if (sr_config_get(driver, selected, NULL, SR_CONF_SAMPLERATE, &actual_rate) != SR_OK) {
        fprintf(stderr, "Cannot verify sample rate\n"); ret = 5; goto cleanup;
    }
    rate = g_variant_get_uint64(actual_rate);
    g_variant_unref(actual_rate);
    if (is_scope && strcmp(driver_name, "demo")) {
        for (GSList *l = sr_dev_inst_channel_groups_get(selected); l; l = l->next) {
            struct sr_channel_group *cg = l->data;
            double gain = !strcmp(cg->name, "CH1") ? vdiv1 : vdiv2;
            if (set(selected, cg, SR_CONF_VDIV,
                    g_variant_new("(tt)", gain >= 1 ? (guint64)1 : (guint64)(gain * 1000 + .5), gain >= 1 ? (guint64)1 : (guint64)1000)) != SR_OK) {
                fprintf(stderr, "Cannot configure independent front-end gain\n"); ret = 5; goto cleanup;
            }
            GVariant *confirmed = NULL;
            guint64 numerator, denominator;
            if (sr_config_get(driver, selected, cg, SR_CONF_VDIV, &confirmed) != SR_OK) {
                fprintf(stderr, "Cannot verify front-end gain\n"); ret = 5; goto cleanup;
            }
            g_variant_get(confirmed, "(tt)", &numerator, &denominator);
            g_variant_unref(confirmed);
            if (!denominator || (double)numerator / denominator != gain) {
                fprintf(stderr, "Front-end gain readback mismatch\n"); ret = 5; goto cleanup;
            }
        }
    }
    int digital = 0;
    for (GSList *l = sr_dev_inst_channels_get(selected); l; l = l->next) {
        struct sr_channel *ch = l->data;
        gboolean enabled = is_scope ? (ch->type == SR_CHANNEL_ANALOG &&
                (!strcmp(ch->name, "CH1") || !strcmp(ch->name, "CH2") ||
                 !strcmp(ch->name, "A0") || !strcmp(ch->name, "A1")))
            : (ch->type == SR_CHANNEL_LOGIC && digital++ < 8);
        if (sr_dev_channel_enable(ch, enabled) != SR_OK) {
            fprintf(stderr, "Cannot enable channels\n"); ret = 5; goto cleanup;
        }
    }
    for (int i = 0; i < (is_scope ? 2 : 1); i++) {
        gchar *path = g_strdup_printf("%s/%s", folder,
                     is_scope ? (i == 0 ? "CH1.f32" : "CH2.f32") : "logic.bin");
        FILE *f = fopen(path, "wb");
        g_free(path);
        if (!f) { fprintf(stderr, "Cannot write capture file\n"); ret = 6; goto cleanup; }
        if (is_scope) cap.analog[i] = f; else cap.logic = f;
    }
    if (sr_session_new(ctx, &cap.session) != SR_OK ||
        sr_session_dev_add(cap.session, selected) != SR_OK ||
        sr_session_datafeed_callback_add(cap.session, datafeed, &cap) != SR_OK ||
        sr_session_start(cap.session) != SR_OK || sr_session_run(cap.session) != SR_OK || cap.error) {
        fprintf(stderr, "USB capture failed or ended unexpectedly\n"); ret = 7; goto cleanup;
    }
    printf("{\"rate\":%" G_GUINT64_FORMAT ",\"counts\":[%" G_GUINT64_FORMAT ",%" G_GUINT64_FORMAT
           "],\"logic_count\":%" G_GUINT64_FORMAT "}\n", rate, cap.counts[0], cap.counts[1], cap.logic_count);
cleanup:
    if (cap.session) sr_session_destroy(cap.session);
    for (int i = 0; i < 2; i++) if (cap.analog[i] && fclose(cap.analog[i])) ret = 6;
    if (cap.logic && fclose(cap.logic)) ret = 6;
    sr_dev_close(selected);
    g_slist_free(devices);
    sr_exit(ctx);
    g_option_context_free(opt);
    return ret;
}
