#include "sensorium-runtime-internal.h"

int sensorium_runtime_i2c_register_bus(struct sensorium_runtime_bus *bus)
{
	struct i2c_adapter *adapter = &bus->u.i2c.adapter;
	unsigned int index;
	int ret;

	ret = sensorium_runtime_parse_i2c_name(bus->name, &index);
	if (ret)
		return ret;

	bus->u.i2c.index = index;
	snprintf(adapter->name, sizeof(adapter->name), "%s-runtime-%s",
		 SENSORIUM_DRIVER_NAME, bus->name);
	adapter->owner = THIS_MODULE;
	adapter->class = I2C_CLASS_HWMON;
	adapter->dev.parent = &bus->runtime->sim->pdev->dev;
	adapter->nr = index;
	i2c_set_adapdata(adapter, bus);
	return 0;
}

static int sensorium_runtime_i2c_exec(struct sensorium_runtime_device *dev,
				      struct i2c_msg *msgs, int num)
{
	struct sensorium_runtime_i2c_req *req;
	struct sensorium_runtime_i2c_msg_desc *descs;
	u8 *reply_buf = NULL;
	u8 *tx_data;
	size_t req_len;
	size_t reply_len = 0;
	size_t data_len = 0;
	size_t rx_len = 0;
	size_t desc_len;
	unsigned int i;
	unsigned int offset = 0;
	u16 addr;
	int ret;

	if (num <= 0 || num > SENSORIUM_RUNTIME_MAX_I2C_MSGS)
		return -EOPNOTSUPP;

	addr = msgs[0].addr;
	for (i = 0; i < num; ++i) {
		if (msgs[i].addr != addr)
			return -EOPNOTSUPP;
		if (msgs[i].flags & I2C_M_RD)
			rx_len += msgs[i].len;
		else
			data_len += msgs[i].len;
	}

	desc_len = sizeof(*descs) * num;
	req_len = sizeof(*req) + desc_len + data_len;
	if (req_len > SENSORIUM_RUNTIME_MAX_PAYLOAD)
		return -EMSGSIZE;

	if (rx_len > SENSORIUM_RUNTIME_MAX_PAYLOAD)
		return -EMSGSIZE;

	req = kvzalloc(req_len, GFP_KERNEL);
	if (!req)
		return -ENOMEM;

	memset(req, 0, sizeof(*req));
	req->device_handle = dev->handle;
	req->bus_handle = dev->bus ? dev->bus->handle : 0;
	req->num_msgs = num;
	req->data_len = data_len;
	descs = (struct sensorium_runtime_i2c_msg_desc *)req->data;
	tx_data = req->data + desc_len;

	for (i = 0; i < num; ++i) {
		descs[i].addr = msgs[i].addr;
		descs[i].flags = msgs[i].flags;
		descs[i].len = msgs[i].len;
		if (msgs[i].flags & I2C_M_RD)
			continue;
		memcpy(tx_data + offset, msgs[i].buf, msgs[i].len);
		offset += msgs[i].len;
	}

	if (rx_len) {
		reply_buf = kvmalloc(rx_len, GFP_KERNEL);
		if (!reply_buf) {
			kvfree(req);
			return -ENOMEM;
		}
		reply_len = rx_len;
	}

	ret = sensorium_runtime_send_request(dev->runtime,
					     SENSORIUM_RUNTIME_REQ_I2C_XFER,
					     req, req_len, reply_buf,
					     rx_len ? &reply_len : NULL);
	if (ret)
		goto out;
	if (reply_len != rx_len) {
		ret = -EPROTO;
		goto out;
	}

	offset = 0;
	for (i = 0; i < num; ++i) {
		if (!(msgs[i].flags & I2C_M_RD))
			continue;
		memcpy(msgs[i].buf, reply_buf + offset, msgs[i].len);
		offset += msgs[i].len;
	}

	ret = num;

out:
	kvfree(reply_buf);
	kvfree(req);
	return ret;
}

static int sensorium_runtime_i2c_master_xfer(struct i2c_adapter *adapter,
					     struct i2c_msg *msgs, int num)
{
	struct sensorium_runtime_bus *bus = i2c_get_adapdata(adapter);
	struct sensorium_runtime_device *dev;
	u16 addr;

	if (!bus || num <= 0)
		return -EINVAL;

	addr = msgs[0].addr;
	mutex_lock(&bus->runtime->lock);
	dev = sensorium_runtime_find_i2c_device_locked(bus, addr);
	mutex_unlock(&bus->runtime->lock);
	if (!dev)
		return -ENXIO;

	return sensorium_runtime_i2c_exec(dev, msgs, num);
}

static s32 sensorium_runtime_i2c_smbus_xfer(struct i2c_adapter *adapter, u16 addr,
					    unsigned short flags, char read_write,
					    u8 command, int size,
					    union i2c_smbus_data *data)
{
	u8 buf[1 + I2C_SMBUS_BLOCK_MAX];
	struct i2c_msg msgs[2];
	int ret;
	int read_len = 0;

	memset(msgs, 0, sizeof(msgs));

	switch (size) {
	case I2C_SMBUS_QUICK:
		msgs[0].addr = addr;
		msgs[0].flags = (read_write == I2C_SMBUS_READ) ? I2C_M_RD : 0;
		msgs[0].len = 0;
		ret = sensorium_runtime_i2c_master_xfer(adapter, msgs, 1);
		return ret < 0 ? ret : 0;
	case I2C_SMBUS_BYTE:
		msgs[0].addr = addr;
		if (read_write == I2C_SMBUS_READ) {
			msgs[0].flags = I2C_M_RD;
			msgs[0].len = 1;
			buf[0] = 0;
			msgs[0].buf = buf;
			ret = sensorium_runtime_i2c_master_xfer(adapter, msgs, 1);
			if (ret < 0)
				return ret;
			data->byte = buf[0];
			return 0;
		}
		buf[0] = command;
		msgs[0].buf = buf;
		msgs[0].len = 1;
		ret = sensorium_runtime_i2c_master_xfer(adapter, msgs, 1);
		return ret < 0 ? ret : 0;
	case I2C_SMBUS_BYTE_DATA:
		buf[0] = command;
		msgs[0].addr = addr;
		msgs[0].buf = buf;
		msgs[0].len = 1;
		if (read_write == I2C_SMBUS_READ) {
			msgs[1].addr = addr;
			msgs[1].flags = I2C_M_RD;
			msgs[1].buf = &buf[1];
			msgs[1].len = 1;
			ret = sensorium_runtime_i2c_master_xfer(adapter, msgs, 2);
			if (ret < 0)
				return ret;
			data->byte = buf[1];
			return 0;
		}
		buf[1] = data->byte;
		msgs[0].len = 2;
		ret = sensorium_runtime_i2c_master_xfer(adapter, msgs, 1);
		return ret < 0 ? ret : 0;
	case I2C_SMBUS_WORD_DATA:
	case I2C_SMBUS_PROC_CALL:
		buf[0] = command;
		msgs[0].addr = addr;
		msgs[0].buf = buf;
		if (read_write == I2C_SMBUS_READ) {
			msgs[0].len = 1;
			msgs[1].addr = addr;
			msgs[1].flags = I2C_M_RD;
			msgs[1].buf = &buf[1];
			msgs[1].len = 2;
			ret = sensorium_runtime_i2c_master_xfer(adapter, msgs, 2);
			if (ret < 0)
				return ret;
			data->word = buf[1] | (buf[2] << 8);
			return 0;
		}
		buf[1] = data->word & 0xff;
		buf[2] = (data->word >> 8) & 0xff;
		msgs[0].len = 3;
		ret = sensorium_runtime_i2c_master_xfer(adapter, msgs, 1);
		if (ret < 0)
			return ret;
		if (size == I2C_SMBUS_PROC_CALL) {
			msgs[0].len = 1;
			msgs[1].addr = addr;
			msgs[1].flags = I2C_M_RD;
			msgs[1].buf = &buf[1];
			msgs[1].len = 2;
			ret = sensorium_runtime_i2c_master_xfer(adapter, msgs, 2);
			if (ret < 0)
				return ret;
			data->word = buf[1] | (buf[2] << 8);
		}
		return 0;
	case I2C_SMBUS_BLOCK_DATA:
	case I2C_SMBUS_I2C_BLOCK_BROKEN:
	case I2C_SMBUS_I2C_BLOCK_DATA:
		buf[0] = command;
		msgs[0].addr = addr;
		msgs[0].buf = buf;
		if (read_write == I2C_SMBUS_READ) {
			read_len = data && data->block[0] ?
				min_t(int, data->block[0], I2C_SMBUS_BLOCK_MAX) :
				I2C_SMBUS_BLOCK_MAX;
			msgs[0].len = 1;
			msgs[1].addr = addr;
			msgs[1].flags = I2C_M_RD;
			msgs[1].buf = &buf[1];
			msgs[1].len = read_len;
			ret = sensorium_runtime_i2c_master_xfer(adapter, msgs, 2);
			if (ret < 0)
				return ret;
			data->block[0] = read_len;
			memcpy(&data->block[1], &buf[1], read_len);
			return 0;
		}
		read_len = min_t(int, data ? data->block[0] : 0, I2C_SMBUS_BLOCK_MAX);
		memcpy(&buf[1], &data->block[1], read_len);
		msgs[0].len = 1 + read_len;
		ret = sensorium_runtime_i2c_master_xfer(adapter, msgs, 1);
		return ret < 0 ? ret : 0;
	default:
		return -EOPNOTSUPP;
	}
}

static u32 sensorium_runtime_i2c_functionality(struct i2c_adapter *adapter)
{
	return I2C_FUNC_I2C |
	       I2C_FUNC_SMBUS_EMUL_ALL |
	       I2C_FUNC_SMBUS_READ_BLOCK_DATA;
}

const struct i2c_algorithm sensorium_runtime_i2c_algorithm = {
	.master_xfer = sensorium_runtime_i2c_master_xfer,
	.smbus_xfer = sensorium_runtime_i2c_smbus_xfer,
	.functionality = sensorium_runtime_i2c_functionality,
};
