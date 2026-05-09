#include "sensorium-runtime-uart-internal.h"

static int sensorium_runtime_uart_send(struct sensorium_runtime_device *dev,
				       u32 flags, u32 modem_mask,
				       u32 modem_values, const u8 *data,
				       size_t len);

size_t
sensorium_runtime_uart_tx_used_locked(const struct sensorium_runtime_device *dev)
{
	return dev->u.uart.tx_count;
}

size_t
sensorium_runtime_uart_tx_room_locked(const struct sensorium_runtime_device *dev)
{
	return dev->u.uart.tx_capacity > dev->u.uart.tx_count ?
	       dev->u.uart.tx_capacity - dev->u.uart.tx_count : 0;
}

size_t
sensorium_runtime_uart_tx_copy_in_locked(struct sensorium_runtime_device *dev,
					 const u8 *buf, size_t count)
{
	size_t room = sensorium_runtime_uart_tx_room_locked(dev);
	size_t total = min(count, room);
	size_t first;
	size_t second;

	if (!total)
		return 0;

	first = min(total, dev->u.uart.tx_capacity - dev->u.uart.tx_tail);
	memcpy(dev->u.uart.tx_queue + dev->u.uart.tx_tail, buf, first);
	second = total - first;
	if (second)
		memcpy(dev->u.uart.tx_queue, buf + first, second);
	dev->u.uart.tx_tail = (dev->u.uart.tx_tail + total) %
			      dev->u.uart.tx_capacity;
	dev->u.uart.tx_count += total;
	return total;
}

size_t
sensorium_runtime_uart_tx_copy_out_locked(struct sensorium_runtime_device *dev,
					  u8 *buf, size_t count)
{
	size_t total = min(count, dev->u.uart.tx_count);
	size_t first;
	size_t second;

	if (!total)
		return 0;

	first = min(total, dev->u.uart.tx_capacity - dev->u.uart.tx_head);
	memcpy(buf, dev->u.uart.tx_queue + dev->u.uart.tx_head, first);
	second = total - first;
	if (second)
		memcpy(buf + first, dev->u.uart.tx_queue, second);
	return total;
}

void
sensorium_runtime_uart_tx_consume_locked(struct sensorium_runtime_device *dev,
					 size_t count)
{
	count = min(count, dev->u.uart.tx_count);
	dev->u.uart.tx_head = (dev->u.uart.tx_head + count) %
			      dev->u.uart.tx_capacity;
	dev->u.uart.tx_count -= count;
	if (!dev->u.uart.tx_count)
		dev->u.uart.tx_head = dev->u.uart.tx_tail = 0;
}

void
sensorium_runtime_uart_tx_reset_locked(struct sensorium_runtime_device *dev)
{
	dev->u.uart.tx_head = 0;
	dev->u.uart.tx_tail = 0;
	dev->u.uart.tx_count = 0;
	dev->u.uart.tx_inflight = 0;
	wake_up_all(&dev->u.uart.tx_waitq);
}

static unsigned int
sensorium_runtime_uart_frame_bits_locked(const struct sensorium_runtime_device *dev)
{
	unsigned int bits = 1;
	unsigned int data_bits = 8;

	switch (dev->u.uart.cflag & CSIZE) {
	case CS5:
		data_bits = 5;
		break;
	case CS6:
		data_bits = 6;
		break;
	case CS7:
		data_bits = 7;
		break;
	default:
		data_bits = 8;
		break;
	}

	bits += data_bits;
	if (dev->u.uart.cflag & PARENB)
		bits += 1;
	bits += (dev->u.uart.cflag & CSTOPB) ? 2 : 1;
	return bits;
}

static u32
sensorium_runtime_uart_drain_delay_us_locked(const struct sensorium_runtime_device *dev,
					     size_t count)
{
	u64 bits;
	u64 baud = dev->u.uart.baud_rate ?: 115200;
	u64 usecs;

	if (!count)
		return 0;

	bits = (u64)count * sensorium_runtime_uart_frame_bits_locked(dev);
	usecs = DIV_ROUND_UP_ULL(bits * 1000000ULL, baud);
	usecs = clamp_val(usecs, 1000ULL, 100000ULL);
	return (u32)usecs;
}

void sensorium_runtime_uart_wakeup_writers(struct sensorium_runtime_device *dev)
{
	struct tty_struct *tty;

	wake_up_all(&dev->u.uart.tx_waitq);
	tty = tty_port_tty_get(&dev->u.uart.port);
	if (!tty)
		return;
	tty_wakeup(tty);
	tty_kref_put(tty);
}

void
sensorium_runtime_uart_mark_disconnected_locked(struct sensorium_runtime_device *dev,
						int status)
{
	dev->u.uart.disconnected = true;
	dev->u.uart.last_status = status;
	dev->u.uart.modem_inputs &= ~TIOCM_CAR;
	sensorium_runtime_uart_tx_reset_locked(dev);
}

void sensorium_runtime_uart_recover_locked(struct sensorium_runtime_device *dev)
{
	dev->u.uart.disconnected = false;
	dev->u.uart.last_status = 0;
	dev->u.uart.modem_inputs |= TIOCM_CAR;
}

void sensorium_runtime_uart_tx_work(struct work_struct *work)
{
	struct sensorium_runtime_device *dev =
		container_of(to_delayed_work(work), struct sensorium_runtime_device,
			     u.uart.tx_work);
	u8 chunk[256];
	size_t chunk_len;
	u32 drain_delay_us;
	size_t drained_len = 0;
	int ret;

	mutex_lock(&dev->lock);
	if (dev->u.uart.tx_inflight) {
		drained_len = dev->u.uart.tx_inflight;
		sensorium_runtime_uart_tx_consume_locked(dev, dev->u.uart.tx_inflight);
		dev->u.uart.tx_inflight = 0;
		if (!dev->u.uart.disconnected)
			sensorium_runtime_uart_recover_locked(dev);
	}
	if (dev->u.uart.disconnected) {
		mutex_unlock(&dev->lock);
		if (drained_len)
			sensorium_runtime_uart_wakeup_writers(dev);
		return;
	}
	chunk_len = sensorium_runtime_uart_tx_copy_out_locked(dev, chunk,
							      sizeof(chunk));
	drain_delay_us = sensorium_runtime_uart_drain_delay_us_locked(dev,
								      chunk_len);
	mutex_unlock(&dev->lock);

	if (drained_len)
		sensorium_runtime_uart_wakeup_writers(dev);
	if (!chunk_len)
		return;

	ret = sensorium_runtime_uart_send(dev, 0, 0, 0, chunk, chunk_len);
	if (ret < 0) {
		mutex_lock(&dev->lock);
		sensorium_runtime_uart_mark_disconnected_locked(dev, ret);
		mutex_unlock(&dev->lock);
		sensorium_runtime_uart_wakeup_writers(dev);
		return;
	}

	mutex_lock(&dev->lock);
	dev->u.uart.tx_inflight = chunk_len;
	mutex_unlock(&dev->lock);

	mod_delayed_work(system_wq, &dev->u.uart.tx_work,
			 usecs_to_jiffies(max_t(u32, drain_delay_us, 1000U)));
}

void sensorium_runtime_uart_inject_locked(struct sensorium_runtime_device *dev,
					  const u8 *data, size_t len)
{
	size_t chunk;
	ssize_t written;
	bool pushed = false;

	if (len && !dev->u.uart.throttled && !dev->u.uart.rx_count) {
		written = tty_insert_flip_string(&dev->u.uart.port, data, len);
		if (written > 0) {
			tty_flip_buffer_push(&dev->u.uart.port);
			pushed = true;
			data += written;
			len -= written;
		}
	}

	while (len && dev->u.uart.rx_count < dev->u.uart.rx_capacity) {
		chunk = min(len, dev->u.uart.rx_capacity - dev->u.uart.rx_tail);
		chunk = min(chunk, dev->u.uart.rx_capacity - dev->u.uart.rx_count);
		memcpy(dev->u.uart.rx_queue + dev->u.uart.rx_tail, data, chunk);
		dev->u.uart.rx_tail = (dev->u.uart.rx_tail + chunk) %
				      dev->u.uart.rx_capacity;
		dev->u.uart.rx_count += chunk;
		data += chunk;
		len -= chunk;
	}

	while (!dev->u.uart.throttled && dev->u.uart.rx_count) {
		chunk = min(dev->u.uart.rx_count,
			    dev->u.uart.rx_capacity - dev->u.uart.rx_head);
		written = tty_insert_flip_string(&dev->u.uart.port,
						 dev->u.uart.rx_queue + dev->u.uart.rx_head,
						 chunk);
		if (written <= 0)
			break;
		dev->u.uart.rx_head = (dev->u.uart.rx_head + written) %
				      dev->u.uart.rx_capacity;
		dev->u.uart.rx_count -= written;
		pushed = true;
		if ((size_t)written < chunk)
			break;
	}

	if (pushed)
		tty_flip_buffer_push(&dev->u.uart.port);
}

static int sensorium_runtime_uart_send(struct sensorium_runtime_device *dev,
				       u32 flags, u32 modem_mask,
				       u32 modem_values, const u8 *data,
				       size_t len)
{
	struct sensorium_runtime_uart_req *req;
	u8 *reply_buf = NULL;
	size_t req_len;
	size_t reply_len = SENSORIUM_RUNTIME_MAX_PAYLOAD;
	int ret;

	if (sizeof(*req) + len > SENSORIUM_RUNTIME_MAX_PAYLOAD)
		return -EMSGSIZE;

	req_len = sizeof(*req) + len;
	req = kvzalloc(req_len, GFP_KERNEL);
	if (!req)
		return -ENOMEM;

	reply_buf = kvmalloc(reply_len, GFP_KERNEL);
	if (!reply_buf) {
		kvfree(req);
		return -ENOMEM;
	}

	req->device_handle = dev->handle;
	req->flags = flags;
	req->len = len;
	req->modem_mask = modem_mask;
	req->modem_values = modem_values;
	if (len)
		memcpy(req->data, data, len);

	ret = sensorium_runtime_send_request(dev->runtime,
					     (flags & 1) ? SENSORIUM_RUNTIME_REQ_UART_CTRL :
					     SENSORIUM_RUNTIME_REQ_UART_TX,
					     req, req_len, reply_buf, &reply_len);
	kvfree(req);
	if (ret)
		goto out_free_reply;

	mutex_lock(&dev->lock);
	sensorium_runtime_uart_inject_locked(dev, reply_buf, reply_len);
	mutex_unlock(&dev->lock);
	ret = 0;

out_free_reply:
	kvfree(reply_buf);
	return ret;
}

void
sensorium_runtime_uart_capture_termios_locked(struct sensorium_runtime_device *dev,
					      const struct ktermios *termios,
					      u32 baud_rate)
{
	dev->u.uart.baud_rate = baud_rate ?: 115200;
	dev->u.uart.cflag = termios->c_cflag;
	dev->u.uart.iflag = termios->c_iflag;
	dev->u.uart.oflag = termios->c_oflag;
	dev->u.uart.lflag = termios->c_lflag;
}

int sensorium_runtime_uart_push_config(struct sensorium_runtime_device *dev)
{
	struct sensorium_runtime_uart_cfg_req req;
	int ret;

	memset(&req, 0, sizeof(req));
	req.device_handle = dev->handle;
	req.baud_rate = dev->u.uart.baud_rate;
	req.cflag = dev->u.uart.cflag;
	req.iflag = dev->u.uart.iflag;
	req.oflag = dev->u.uart.oflag;
	req.lflag = dev->u.uart.lflag;

	ret = sensorium_runtime_send_request(dev->runtime,
					     SENSORIUM_RUNTIME_REQ_UART_CFG,
					     &req, sizeof(req), NULL, NULL);
	mutex_lock(&dev->lock);
	if (ret)
		sensorium_runtime_uart_mark_disconnected_locked(dev, ret);
	else
		sensorium_runtime_uart_recover_locked(dev);
	mutex_unlock(&dev->lock);
	if (ret)
		sensorium_runtime_uart_wakeup_writers(dev);
	return ret;
}

int sensorium_runtime_uart_send_control(struct sensorium_runtime_device *dev,
					u32 modem_mask, u32 modem_values)
{
	return sensorium_runtime_uart_send(dev, 1, modem_mask, modem_values,
					   NULL, 0);
}
