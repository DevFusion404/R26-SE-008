/*
 * utils.h
 * Sample header file for CUQA C parser tests.
 * This is a normal-sized header (< 300 lines) — no LargeHeaderFile smell.
 */

#ifndef UTILS_H
#define UTILS_H

#include <stddef.h>

/* Maximum buffer size constant */
#define MAX_BUF 1024

/* ------------------------------------------------------------------
 * Utility function declarations
 * ------------------------------------------------------------------ */

/**
 * safe_copy - Copy src into dst up to dst_size - 1 bytes.
 * @dst:      Destination buffer.
 * @src:      Source string.
 * @dst_size: Size of destination buffer (including null terminator).
 *
 * Returns the number of bytes copied (excluding null terminator).
 */
size_t safe_copy(char *dst, const char *src, size_t dst_size);

/**
 * clamp - Clamp value to [min_val, max_val].
 * @value:   Input value.
 * @min_val: Minimum bound (inclusive).
 * @max_val: Maximum bound (inclusive).
 *
 * Returns the clamped value.
 */
int clamp(int value, int min_val, int max_val);

/**
 * is_empty - Return non-zero if string is NULL or has zero length.
 * @s: Input string.
 */
int is_empty(const char *s);

#endif /* UTILS_H */
