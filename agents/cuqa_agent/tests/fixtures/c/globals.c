/* C fixture: globals.c — GlobalVariable smell */
#include <stdio.h>

int g_counter = 0;
static int g_buffer_size = 1024;
extern int g_external_flag;

void inc(void) {
    g_counter++;
}
