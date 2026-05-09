#ifndef SENSORIUM_TRANSPORT_ALIAS_INTERNAL_H
#define SENSORIUM_TRANSPORT_ALIAS_INTERNAL_H

#include "sensorium.h"

int sensorium_i2c_alias_register(struct sensorium_device *sim);
void sensorium_i2c_alias_unregister(struct sensorium_device *sim);

int sensorium_spi_alias_register(struct sensorium_device *sim);
void sensorium_spi_alias_unregister(struct sensorium_device *sim);

int sensorium_uart_alias_register(struct sensorium_device *sim);
void sensorium_uart_alias_unregister(struct sensorium_device *sim);

#endif /* SENSORIUM_TRANSPORT_ALIAS_INTERNAL_H */
