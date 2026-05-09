#include <linux/kernel.h>
#include "sensorium.h"
#include "sensorium-family-imx.h"

static const struct sensorium_family * const sensorium_families[] = {
	&sensorium_family_imx,
};

static bool sensorium_camera_supports_transport(enum sensorium_transport_type transport)
{
	return transport == SENSORIUM_TRANSPORT_VIRTUAL;
}

static bool sensorium_iio_supports_transport(enum sensorium_transport_type transport)
{
	return transport == SENSORIUM_TRANSPORT_I2C ||
	       transport == SENSORIUM_TRANSPORT_SPI ||
	       transport == SENSORIUM_TRANSPORT_UART ||
	       transport == SENSORIUM_TRANSPORT_VIRTUAL;
}

static bool sensorium_runtime_supports_transport(enum sensorium_transport_type transport)
{
	return transport == SENSORIUM_TRANSPORT_VIRTUAL;
}

static const struct sensorium_transport sensorium_transports[] = {
	{
		.name = "virtual",
		.type = SENSORIUM_TRANSPORT_VIRTUAL,
		.frame_ingress = true,
		.register_access = true,
	},
	{
		.name = "i2c",
		.type = SENSORIUM_TRANSPORT_I2C,
		.register_access = true,
	},
	{
		.name = "spi",
		.type = SENSORIUM_TRANSPORT_SPI,
		.register_access = true,
	},
	{
		.name = "uart",
		.type = SENSORIUM_TRANSPORT_UART,
		.register_access = true,
	},
};

static const struct sensorium_adapter_ops sensorium_adapters[] = {
	{
		.name = "camera",
		.type = SENSORIUM_ADAPTER_CAMERA,
		.supports_transport = sensorium_camera_supports_transport,
		.register_instance = sensorium_camera_register_instance,
		.unregister_instance = sensorium_camera_unregister_instance,
	},
	{
		.name = "iio",
		.type = SENSORIUM_ADAPTER_IIO,
		.supports_transport = sensorium_iio_supports_transport,
		.register_instance = sensorium_iio_register,
		.unregister_instance = sensorium_iio_unregister,
	},
	{
		.name = "runtime",
		.type = SENSORIUM_ADAPTER_RUNTIME,
		.supports_transport = sensorium_runtime_supports_transport,
		.register_instance = sensorium_runtime_register,
		.unregister_instance = sensorium_runtime_unregister,
	},
};

const struct sensorium_family *sensorium_find_family(const char *name)
{
	unsigned int i;

	if (!name || !*name)
		return sensorium_families[0];

	for (i = 0; i < ARRAY_SIZE(sensorium_families); ++i) {
		if (!strcmp(sensorium_families[i]->name, name))
			return sensorium_families[i];
	}

	return NULL;
}

const struct sensorium_transport *sensorium_find_transport(const char *name)
{
	unsigned int i;

	if (!name || !*name)
		return &sensorium_transports[0];

	for (i = 0; i < ARRAY_SIZE(sensorium_transports); ++i) {
		if (!strcmp(sensorium_transports[i].name, name))
			return &sensorium_transports[i];
	}

	return NULL;
}

const struct sensorium_adapter_ops *sensorium_find_adapter(const char *name)
{
	unsigned int i;

	if (!name || !*name)
		return &sensorium_adapters[0];

	for (i = 0; i < ARRAY_SIZE(sensorium_adapters); ++i) {
		if (!strcmp(sensorium_adapters[i].name, name))
			return &sensorium_adapters[i];
	}

	return NULL;
}

enum sensorium_fault_mode sensorium_find_fault_mode(const char *name)
{
	if (!name || !*name || !strcmp(name, "none"))
		return SENSORIUM_FAULT_NONE;
	if (!strcmp(name, "stale-data"))
		return SENSORIUM_FAULT_STALE_DATA;
	if (!strcmp(name, "timeout"))
		return SENSORIUM_FAULT_TIMEOUT;
	return SENSORIUM_FAULT_NONE;
}

const struct sensorium_profile *
sensorium_find_profile(const struct sensorium_family *family, const char *name)
{
	unsigned int i;

	if (!family)
		return NULL;

	if (!name || !*name)
		return sensorium_default_profile(family);

	for (i = 0; i < family->num_profiles; ++i) {
		if (!strcmp(family->profiles[i].name, name))
			return &family->profiles[i];
	}

	return NULL;
}

const struct sensorium_profile *
sensorium_default_profile(const struct sensorium_family *family)
{
	unsigned int i;

	if (!family)
		return NULL;

	if (family->default_sensor_name) {
		for (i = 0; i < family->num_profiles; ++i) {
			if (!strcmp(family->profiles[i].name,
				    family->default_sensor_name))
				return &family->profiles[i];
		}
	}

	return family->num_profiles ? &family->profiles[0] : NULL;
}

const struct sensorium_mode *sensorium_find_mode(const struct sensorium_device *sim,
						 u32 width, u32 height)
{
	unsigned int i;
	const struct sensorium_profile *profile = sim->profile;

	for (i = 0; i < profile->num_modes; i++) {
		if (profile->modes[i].width == width &&
		    profile->modes[i].height == height)
			return &profile->modes[i];
	}

	return sensorium_default_mode(sim);
}

const struct sensorium_mode *sensorium_default_mode(const struct sensorium_device *sim)
{
	return &sim->profile->modes[0];
}

size_t sensorium_max_frame_size(const struct sensorium_device *sim)
{
	size_t max_size = 0;
	unsigned int i;

	for (i = 0; i < sim->profile->num_modes; i++)
		max_size = max(max_size, (size_t)sim->profile->modes[i].frame_size);

	return max_size;
}
