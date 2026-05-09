#include "sensorium-runtime-internal.h"

void sensorium_runtime_destroy_device(struct sensorium_runtime_device *dev)
{
	if (!dev)
		return;

	switch (dev->transport) {
	case SENSORIUM_TRANSPORT_SPI:
		sensorium_runtime_destroy_spi_device(dev);
		break;
	case SENSORIUM_TRANSPORT_UART:
		sensorium_runtime_destroy_uart_device(dev);
		break;
	default:
		break;
	}

	if (dev->bus)
		xa_erase(&dev->bus->location_index, dev->location);
	xa_erase(&dev->runtime->device_index, dev->handle);
	list_del(&dev->list);
	kfree(dev);
}

void sensorium_runtime_destroy_bus(struct sensorium_runtime_bus *bus)
{
	if (!bus)
		return;

	switch (bus->transport) {
	case SENSORIUM_TRANSPORT_I2C:
		if (bus->u.i2c.registered) {
			i2c_del_adapter(&bus->u.i2c.adapter);
			bus->u.i2c.registered = false;
		}
		break;
	case SENSORIUM_TRANSPORT_SPI:
		if (bus->u.spi.registered) {
			spi_unregister_controller(bus->u.spi.ctlr);
			bus->u.spi.ctlr = NULL;
			bus->u.spi.registered = false;
		}
		break;
	default:
		break;
	}

	xa_destroy(&bus->location_index);
	xa_erase(&bus->runtime->bus_index, bus->handle);
	list_del(&bus->list);
	kfree(bus);
}
