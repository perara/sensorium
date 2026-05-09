#ifndef SENSORIUM_RUNTIME_UART_INTERNAL_H
#define SENSORIUM_RUNTIME_UART_INTERNAL_H

#include "sensorium-runtime-internal.h"

struct sensorium_runtime_device *
sensorium_runtime_uart_device_from_tty(struct tty_struct *tty);

size_t
sensorium_runtime_uart_tx_used_locked(const struct sensorium_runtime_device *dev);
size_t
sensorium_runtime_uart_tx_room_locked(const struct sensorium_runtime_device *dev);
size_t
sensorium_runtime_uart_tx_copy_in_locked(struct sensorium_runtime_device *dev,
					 const u8 *buf, size_t count);
size_t
sensorium_runtime_uart_tx_copy_out_locked(struct sensorium_runtime_device *dev,
					  u8 *buf, size_t count);
void
sensorium_runtime_uart_tx_consume_locked(struct sensorium_runtime_device *dev,
					 size_t count);
void
sensorium_runtime_uart_tx_reset_locked(struct sensorium_runtime_device *dev);
void
sensorium_runtime_uart_capture_termios_locked(struct sensorium_runtime_device *dev,
					      const struct ktermios *termios,
					      u32 baud_rate);
int sensorium_runtime_uart_push_config(struct sensorium_runtime_device *dev);
int sensorium_runtime_uart_send_control(struct sensorium_runtime_device *dev,
					u32 modem_mask, u32 modem_values);
void sensorium_runtime_uart_tx_work(struct work_struct *work);

extern const struct tty_operations sensorium_runtime_uart_tty_ops;
extern const struct tty_port_operations sensorium_runtime_uart_port_ops;

#endif
