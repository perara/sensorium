#include <linux/kernel.h>
#include "sensorium-transport-alias-internal.h"

static const char *
sensorium_default_transport_device_name(const struct sensorium_transport *transport)
{
	if (!transport)
		return "";

	switch (transport->type) {
	case SENSORIUM_TRANSPORT_I2C:
		return "i2c-1";
	case SENSORIUM_TRANSPORT_SPI:
		return "spidev0.0";
	case SENSORIUM_TRANSPORT_UART:
		return "ttyAMA0";
	default:
		return "";
	}
}

int sensorium_set_transport_device_name(struct sensorium_device *sim,
				       const char *name)
{
	const char *resolved = name;

	if (!resolved || !*resolved)
		resolved = sensorium_default_transport_device_name(sim->transport);

	if (!resolved || !*resolved) {
		sim->transport_device_name[0] = '\0';
		return 0;
	}

	if (strnlen(resolved, sizeof(sim->transport_device_name)) >=
	    sizeof(sim->transport_device_name))
		return -EINVAL;

	if (strpbrk(resolved, "/ \t\r\n"))
		return -EINVAL;

	strscpy(sim->transport_device_name, resolved,
		sizeof(sim->transport_device_name));
	return 0;
}

int sensorium_transport_alias_register(struct sensorium_device *sim)
{
	if (!sim->transport_device_name[0])
		return 0;

	if (sim->transport->type == SENSORIUM_TRANSPORT_I2C)
		return sensorium_i2c_alias_register(sim);
	if (sim->transport->type == SENSORIUM_TRANSPORT_UART)
		return sensorium_uart_alias_register(sim);
	if (sim->transport->type != SENSORIUM_TRANSPORT_SPI)
		return 0;
	return sensorium_spi_alias_register(sim);
}

void sensorium_transport_alias_unregister(struct sensorium_device *sim)
{
	if (sim->transport->type == SENSORIUM_TRANSPORT_I2C) {
		sensorium_i2c_alias_unregister(sim);
		return;
	}

	if (sim->transport->type == SENSORIUM_TRANSPORT_UART) {
		sensorium_uart_alias_unregister(sim);
		return;
	}
	sensorium_spi_alias_unregister(sim);
}
