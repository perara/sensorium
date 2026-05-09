#include <linux/i2c-dev.h>
#include <linux/i2c.h>
#include <linux/kernel.h>
#include <linux/module.h>
#include "sensorium-transport-alias-internal.h"

static int sensorium_parse_i2c_name(const char *name, unsigned int *index)
{
	const char *suffix;

	if (!name || !*name)
		return -EINVAL;
	if (strncmp(name, "i2c-", 4))
		return -EINVAL;

	suffix = name + 4;
	if (!*suffix)
		return -EINVAL;

	return kstrtouint(suffix, 10, index);
}

static int sensorium_i2c_check_addr(const struct sensorium_i2c_alias *alias,
				    u16 addr, bool tenbit)
{
	if (tenbit)
		return -EOPNOTSUPP;
	if (addr != alias->addr)
		return -ENXIO;

	return 0;
}

static int sensorium_i2c_master_xfer(struct i2c_adapter *adapter,
				     struct i2c_msg *msgs, int num)
{
	struct sensorium_i2c_alias *alias = i2c_get_adapdata(adapter);
	struct sensorium_device *sim =
		container_of(alias, struct sensorium_device, i2c_alias);
	int i;
	int ret = num;

	mutex_lock(&sim->lock);

	if (sim->fault_mode == SENSORIUM_FAULT_TIMEOUT) {
		ret = -ETIMEDOUT;
		goto out_unlock;
	}

	for (i = 0; i < num; ++i) {
		struct i2c_msg *msg = &msgs[i];
		u8 reg;
		u16 j;

		ret = sensorium_i2c_check_addr(alias, msg->addr,
					       msg->flags & I2C_M_TEN);
		if (ret)
			goto out_unlock;

		if (msg->flags & I2C_M_RD) {
			reg = alias->reg_ptr;
			for (j = 0; j < msg->len; ++j)
				msg->buf[j] = alias->registers[reg++];
			alias->reg_ptr = reg;
			continue;
		}

		if (!msg->len)
			continue;

		reg = msg->buf[0];
		alias->reg_ptr = reg;
		for (j = 1; j < msg->len; ++j)
			alias->registers[reg++] = msg->buf[j];
		if (msg->len > 1)
			alias->reg_ptr = reg;
	}

out_unlock:
	mutex_unlock(&sim->lock);
	return ret;
}

static s32 sensorium_i2c_smbus_xfer(struct i2c_adapter *adapter, u16 addr,
				    unsigned short flags, char read_write,
				    u8 command, int size,
				    union i2c_smbus_data *data)
{
	struct sensorium_i2c_alias *alias = i2c_get_adapdata(adapter);
	struct sensorium_device *sim =
		container_of(alias, struct sensorium_device, i2c_alias);
	s32 ret = 0;
	u8 len;

	mutex_lock(&sim->lock);

	if (sim->fault_mode == SENSORIUM_FAULT_TIMEOUT) {
		ret = -ETIMEDOUT;
		goto out_unlock;
	}

	ret = sensorium_i2c_check_addr(alias, addr, false);
	if (ret)
		goto out_unlock;

	switch (size) {
	case I2C_SMBUS_QUICK:
		ret = 0;
		break;
	case I2C_SMBUS_BYTE:
		if (read_write == I2C_SMBUS_READ)
			data->byte = alias->registers[alias->reg_ptr++];
		else
			alias->reg_ptr = command;
		ret = 0;
		break;
	case I2C_SMBUS_BYTE_DATA:
		if (read_write == I2C_SMBUS_READ)
			data->byte = alias->registers[command];
		else
			alias->registers[command] = data->byte;
		alias->reg_ptr = command + 1;
		ret = 0;
		break;
	case I2C_SMBUS_WORD_DATA:
	case I2C_SMBUS_PROC_CALL:
		if (read_write == I2C_SMBUS_READ ||
		    size == I2C_SMBUS_PROC_CALL) {
			data->word = alias->registers[command] |
				(alias->registers[(u8)(command + 1)] << 8);
		}
		if (read_write == I2C_SMBUS_WRITE ||
		    size == I2C_SMBUS_PROC_CALL) {
			alias->registers[command] = data->word & 0xff;
			alias->registers[(u8)(command + 1)] =
				(data->word >> 8) & 0xff;
		}
		alias->reg_ptr = command + 2;
		ret = 0;
		break;
	case I2C_SMBUS_BLOCK_DATA:
	case I2C_SMBUS_I2C_BLOCK_BROKEN:
	case I2C_SMBUS_I2C_BLOCK_DATA:
		if (read_write == I2C_SMBUS_READ) {
			len = data && data->block[0] ?
				min_t(u8, data->block[0], I2C_SMBUS_BLOCK_MAX) :
				I2C_SMBUS_BLOCK_MAX;
			data->block[0] = len;
			memcpy(&data->block[1], &alias->registers[command], len);
			alias->reg_ptr = command + len;
		} else {
			len = data ? min_t(u8, data->block[0],
						I2C_SMBUS_BLOCK_MAX) : 0;
			memcpy(&alias->registers[command], &data->block[1], len);
			alias->reg_ptr = command + len;
			if (data)
				data->block[0] = len;
		}
		ret = 0;
		break;
	default:
		ret = -EOPNOTSUPP;
		break;
	}

out_unlock:
	mutex_unlock(&sim->lock);
	return ret;
}

static u32 sensorium_i2c_functionality(struct i2c_adapter *adapter)
{
	return I2C_FUNC_I2C |
	       I2C_FUNC_SMBUS_EMUL_ALL |
	       I2C_FUNC_SMBUS_READ_BLOCK_DATA;
}

static const struct i2c_algorithm sensorium_i2c_algorithm = {
	.master_xfer = sensorium_i2c_master_xfer,
	.smbus_xfer = sensorium_i2c_smbus_xfer,
	.functionality = sensorium_i2c_functionality,
};

int sensorium_i2c_alias_register(struct sensorium_device *sim)
{
	struct sensorium_i2c_alias *alias = &sim->i2c_alias;
	int ret;

	ret = sensorium_parse_i2c_name(sim->transport_device_name, &alias->index);
	if (ret) {
		pr_err("%s: invalid I2C transport device name '%s' (expected i2c-N)\n",
		       SENSORIUM_DRIVER_NAME, sim->transport_device_name);
		return ret;
	}

	if (sensorium_i2c_address > 0x7f) {
		pr_err("%s: invalid I2C address 0x%x (expected 7-bit address)\n",
		       SENSORIUM_DRIVER_NAME, sensorium_i2c_address);
		return -EINVAL;
	}

	strscpy(alias->name, sim->transport_device_name, sizeof(alias->name));
	alias->addr = sensorium_i2c_address;
	snprintf(alias->adapter.name, sizeof(alias->adapter.name),
		 "%s %s", SENSORIUM_DRIVER_NAME, sim->instance_name);
	alias->adapter.owner = THIS_MODULE;
	alias->adapter.class = I2C_CLASS_HWMON;
	alias->adapter.algo = &sensorium_i2c_algorithm;
	alias->adapter.dev.parent = &sim->pdev->dev;
	alias->adapter.nr = alias->index;
	i2c_set_adapdata(&alias->adapter, alias);

	ret = i2c_add_numbered_adapter(&alias->adapter);
	if (ret) {
		pr_err("%s: failed to register I2C adapter /dev/%s: %d\n",
		       SENSORIUM_DRIVER_NAME, sim->transport_device_name, ret);
		return ret;
	}

	alias->registered = true;
	return 0;
}

void sensorium_i2c_alias_unregister(struct sensorium_device *sim)
{
	struct sensorium_i2c_alias *alias = &sim->i2c_alias;

	if (!alias->registered)
		return;

	i2c_del_adapter(&alias->adapter);
	alias->registered = false;
}
