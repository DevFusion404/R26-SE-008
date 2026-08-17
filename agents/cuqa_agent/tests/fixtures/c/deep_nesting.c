/* C fixture: deep_nesting.c — DeepNesting smell (max depth > 4) */
#include <stdio.h>

int check_deep(int a, int b, int c, int d, int e) {
    if (a > 0) {
        if (b > 0) {
            if (c > 0) {
                if (d > 0) {
                    if (e > 0) {
                        return 1;  /* depth 5 braces → DeepNesting triggered (> 4) */
                    }
                }
            }
        }
    }
    return 0;
}
