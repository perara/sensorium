#include <linux/err.h>
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/version.h>
#include <linux/iio/events.h>
#include <linux/iio/iio.h>
#include "sensorium.h"

#if IS_ENABLED(CONFIG_IIO)

static const struct iio_event_spec sensorium_iio_temp_event_spec[] = {
	{
		.type = IIO_EV_TYPE_THRESH,
		.dir = IIO_EV_DIR_RISING,
		.mask_separate = BIT(IIO_EV_INFO_VALUE) | BIT(IIO_EV_INFO_ENABLE),
	},
};

static const struct iio_chan_spec sensorium_iio_channels_basic[] = {
	{
		.type = IIO_TEMP,
		.indexed = 1,
		.channel = 0,
		.info_mask_separate = BIT(IIO_CHAN_INFO_PROCESSED) |
				      BIT(IIO_CHAN_INFO_CALIBBIAS),
	},
	{
		.type = IIO_PRESSURE,
		.indexed = 1,
		.channel = 1,
		.info_mask_separate = BIT(IIO_CHAN_INFO_PROCESSED) |
				      BIT(IIO_CHAN_INFO_CALIBBIAS),
	},
};

static const struct iio_chan_spec sensorium_iio_channels_plus[] = {
	{
		.type = IIO_TEMP,
		.indexed = 1,
		.channel = 0,
		.info_mask_separate = BIT(IIO_CHAN_INFO_PROCESSED) |
				      BIT(IIO_CHAN_INFO_CALIBBIAS),
		.event_spec = sensorium_iio_temp_event_spec,
		.num_event_specs = ARRAY_SIZE(sensorium_iio_temp_event_spec),
	},
	{
		.type = IIO_PRESSURE,
		.indexed = 1,
		.channel = 1,
		.info_mask_separate = BIT(IIO_CHAN_INFO_PROCESSED) |
				      BIT(IIO_CHAN_INFO_CALIBBIAS),
	},
	{
		.type = IIO_HUMIDITYRELATIVE,
		.indexed = 1,
		.channel = 2,
		.info_mask_separate = BIT(IIO_CHAN_INFO_PROCESSED) |
				      BIT(IIO_CHAN_INFO_CALIBBIAS),
	},
};

struct sensorium_iio_priv {
	struct sensorium_device *sim;
};

static void sensorium_iio_bounce_channel(int *value, int *step,
					 int minimum, int maximum)
{
	int next = *value + *step;

	if (next > maximum || next < minimum) {
		*step = -*step;
		next = *value + *step;
	}

	*value = clamp(next, minimum, maximum);
}

static void sensorium_iio_maybe_push_events(struct sensorium_device *sim)
{
	struct sensorium_iio_state *iio = &sim->iio;
	s64 ts;
	int previous_value, current_value;

	if (!iio->indio_dev || !iio->temperature_event_enabled)
		return;

	previous_value =
		iio->last_temperature_reported_millic + iio->temperature_bias_millic;
	current_value = iio->temperature_millic + iio->temperature_bias_millic;
	iio->last_temperature_reported_millic = iio->temperature_millic;

	if (previous_value >= iio->temperature_thresh_rising_millic ||
	    current_value < iio->temperature_thresh_rising_millic)
		return;

	ts = iio_get_time_ns(iio->indio_dev);
	iio_push_event(iio->indio_dev,
		       IIO_UNMOD_EVENT_CODE(IIO_TEMP, 0,
					    IIO_EV_TYPE_THRESH,
					    IIO_EV_DIR_RISING),
		       ts);
}

static void sensorium_iio_update_work(struct work_struct *work)
{
	struct sensorium_iio_state *iio =
		container_of(to_delayed_work(work), struct sensorium_iio_state,
			     update_work);
	struct sensorium_device *sim =
		container_of(iio, struct sensorium_device, iio);

	mutex_lock(&sim->lock);
	if (sim->fault_mode != SENSORIUM_FAULT_STALE_DATA) {
		sensorium_iio_bounce_channel(&iio->temperature_millic,
					     &iio->temperature_step_millic,
					     iio->temperature_min_millic,
					     iio->temperature_max_millic);
		sensorium_iio_bounce_channel(&iio->pressure_pascal,
					     &iio->pressure_step_pascal,
					     iio->pressure_min_pascal,
					     iio->pressure_max_pascal);
		if (iio->humidity_enabled)
			sensorium_iio_bounce_channel(&iio->humidity_millipercent,
						     &iio->humidity_step_millipercent,
						     iio->humidity_min_millipercent,
						     iio->humidity_max_millipercent);
		sensorium_iio_maybe_push_events(sim);
	}
	mutex_unlock(&sim->lock);

	if (iio->update_interval_ms)
		mod_delayed_work(system_wq, &iio->update_work,
				 msecs_to_jiffies(iio->update_interval_ms));
}

static int sensorium_iio_read_raw(struct iio_dev *indio_dev,
				  const struct iio_chan_spec *chan,
				  int *val, int *val2, long mask)
{
	struct sensorium_iio_priv *priv = iio_priv(indio_dev);
	struct sensorium_device *sim = priv->sim;
	int ret = -EINVAL;

	mutex_lock(&sim->lock);
	if (sim->fault_mode == SENSORIUM_FAULT_TIMEOUT) {
		ret = -ETIMEDOUT;
		goto out_unlock;
	}

	switch (mask) {
	case IIO_CHAN_INFO_PROCESSED:
		switch (chan->type) {
		case IIO_TEMP:
			*val = sim->iio.temperature_millic +
			       sim->iio.temperature_bias_millic;
			ret = IIO_VAL_INT;
			break;
		case IIO_PRESSURE:
			*val = sim->iio.pressure_pascal +
			       sim->iio.pressure_bias_pascal;
			ret = IIO_VAL_INT;
			break;
		case IIO_HUMIDITYRELATIVE:
			*val = sim->iio.humidity_millipercent +
			       sim->iio.humidity_bias_millipercent;
			ret = IIO_VAL_INT;
			break;
		default:
			ret = -EINVAL;
			break;
		}
		break;
	case IIO_CHAN_INFO_CALIBBIAS:
		switch (chan->type) {
		case IIO_TEMP:
			*val = sim->iio.temperature_bias_millic;
			ret = IIO_VAL_INT;
			break;
		case IIO_PRESSURE:
			*val = sim->iio.pressure_bias_pascal;
			ret = IIO_VAL_INT;
			break;
		case IIO_HUMIDITYRELATIVE:
			*val = sim->iio.humidity_bias_millipercent;
			ret = IIO_VAL_INT;
			break;
		default:
			ret = -EINVAL;
			break;
		}
		break;
	default:
		ret = -EINVAL;
		break;
	}

out_unlock:
	mutex_unlock(&sim->lock);
	*val2 = 0;
	return ret;
}

static int sensorium_iio_write_raw(struct iio_dev *indio_dev,
				   const struct iio_chan_spec *chan,
				   int val, int val2, long mask)
{
	struct sensorium_iio_priv *priv = iio_priv(indio_dev);
	struct sensorium_device *sim = priv->sim;
	int ret = 0;

	if (mask != IIO_CHAN_INFO_CALIBBIAS || val2 != 0)
		return -EINVAL;

	mutex_lock(&sim->lock);
	switch (chan->type) {
	case IIO_TEMP:
		sim->iio.temperature_bias_millic = val;
		break;
	case IIO_PRESSURE:
		sim->iio.pressure_bias_pascal = val;
		break;
	case IIO_HUMIDITYRELATIVE:
		sim->iio.humidity_bias_millipercent = val;
		break;
	default:
		ret = -EINVAL;
		break;
	}
	mutex_unlock(&sim->lock);

	return ret;
}

static int sensorium_iio_read_event_config(struct iio_dev *indio_dev,
					   const struct iio_chan_spec *chan,
					   enum iio_event_type type,
					   enum iio_event_direction dir)
{
	struct sensorium_iio_priv *priv = iio_priv(indio_dev);
	struct sensorium_device *sim = priv->sim;
	int enabled;

	if (chan->type != IIO_TEMP || type != IIO_EV_TYPE_THRESH ||
	    dir != IIO_EV_DIR_RISING)
		return -EINVAL;

	mutex_lock(&sim->lock);
	enabled = sim->iio.temperature_event_enabled;
	mutex_unlock(&sim->lock);

	return enabled;
}

static int sensorium_iio_write_event_config(struct iio_dev *indio_dev,
					    const struct iio_chan_spec *chan,
					    enum iio_event_type type,
					    enum iio_event_direction dir,
#if LINUX_VERSION_CODE >= KERNEL_VERSION(6, 16, 0)
					    bool state)
#else
					    int state)
#endif
{
	struct sensorium_iio_priv *priv = iio_priv(indio_dev);
	struct sensorium_device *sim = priv->sim;

	if (chan->type != IIO_TEMP || type != IIO_EV_TYPE_THRESH ||
	    dir != IIO_EV_DIR_RISING)
		return -EINVAL;

	mutex_lock(&sim->lock);
	sim->iio.temperature_event_enabled = !!state;
	mutex_unlock(&sim->lock);

	return 0;
}

static int sensorium_iio_read_event_value(struct iio_dev *indio_dev,
					  const struct iio_chan_spec *chan,
					  enum iio_event_type type,
					  enum iio_event_direction dir,
					  enum iio_event_info info,
					  int *val, int *val2)
{
	struct sensorium_iio_priv *priv = iio_priv(indio_dev);
	struct sensorium_device *sim = priv->sim;

	if (chan->type != IIO_TEMP || type != IIO_EV_TYPE_THRESH ||
	    dir != IIO_EV_DIR_RISING || info != IIO_EV_INFO_VALUE)
		return -EINVAL;

	mutex_lock(&sim->lock);
	*val = sim->iio.temperature_thresh_rising_millic;
	mutex_unlock(&sim->lock);
	*val2 = 0;

	return IIO_VAL_INT;
}

static int sensorium_iio_write_event_value(struct iio_dev *indio_dev,
					   const struct iio_chan_spec *chan,
					   enum iio_event_type type,
					   enum iio_event_direction dir,
					   enum iio_event_info info,
					   int val, int val2)
{
	struct sensorium_iio_priv *priv = iio_priv(indio_dev);
	struct sensorium_device *sim = priv->sim;

	if (chan->type != IIO_TEMP || type != IIO_EV_TYPE_THRESH ||
	    dir != IIO_EV_DIR_RISING || info != IIO_EV_INFO_VALUE ||
	    val2 != 0)
		return -EINVAL;

	mutex_lock(&sim->lock);
	sim->iio.temperature_thresh_rising_millic = val;
	mutex_unlock(&sim->lock);

	return 0;
}

static const struct iio_info sensorium_iio_info = {
	.read_raw = sensorium_iio_read_raw,
	.write_raw = sensorium_iio_write_raw,
	.read_event_config = sensorium_iio_read_event_config,
	.write_event_config = sensorium_iio_write_event_config,
	.read_event_value = sensorium_iio_read_event_value,
	.write_event_value = sensorium_iio_write_event_value,
};

int sensorium_iio_register(struct sensorium_device *sim)
{
	struct sensorium_iio_priv *priv;
	struct iio_dev *indio_dev;
	const struct iio_chan_spec *channels;
	int num_channels;
	int ret;

	indio_dev = iio_device_alloc(&sim->pdev->dev, sizeof(*priv));
	if (!indio_dev)
		return -ENOMEM;

	priv = iio_priv(indio_dev);
	priv->sim = sim;

	if (sim->iio.humidity_enabled) {
		channels = sensorium_iio_channels_plus;
		num_channels = ARRAY_SIZE(sensorium_iio_channels_plus);
	} else {
		channels = sensorium_iio_channels_basic;
		num_channels = ARRAY_SIZE(sensorium_iio_channels_basic);
	}

	indio_dev->name = sim->instance_name;
	indio_dev->modes = INDIO_DIRECT_MODE;
	indio_dev->info = &sensorium_iio_info;
	indio_dev->channels = channels;
	indio_dev->num_channels = num_channels;

	INIT_DELAYED_WORK(&sim->iio.update_work, sensorium_iio_update_work);

	ret = iio_device_register(indio_dev);
	if (ret) {
		iio_device_free(indio_dev);
		return ret;
	}

	sim->iio.indio_dev = indio_dev;
	if (sim->iio.update_interval_ms)
		mod_delayed_work(system_wq, &sim->iio.update_work,
				 msecs_to_jiffies(sim->iio.update_interval_ms));

	return 0;
}

void sensorium_iio_unregister(struct sensorium_device *sim)
{
	if (!sim->iio.indio_dev)
		return;

	cancel_delayed_work_sync(&sim->iio.update_work);
	iio_device_unregister(sim->iio.indio_dev);
	iio_device_free(sim->iio.indio_dev);
	sim->iio.indio_dev = NULL;
}

#else

int sensorium_iio_register(struct sensorium_device *sim)
{
	return -EOPNOTSUPP;
}

void sensorium_iio_unregister(struct sensorium_device *sim)
{
}

#endif
