/* C fixture: clean.c — no code smells */
#include <stdio.h>

int add(int a, int b) {
    return a + b;
}

int main(void) {
    int result = add(1, 2);
    printf("Result: %d\n", result);
    return 0;
}
