/* C fixture: magic_number.c — MagicNumber smell */
#include <stdio.h>

int compute(int val) {
    int x = val * 42;  // 42 is magic number
    double pi = 3.14;   // 3.14 is magic number
    return x;
}

int safe(int val) {
    int zero = 0;   // safe
    int one = 1;    // safe
    int neg = -1;   // safe
    int two = 2;    // safe
    return zero + one + neg + two;
}
