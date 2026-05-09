#include <linux/version.h>
#include "sensorium-runtime-internal.h"

static int
sensorium_runtime_spi_transfer_one_message(struct spi_controller *ctlr,
					   struct spi_message *msg);

static int sensorium_runtime_spi_setup(struct spi_device *spi)
{
	return 0;
}

int sensorium_runtime_spi_register_bus(struct sensorium_runtime_bus *bus)
{
	struct spi_controller *ctlr;
	unsigned int index;
	int ret;

	ret = sensorium_runtime_parse_spi_name(bus->name, &index);
	if (ret)
		return ret;

	ctlr = spi_alloc_host(&bus->runtime->sim->pdev->dev, 0);
	if (!ctlr)
		return -ENOMEM;

	bus->u.spi.ctlr = ctlr;
	bus->u.spi.index = index;
	ctlr->bus_num = index;
	ctlr->num_chipselect = 256;
	ctlr->mode_bits = SPI_CPOL | SPI_CPHA | SPI_CS_HIGH | SPI_LSB_FIRST;
	ctlr->bits_per_word_mask = SPI_BPW_RANGE_MASK(1, 32);
	ctlr->setup = sensorium_runtime_spi_setup;
	ctlr->transfer_one_message = sensorium_runtime_spi_transfer_one_message;
	spi_controller_set_devdata(ctlr, bus);

	ret = spi_register_controller(ctlr);
	if (ret) {
		spi_controller_put(ctlr);
		bus->u.spi.ctlr = NULL;
		return ret;
	}

	return 0;
}

static u32
sensorium_runtime_spi_delay_to_usecs(const struct spi_delay *delay, u32 speed_hz)
{
	u64 usecs;

	if (!delay || !delay->value)
		return 0;

	switch (delay->unit) {
	case SPI_DELAY_UNIT_USECS:
		usecs = delay->value;
		break;
	case SPI_DELAY_UNIT_NSECS:
		usecs = DIV_ROUND_UP_ULL((u64)delay->value, 1000ULL);
		break;
	case SPI_DELAY_UNIT_SCK:
		if (!speed_hz)
			return 0;
		usecs = DIV_ROUND_UP_ULL((u64)delay->value * 1000000ULL,
					 speed_hz);
		break;
	default:
		return 0;
	}

	return usecs > U32_MAX ? U32_MAX : (u32)usecs;
}

static u8 sensorium_runtime_spi_lane_width(u8 nbits)
{
	switch (nbits) {
	case 0:
	case 1:
		return 1;
	case 2:
	case 4:
	case 8:
		return nbits;
	default:
		return 0;
	}
}

static int sensorium_runtime_spi_exec_message(struct sensorium_runtime_device *dev,
					      struct spi_message *msg)
{
	struct sensorium_runtime_spi_req *req;
	struct spi_transfer *xfer;
	u8 *reply_buf = NULL;
	struct sensorium_runtime_spi_xfer_desc *descs;
	u8 *tx_data;
	size_t req_len;
	size_t reply_len = 0;
	size_t total_len = 0;
	size_t desc_len;
	unsigned int num_xfers = 0;
	unsigned int index = 0;
	unsigned int offset = 0;
	u32 speed_hz;
	u32 delay_usecs;
	u32 word_delay_usecs;
	u8 bits_per_word;
	u8 tx_nbits;
	u8 rx_nbits;
	int ret;

	list_for_each_entry(xfer, &msg->transfers, transfer_list) {
		if (++num_xfers > SENSORIUM_RUNTIME_MAX_SPI_XFERS)
			return -EOPNOTSUPP;
		total_len += xfer->len;
	}

	desc_len = sizeof(*descs) * num_xfers;
	req_len = sizeof(*req) + desc_len + total_len;
	if (req_len > SENSORIUM_RUNTIME_MAX_PAYLOAD)
		return -EMSGSIZE;

	req = kvzalloc(req_len, GFP_KERNEL);
	if (!req)
		return -ENOMEM;

	memset(req, 0, sizeof(*req));
	req->device_handle = dev->handle;
	req->bus_handle = dev->bus ? dev->bus->handle : 0;
	req->mode = msg->spi->mode;
	req->num_xfers = num_xfers;
	req->data_len = total_len;
	req->chip_select = spi_get_chipselect(msg->spi, 0);
	descs = (struct sensorium_runtime_spi_xfer_desc *)req->data;
	tx_data = req->data + desc_len;

	list_for_each_entry(xfer, &msg->transfers, transfer_list) {
		descs[index].len = xfer->len;
		speed_hz = xfer->speed_hz ?: msg->spi->max_speed_hz;
		bits_per_word = xfer->bits_per_word ?: msg->spi->bits_per_word ?: 8;
		tx_nbits = sensorium_runtime_spi_lane_width(xfer->tx_nbits);
		rx_nbits = sensorium_runtime_spi_lane_width(xfer->rx_nbits);
		if (!tx_nbits || !rx_nbits) {
			ret = -EOPNOTSUPP;
			goto out;
		}
		delay_usecs = sensorium_runtime_spi_delay_to_usecs(&xfer->delay,
								   speed_hz);
		word_delay_usecs =
			sensorium_runtime_spi_delay_to_usecs(&xfer->word_delay,
							     speed_hz);
		descs[index].speed_hz = speed_hz;
		descs[index].delay_usecs = min_t(u32, delay_usecs, U16_MAX);
		descs[index].bits_per_word = bits_per_word;
		descs[index].cs_change = xfer->cs_change;
		descs[index].tx_nbits = tx_nbits;
		descs[index].rx_nbits = rx_nbits;
		descs[index].word_delay_usecs =
			min_t(u32, word_delay_usecs, U8_MAX);
		descs[index].has_tx = xfer->tx_buf ? 1 : 0;
		descs[index].has_rx = xfer->rx_buf ? 1 : 0;
		if (xfer->tx_buf)
			memcpy(tx_data + offset, xfer->tx_buf, xfer->len);
		else
			memset(tx_data + offset, 0, xfer->len);
		offset += xfer->len;
		index++;
	}

	if (total_len) {
		reply_buf = kvmalloc(total_len, GFP_KERNEL);
		if (!reply_buf) {
			kvfree(req);
			return -ENOMEM;
		}
		reply_len = total_len;
	}

	ret = sensorium_runtime_send_request(dev->runtime,
					     SENSORIUM_RUNTIME_REQ_SPI_XFER,
					     req, req_len, reply_buf,
					     total_len ? &reply_len : NULL);
	if (ret)
		goto out;
	if (reply_len != total_len) {
		ret = -EPROTO;
		goto out;
	}

	offset = 0;
	list_for_each_entry(xfer, &msg->transfers, transfer_list) {
		if (xfer->rx_buf)
			memcpy(xfer->rx_buf, reply_buf + offset, xfer->len);
		offset += xfer->len;
	}

	ret = total_len;

out:
	kvfree(reply_buf);
	kvfree(req);
	return ret;
}

static int
sensorium_runtime_spi_transfer_one_message(struct spi_controller *ctlr,
					   struct spi_message *msg)
{
	struct sensorium_runtime_bus *bus = spi_controller_get_devdata(ctlr);
	struct sensorium_runtime_device *dev;
	int ret;

	mutex_lock(&bus->runtime->lock);
	dev = sensorium_runtime_find_spi_device_locked(bus,
						       spi_get_chipselect(msg->spi, 0));
	mutex_unlock(&bus->runtime->lock);
	if (!dev) {
		dev_warn(&ctlr->dev,
			 "sensorium runtime spi missing device for bus=%s cs=%u\n",
			 bus->name, spi_get_chipselect(msg->spi, 0));
		msg->status = -ENODEV;
		spi_finalize_current_message(ctlr);
		return 0;
	}

	ret = sensorium_runtime_spi_exec_message(dev, msg);
	if (ret < 0)
		dev_warn(&msg->spi->dev,
			 "sensorium runtime spi transfer failed cs=%u ret=%d\n",
			 spi_get_chipselect(msg->spi, 0), ret);
	msg->status = ret < 0 ? ret : 0;
	msg->actual_length = ret > 0 ? ret : 0;
	spi_finalize_current_message(ctlr);
	return 0;
}

int sensorium_runtime_register_spi(struct sensorium_runtime_device *dev)
{
	struct spi_board_info board_info = { 0 };
	struct spi_device *spi;
	int ret;

	request_module("spidev");

	strscpy(board_info.modalias, "spidev", sizeof(board_info.modalias));
	board_info.max_speed_hz = dev->u.spi.max_speed_hz;
	board_info.bus_num = dev->bus->u.spi.index;
	board_info.chip_select = dev->location;
	board_info.mode = dev->u.spi.mode;

	spi = spi_new_device(dev->bus->u.spi.ctlr, &board_info);
	if (!spi)
		return -ENODEV;

#if LINUX_VERSION_CODE >= KERNEL_VERSION(6, 19, 0) || \
	(LINUX_VERSION_CODE >= KERNEL_VERSION(6, 12, 80) && \
	 LINUX_VERSION_CODE < KERNEL_VERSION(6, 13, 0))
	ret = device_set_driver_override(&spi->dev, "spidev");
#else
	ret = driver_set_override(&spi->dev, &spi->driver_override,
				 "spidev", strlen("spidev"));
#endif
	if (ret) {
		spi_unregister_device(spi);
		return ret;
	}

	spi->bits_per_word = dev->u.spi.bits_per_word;
	if (spi_setup(spi)) {
		spi_unregister_device(spi);
		return -ENODEV;
	}

	ret = device_attach(&spi->dev);
	if (ret <= 0) {
		spi_unregister_device(spi);
		return ret < 0 ? ret : -ENODEV;
	}

	dev->u.spi.spi = spi;
	dev->u.spi.registered = true;
	return 0;
}

void sensorium_runtime_destroy_spi_device(struct sensorium_runtime_device *dev)
{
	if (dev->u.spi.registered) {
		spi_unregister_device(dev->u.spi.spi);
		dev->u.spi.spi = NULL;
		dev->u.spi.registered = false;
	}
}
