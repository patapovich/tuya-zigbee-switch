#include "hal/swd_io.h"
#pragma pack(push, 1)
#include "tl_common.h"
#pragma pack(pop)

/* SWD pin layer on the TLSR8258 (src/hal/swd_io.h). gpio_write / gpio_read /
 * gpio_set_output_en are inline register accesses (digital registers), so a bit
 * costs a few dozen cycles; input enable and the pulls of ports B and C sit in
 * analog registers, so they are set once per session, never per bit. A pin is
 * left exactly as the SDK sets an unused one at power-up
 * (platform/chip_8258/gpio_default.h): GPIO, input and output disabled, output
 * level 0, no pull.
 *
 * Both lines are driven open-drain: the output level stays 0, low is the output
 * enabled, high is the output disabled and the 10 kOhm pull-up. The wires to the
 * MCU's SWD pads have no series resistor (INSTALL), and the MCU's firmware may
 * drive these pins (the stock firmware holds SWCLK low while it runs, and takes
 * the pins some ms after its start, maybe in the middle of an attach try): the
 * module never drives a line high, so it never fights a pin driven low. The MCU
 * drives SWDIO itself (push-pull) in its turns. Rise time: 10 kOhm against the
 * pins and a few cm of wire, some 100 ns; a half bit is 1 us (hal_swd_delay). */

static GPIO_PinTypeDef g_clk;
static GPIO_PinTypeDef g_dio;
static uint8_t         g_taken;

/* Taken once per session, so a bit is a few loads and stores. */
static volatile unsigned char *g_clk_oen; /* output enable, active low */
static volatile unsigned char *g_dio_oen;
static volatile unsigned char *g_dio_in;
static unsigned char           g_clk_bit;
static unsigned char           g_dio_bit;
static uint8_t                 g_dio_host;  /* the module's turn on SWDIO */
static uint8_t                 g_dio_level; /* the level it sends then */

static void pin_released(GPIO_PinTypeDef pin) {
    gpio_set_output_en(pin, 0);
    gpio_write(pin, 0);
    gpio_set_input_en(pin, 0);
    gpio_setup_up_down_resistor(pin, PM_PIN_UP_DOWN_FLOAT);
}

uint8_t hal_swd_clk_idle_level(hal_gpio_pin_t clk) {
    GPIO_PinTypeDef pin = (GPIO_PinTypeDef)clk;

    gpio_set_func(pin, AS_GPIO);
    gpio_set_output_en(pin, 0);
    gpio_setup_up_down_resistor(pin, PM_PIN_PULLUP_10K);
    gpio_set_input_en(pin, 1);
    sleep_us(20);  // 10 kOhm against the pin and wire capacitance: well under 1 us
    uint8_t level = gpio_read(pin) ? 1 : 0;
    pin_released(pin);
    return level;
}

void hal_swd_take(hal_gpio_pin_t clk, hal_gpio_pin_t dio) {
    g_clk = (GPIO_PinTypeDef)clk;
    g_dio = (GPIO_PinTypeDef)dio;
    gpio_set_func(g_clk, AS_GPIO);
    gpio_set_func(g_dio, AS_GPIO);
    gpio_write(g_clk, 0);
    gpio_write(g_dio, 0);
    gpio_setup_up_down_resistor(g_clk, PM_PIN_PULLUP_10K);
    gpio_setup_up_down_resistor(g_dio, PM_PIN_PULLUP_10K);
    gpio_set_input_en(g_dio, 1);
    gpio_set_output_en(g_dio, 0);
    gpio_set_output_en(g_clk, 1);  // SWCLK starts low, as before
    g_clk_oen = &reg_gpio_oen(g_clk);
    g_dio_oen = &reg_gpio_oen(g_dio);
    g_dio_in  = &reg_gpio_in(g_dio);
    g_clk_bit = (unsigned char)(g_clk & 0xff);
    g_dio_bit = (unsigned char)(g_dio & 0xff);
    g_dio_host  = 0;
    g_dio_level = 1;
    g_taken   = 1;
}

void hal_swd_release(void) {
    if (!g_taken) {
        return;
    }
    pin_released(g_dio);
    pin_released(g_clk);
    g_taken = 0;
}

/* low: output enabled (level 0); high: output disabled, the pull-up */
static inline void line(volatile unsigned char *oen, unsigned char bit, uint8_t low) {
    if (low) {
        *oen &= (unsigned char)~bit;
    } else {
        *oen |= bit;
    }
}

void hal_swd_clk(uint8_t high) {
    line(g_clk_oen, g_clk_bit, !high);
}

void hal_swd_dio_drive(uint8_t on) {
    g_dio_host = on ? 1 : 0;
    line(g_dio_oen, g_dio_bit, g_dio_host && !g_dio_level);
}

void hal_swd_dio_set(uint8_t high) {
    g_dio_level = high ? 1 : 0;
    line(g_dio_oen, g_dio_bit, g_dio_host && !g_dio_level);
}

uint8_t hal_swd_dio_get(void) {
    return (*g_dio_in & g_dio_bit) ? 1 : 0;
}

void hal_swd_delay(void) {
    /* Half a bit: 1 us, so about 400 kHz with the pin accesses, the clock every
     * bench step has used with pyOCD. SWD has no minimum clock; interrupts only
     * stretch a bit. Bench test T0 measures the real rate. */
    sleep_us(1);
}
