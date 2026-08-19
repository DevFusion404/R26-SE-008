/* C fixture: unsafe_functions.c — UnsafeFunctionUsage smell (gets, strcpy, strcat, sprintf, scanf) */
#include <stdio.h>
#include <string.h>

void do_unsafe(char *dest, char *src, char *buf) {
    strcpy(dest, src);
    strcat(dest, " suffix");
    sprintf(buf, "formatted %s", dest);
    gets(buf);
    scanf("%s", buf);
}

void safe_commented_and_string(void) {
    // strcpy(a, b);   -- inside comment, must NOT trigger
    char *msg = "strcpy(dest, src)"; // inside string, must NOT trigger
}
