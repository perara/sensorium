#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/slab.h>
#include <linux/version.h>
#include "sensorium.h"

static struct sensorium_device *sensorium;

static void sensorium_pdev_release(struct device *dev)
{
}

static struct platform_device sensorium_pdev = {
	.name = SENSORIUM_DRIVER_NAME,
	.dev.release = sensorium_pdev_release,
};

static bool sensorium_repeat_last_frame = true;
module_param_named(repeat_last_frame, sensorium_repeat_last_frame, bool, 0644);
MODULE_PARM_DESC(repeat_last_frame,
		 "Repeat the last injected frame when ingress underruns");

static char *sensorium_adapter_name = SENSORIUM_DEFAULT_ADAPTER_NAME;
module_param_named(adapter, sensorium_adapter_name, charp, 0644);
MODULE_PARM_DESC(adapter, "Select the simulated subsystem adapter");

static char *sensorium_transport_name = SENSORIUM_DEFAULT_TRANSPORT_NAME;
module_param_named(transport, sensorium_transport_name, charp, 0644);
MODULE_PARM_DESC(transport, "Select the simulated transport backend");

static char *sensorium_instance_name = SENSORIUM_DEFAULT_INSTANCE_NAME;
module_param_named(instance, sensorium_instance_name, charp, 0644);
MODULE_PARM_DESC(instance, "Select the simulated instance name");

static char *sensorium_transport_device_name;
module_param_named(transport_device_name, sensorium_transport_device_name, charp, 0644);
MODULE_PARM_DESC(transport_device_name,
		 "Optional transport-facing device node name such as spidev0.0 or ttyAMA0");

static char *sensorium_fault_mode_name = SENSORIUM_DEFAULT_FAULT_MODE_NAME;
module_param_named(fault_mode, sensorium_fault_mode_name, charp, 0644);
MODULE_PARM_DESC(fault_mode, "Select the simulated fault mode");

static char *sensorium_family_name = SENSORIUM_DEFAULT_FAMILY_NAME;
module_param_named(family, sensorium_family_name, charp, 0644);
MODULE_PARM_DESC(family, "Select the simulated sensor family");

static char *sensorium_sensor_name = SENSORIUM_DEFAULT_SENSOR_NAME;
module_param_named(sensor, sensorium_sensor_name, charp, 0644);
MODULE_PARM_DESC(sensor,
		 "Select the simulated sensor profile");

static int sensorium_iio_temperature_millic = 21500;
module_param_named(iio_temperature_millic, sensorium_iio_temperature_millic,
		   int, 0644);
MODULE_PARM_DESC(iio_temperature_millic,
		 "Initial IIO temperature channel value in millicelsius");

static int sensorium_iio_pressure_pascal = 101325;
module_param_named(iio_pressure_pascal, sensorium_iio_pressure_pascal, int, 0644);
MODULE_PARM_DESC(iio_pressure_pascal,
		 "Initial IIO pressure channel value in pascals");

static int sensorium_iio_temperature_step_millic = 250;
module_param_named(iio_temperature_step_millic, sensorium_iio_temperature_step_millic,
		   int, 0644);
MODULE_PARM_DESC(iio_temperature_step_millic,
		 "IIO temperature channel update step in millicelsius");

static int sensorium_iio_pressure_step_pascal = 120;
module_param_named(iio_pressure_step_pascal, sensorium_iio_pressure_step_pascal,
		   int, 0644);
MODULE_PARM_DESC(iio_pressure_step_pascal,
		 "IIO pressure channel update step in pascals");

static int sensorium_iio_humidity_millipercent = 45500;
module_param_named(iio_humidity_millipercent, sensorium_iio_humidity_millipercent,
		   int, 0644);
MODULE_PARM_DESC(iio_humidity_millipercent,
		 "Initial IIO humidity channel value in milli-percent relative humidity");

static int sensorium_iio_humidity_step_millipercent = 350;
module_param_named(iio_humidity_step_millipercent,
		   sensorium_iio_humidity_step_millipercent, int, 0644);
MODULE_PARM_DESC(iio_humidity_step_millipercent,
		 "IIO humidity channel update step in milli-percent relative humidity");

static int sensorium_iio_temperature_thresh_rising_millic = 26000;
module_param_named(iio_temperature_thresh_rising_millic,
		   sensorium_iio_temperature_thresh_rising_millic, int, 0644);
MODULE_PARM_DESC(iio_temperature_thresh_rising_millic,
		 "IIO temperature rising-threshold event value in millicelsius");

static char *sensorium_iio_profile_name = "environment-basic";
module_param_named(iio_profile, sensorium_iio_profile_name, charp, 0644);
MODULE_PARM_DESC(iio_profile,
		 "Select the simulated IIO profile (environment-basic or environment-plus)");

static unsigned int sensorium_update_interval_ms = 1000;
module_param_named(update_interval_ms, sensorium_update_interval_ms, uint, 0644);
MODULE_PARM_DESC(update_interval_ms,
		 "Simulation update interval in milliseconds");

unsigned int sensorium_i2c_address = 0x76;
module_param_named(i2c_address, sensorium_i2c_address, uint, 0644);
MODULE_PARM_DESC(i2c_address,
		 "7-bit I2C address exposed by the simulated i2c-dev adapter");

static int sensorium_probe(struct platform_device *pdev)
{
	const struct sensorium_adapter_ops *adapter;
	const struct sensorium_transport *transport;
	const struct sensorium_family *family;
	int ret;

	pr_info("%s: init start\n", SENSORIUM_DRIVER_NAME);

	sensorium = kzalloc(sizeof(*sensorium), GFP_KERNEL);
	if (!sensorium)
		return -ENOMEM;

	adapter = sensorium_find_adapter(sensorium_adapter_name);
	if (!adapter) {
		pr_err("%s: unknown adapter '%s'\n", SENSORIUM_DRIVER_NAME,
		       sensorium_adapter_name);
		kfree(sensorium);
		sensorium = NULL;
		return -EINVAL;
	}

	transport = sensorium_find_transport(sensorium_transport_name);
	if (!transport) {
		pr_err("%s: unknown transport '%s'\n", SENSORIUM_DRIVER_NAME,
		       sensorium_transport_name);
		kfree(sensorium);
		sensorium = NULL;
		return -EINVAL;
	}

	if (adapter->supports_transport &&
	    !adapter->supports_transport(transport->type)) {
		pr_err("%s: adapter '%s' does not support transport '%s'\n",
		       SENSORIUM_DRIVER_NAME, adapter->name, transport->name);
		kfree(sensorium);
		sensorium = NULL;
		return -EINVAL;
	}

	if (strcmp(sensorium_fault_mode_name, "none") &&
	    strcmp(sensorium_fault_mode_name, "stale-data") &&
	    strcmp(sensorium_fault_mode_name, "timeout")) {
		pr_err("%s: unknown fault mode '%s'\n", SENSORIUM_DRIVER_NAME,
		       sensorium_fault_mode_name);
		kfree(sensorium);
		sensorium = NULL;
		return -EINVAL;
	}

	if (sensorium_i2c_address > 0x7f) {
		pr_err("%s: invalid i2c_address 0x%x (expected 7-bit address)\n",
		       SENSORIUM_DRIVER_NAME, sensorium_i2c_address);
		kfree(sensorium);
		sensorium = NULL;
		return -EINVAL;
	}

	mutex_init(&sensorium->lock);
	INIT_DELAYED_WORK(&sensorium->frame_work, sensorium_frame_work);
	sensorium->repeat_last_frame = sensorium_repeat_last_frame;
	sensorium->pdev = pdev;
	sensorium->adapter = adapter;
	sensorium->transport = transport;
	sensorium->fault_mode = sensorium_find_fault_mode(sensorium_fault_mode_name);
	strscpy(sensorium->instance_name, sensorium_instance_name,
		sizeof(sensorium->instance_name));
	ret = sensorium_set_transport_device_name(sensorium,
						  sensorium_transport_device_name);
	if (ret) {
		pr_err("%s: invalid transport device name '%s'\n",
		       SENSORIUM_DRIVER_NAME,
		       sensorium_transport_device_name ?: "");
		goto err_free_frame;
	}

	if (adapter->type == SENSORIUM_ADAPTER_CAMERA) {
		unsigned int i;

		family = sensorium_find_family(sensorium_family_name);
		if (!family) {
			pr_err("%s: unknown sensor family '%s'\n",
			       SENSORIUM_DRIVER_NAME, sensorium_family_name);
			ret = -EINVAL;
			goto err_free_frame;
		}

		sensorium->family = family;
		sensorium->profile = sensorium_find_profile(family,
							    sensorium_sensor_name);
		if (!sensorium->profile) {
			pr_err("%s: unknown sensor '%s' for family '%s'\n",
			       SENSORIUM_DRIVER_NAME, sensorium_sensor_name,
			       family->name);
			ret = -EINVAL;
			goto err_free_frame;
		}

		sensorium->active_mode = sensorium_default_mode(sensorium);
		sensorium_set_inject_format(sensorium, V4L2_PIX_FMT_BGR32);
		for (i = 0; i < ARRAY_SIZE(sensorium->sample_lut); ++i)
			sensorium->sample_lut[i] =
				(i * 1023U + 127U) / 255U;
		sensorium->frame_interval_ns =
			(u64)sensorium->active_mode->frame_interval_ms *
			NSEC_PER_MSEC;
	} else {
		sensorium->iio.profile_name = sensorium_iio_profile_name;
		sensorium->iio.temperature_millic = sensorium_iio_temperature_millic;
		sensorium->iio.pressure_pascal = sensorium_iio_pressure_pascal;
		sensorium->iio.humidity_millipercent =
			sensorium_iio_humidity_millipercent;
		sensorium->iio.temperature_step_millic =
			sensorium_iio_temperature_step_millic;
		sensorium->iio.pressure_step_pascal =
			sensorium_iio_pressure_step_pascal;
		sensorium->iio.humidity_step_millipercent =
			sensorium_iio_humidity_step_millipercent;
		sensorium->iio.temperature_min_millic = -40000;
		sensorium->iio.temperature_max_millic = 125000;
		sensorium->iio.pressure_min_pascal = 80000;
		sensorium->iio.pressure_max_pascal = 120000;
		sensorium->iio.humidity_min_millipercent = 0;
		sensorium->iio.humidity_max_millipercent = 100000;
		sensorium->iio.temperature_thresh_rising_millic =
			sensorium_iio_temperature_thresh_rising_millic;
		sensorium->iio.last_temperature_reported_millic =
			sensorium_iio_temperature_millic;
		sensorium->iio.humidity_enabled =
			!strcmp(sensorium_iio_profile_name, "environment-plus");
		sensorium->iio.temperature_event_enabled =
			sensorium->iio.humidity_enabled;
		sensorium->iio.update_interval_ms = sensorium_update_interval_ms;
	}

	ret = adapter->register_instance(sensorium);
	if (ret)
		goto err_free_frame;

	if (adapter->type != SENSORIUM_ADAPTER_RUNTIME) {
		ret = sensorium_transport_alias_register(sensorium);
		if (ret) {
			pr_err("%s: transport alias register failed: %d\n",
			       SENSORIUM_DRIVER_NAME, ret);
			goto err_unregister_instance;
		}
	}

	if (adapter->type == SENSORIUM_ADAPTER_CAMERA)
		pr_info("%s: registered %s instance '%s' on %s for %s/%s\n",
			SENSORIUM_DRIVER_NAME, adapter->name,
			sensorium->instance_name, transport->name,
			sensorium->family->name, sensorium->profile->name);
	else
		pr_info("%s: registered %s instance '%s' on %s\n",
			SENSORIUM_DRIVER_NAME, adapter->name,
			sensorium->instance_name, transport->name);
	if (sensorium->transport_device_name[0])
		pr_info("%s: registered transport alias /dev/%s for %s instance '%s'\n",
			SENSORIUM_DRIVER_NAME, sensorium->transport_device_name,
			transport->name, sensorium->instance_name);
	if (transport->type == SENSORIUM_TRANSPORT_I2C)
		pr_info("%s: simulated I2C target address 0x%02x on /dev/%s\n",
			SENSORIUM_DRIVER_NAME, sensorium_i2c_address,
			sensorium->transport_device_name);
	platform_set_drvdata(pdev, sensorium);
	return 0;

err_unregister_instance:
	if (adapter->unregister_instance)
		adapter->unregister_instance(sensorium);
err_free_frame:
	kfree(sensorium);
	sensorium = NULL;
	return ret;
}

static void sensorium_remove(struct platform_device *pdev)
{
	struct sensorium_device *sim = platform_get_drvdata(pdev);

	if (!sim)
		return;

	sensorium_stop_streaming(sim);
	if (sim->adapter && sim->adapter->type != SENSORIUM_ADAPTER_RUNTIME)
		sensorium_transport_alias_unregister(sim);
	if (sim->adapter && sim->adapter->unregister_instance)
		sim->adapter->unregister_instance(sim);
	kfree(sim);
	sensorium = NULL;
}

static struct platform_driver sensorium_pdrv = {
	.probe = sensorium_probe,
#if LINUX_VERSION_CODE >= KERNEL_VERSION(6, 12, 0)
	.remove = sensorium_remove,
#else
	.remove_new = sensorium_remove,
#endif
	.driver = {
		.name = SENSORIUM_DRIVER_NAME,
	},
};

static int __init sensorium_init(void)
{
	int ret;

	ret = platform_device_register(&sensorium_pdev);
	if (ret)
		return ret;

	ret = platform_driver_register(&sensorium_pdrv);
	if (ret) {
		platform_device_unregister(&sensorium_pdev);
		return ret;
	}

	return 0;
}

static void __exit sensorium_exit(void)
{
	platform_driver_unregister(&sensorium_pdrv);
	platform_device_unregister(&sensorium_pdev);
}

module_init(sensorium_init);
module_exit(sensorium_exit);

MODULE_DESCRIPTION("Sensorium virtual media controller camera simulator");
MODULE_AUTHOR("Sensorium contributors");
MODULE_LICENSE("GPL");
