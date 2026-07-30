/*
 * simple.c
 * A minimal C file with two small, well-written functions.
 * Used as a "clean" baseline for CUQA C parser tests.
 */

#include <stdio.h>
#include <string.h>

/* Compute the sum of two integers */
int add(int a, int b) {
    return a + b;
}

/* Print a greeting to stdout */
void greet(const char *name) {
    printf("Hello, %s!\n", name);
}

int main(void) {
    int result = add(3, 4);
    printf("3 + 4 = %d\n", result);
    greet("World");
    return 0;
}
