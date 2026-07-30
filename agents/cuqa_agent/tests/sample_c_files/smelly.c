/*
 * smelly.c
 * Deliberately bad C code to trigger all CUQA C smell rules:
 *   - LongFunction        (> 40 lines)
 *   - TooManyParameters   (> 5 params)
 *   - DeepNesting         (depth > 4)
 *   - MagicNumber         (numeric literals not in {0, 1, -1, 2})
 *   - UnsafeFunctionUsage (strcpy, gets)
 *   - GlobalVariable      (file-scope declarations)
 */

#include <stdio.h>
#include <string.h>

/* GlobalVariable smell */
int g_counter = 0;
char g_buffer[256];

/*
 * TooManyParameters: 7 parameters
 * Also contains MagicNumber, UnsafeFunctionUsage, DeepNesting
 */
void process_data(int a, int b, int c, int d, int e, int f, int g) {
    char local_buf[128];

    /* UnsafeFunctionUsage: strcpy */
    strcpy(local_buf, "hello");

    /* MagicNumber: 42, 100, 999 */
    int threshold = 42;
    int max_value = 100;
    int error_code = 999;

    /* DeepNesting: depth 5+ */
    if (a > 0) {
        if (b > 0) {
            if (c > 0) {
                if (d > 0) {
                    if (e > 0) {
                        printf("Deep nesting reached\n");
                        g_counter += threshold;
                    }
                }
            }
        }
    }

    for (int i = 0; i < max_value; i++) {
        if (i % 7 == 0) {           /* MagicNumber: 7 */
            g_counter++;
        }
    }

    /* More body to push over 40 lines */
    int x = a + b;
    int y = c + d;
    int z = e + f + g;
    printf("x=%d y=%d z=%d code=%d\n", x, y, z, error_code);
    printf("counter=%d\n", g_counter);
}

/*
 * LongFunction: this function body exceeds 40 lines
 * Also uses gets() — UnsafeFunctionUsage
 */
void read_user_input(void) {
    char buf[64];

    /* UnsafeFunctionUsage: gets */
    gets(buf);

    printf("You entered: %s\n", buf);
    printf("Line 1\n");
    printf("Line 2\n");
    printf("Line 3\n");
    printf("Line 4\n");
    printf("Line 5\n");
    printf("Line 6\n");
    printf("Line 7\n");
    printf("Line 8\n");
    printf("Line 9\n");
    printf("Line 10\n");
    printf("Line 11\n");
    printf("Line 12\n");
    printf("Line 13\n");
    printf("Line 14\n");
    printf("Line 15\n");
    printf("Line 16\n");
    printf("Line 17\n");
    printf("Line 18\n");
    printf("Line 19\n");
    printf("Line 20\n");
    printf("Line 21\n");
    printf("Line 22\n");
    printf("Line 23\n");
    printf("Line 24\n");
    printf("Line 25\n");
    printf("Line 26\n");
    printf("Line 27\n");
    printf("Line 28\n");
    printf("Line 29\n");
    printf("Line 30\n");
    printf("Line 31\n");
    printf("Line 32\n");
    printf("Line 33\n");
    printf("Line 34\n");
    printf("Line 35\n");
    printf("Line 36\n");
    printf("Line 37\n");
    printf("Line 38\n");
    printf("Line 39\n");
    printf("Line 40\n");
    printf("Line 41 — now over 40 lines\n");
    g_counter = 500; /* MagicNumber: 500 */
}

int main(void) {
    process_data(1, 2, 3, 4, 5, 6, 7);
    read_user_input();
    return 0;
}
