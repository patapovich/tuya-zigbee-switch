#ifndef HAL_PRINTF_H
#define HAL_PRINTF_H

#ifdef HAL_TELINK

extern int tl_printf(const char *format, ...);

/* Only a debug build (make DEBUG=1: UART_PRINTF_MODE=1) has a console. In a
 * release build the SDK's drv_putchar() is empty, so a printf call printed
 * nothing and cost only flash (its format string and the call, ~3.7 KB on
 * the AVATTO ZDMS16-2, whose image is close to the SDK's OTA size limit) and
 * the time to walk the format. The arguments are not evaluated then: they
 * must have no side effects. */
#if UART_PRINTF_MODE
#define printf    tl_printf
#else
#define printf(...)    ((void)0)
#endif

#elif defined(HAL_SILABS)

#include "stdio.h"

#else
#include <stdio.h>
#endif

#endif
