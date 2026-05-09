#include "sensorium-runtime-uart-internal.h"

struct sensorium_runtime_device *
sensorium_runtime_uart_device_from_tty(struct tty_struct *tty)
{
	struct sensorium_runtime_uart_group *group;

	if (!tty || !tty->driver || !tty->driver->driver_state)
		return NULL;

	group = tty->driver->driver_state;
	if (tty->index >= group->num_ports)
		return NULL;

	return READ_ONCE(group->ports[tty->index]);
}

static int sensorium_runtime_uart_open(struct tty_struct *tty, struct file *file)
{
	struct sensorium_runtime_device *dev = sensorium_runtime_uart_device_from_tty(tty);
	int ret;

	if (!dev)
		return -ENODEV;

	ret = tty_port_open(&dev->u.uart.port, tty, file);
	if (ret)
		return ret;

	tty->driver_data = dev;
	mutex_lock(&dev->lock);
	sensorium_runtime_uart_capture_termios_locked(dev, &tty->termios,
						      tty_termios_baud_rate(&tty->termios));
	mutex_unlock(&dev->lock);
	ret = sensorium_runtime_uart_push_config(dev);
	if (ret) {
		tty_port_close(&dev->u.uart.port, tty, file);
		tty->driver_data = NULL;
		return ret;
	}
	return 0;
}

static void sensorium_runtime_uart_close(struct tty_struct *tty, struct file *file)
{
	struct sensorium_runtime_device *dev = tty->driver_data;

	if (!dev)
		return;

	tty_port_close(&dev->u.uart.port, tty, file);
	tty->driver_data = NULL;
}

static int sensorium_runtime_uart_port_activate(struct tty_port *port,
						struct tty_struct *tty)
{
	return 0;
}

static void sensorium_runtime_uart_port_shutdown(struct tty_port *port)
{
	struct sensorium_runtime_device *dev =
		container_of(port, struct sensorium_runtime_device, u.uart.port);

	cancel_delayed_work_sync(&dev->u.uart.tx_work);
	mutex_lock(&dev->lock);
	sensorium_runtime_uart_tx_reset_locked(dev);
	mutex_unlock(&dev->lock);
}

static ssize_t sensorium_runtime_uart_write(struct tty_struct *tty,
					    const u8 *buf, size_t count)
{
	struct sensorium_runtime_device *dev = sensorium_runtime_uart_device_from_tty(tty);
	size_t write_len;
	int status = 0;

	if (!dev)
		return -ENODEV;
	if (!count)
		return 0;

	mutex_lock(&dev->lock);
	if (dev->u.uart.disconnected) {
		status = dev->u.uart.last_status ?: -EPIPE;
		write_len = 0;
	} else {
		write_len = sensorium_runtime_uart_tx_copy_in_locked(dev, buf, count);
	}
	mutex_unlock(&dev->lock);
	if (status)
		return status;

	if (!write_len)
		return 0;

	queue_delayed_work(system_wq, &dev->u.uart.tx_work, 0);
	return write_len;
}

static unsigned int sensorium_runtime_uart_write_room(struct tty_struct *tty)
{
	struct sensorium_runtime_device *dev = sensorium_runtime_uart_device_from_tty(tty);
	size_t room;

	if (!dev)
		return 0;
	mutex_lock(&dev->lock);
	room = dev->u.uart.disconnected ? 0 :
	       sensorium_runtime_uart_tx_room_locked(dev);
	mutex_unlock(&dev->lock);
	return room > UINT_MAX ? UINT_MAX : room;
}

static unsigned int sensorium_runtime_uart_chars_in_buffer(struct tty_struct *tty)
{
	struct sensorium_runtime_device *dev = sensorium_runtime_uart_device_from_tty(tty);
	size_t pending;

	if (!dev)
		return 0;
	mutex_lock(&dev->lock);
	pending = sensorium_runtime_uart_tx_used_locked(dev);
	mutex_unlock(&dev->lock);
	return pending > UINT_MAX ? UINT_MAX : pending;
}

static int sensorium_runtime_uart_tiocmget(struct tty_struct *tty)
{
	struct sensorium_runtime_device *dev = sensorium_runtime_uart_device_from_tty(tty);
	int value;

	if (!dev)
		return -ENODEV;
	mutex_lock(&dev->lock);
	value = dev->u.uart.modem_inputs | dev->u.uart.modem_outputs;
	mutex_unlock(&dev->lock);
	return value;
}

static int sensorium_runtime_uart_tiocmset(struct tty_struct *tty,
					   unsigned int set,
					   unsigned int clear)
{
	struct sensorium_runtime_device *dev = sensorium_runtime_uart_device_from_tty(tty);
	unsigned int outputs;
	unsigned int mask = set | clear;
	int ret;

	if (!dev)
		return -ENODEV;
	mutex_lock(&dev->lock);
	outputs = dev->u.uart.modem_outputs;
	outputs |= set;
	outputs &= ~clear;
	mutex_unlock(&dev->lock);

	ret = sensorium_runtime_uart_send_control(dev, mask, outputs & mask);
	if (ret)
		return ret;

	mutex_lock(&dev->lock);
	dev->u.uart.modem_outputs = (dev->u.uart.modem_outputs & ~mask) |
				    (outputs & mask);
	mutex_unlock(&dev->lock);
	return 0;
}

static void sensorium_runtime_uart_set_termios(struct tty_struct *tty,
					       const struct ktermios *old_termios)
{
	struct sensorium_runtime_device *dev = sensorium_runtime_uart_device_from_tty(tty);

	if (!dev)
		return;

	mutex_lock(&dev->lock);
	sensorium_runtime_uart_capture_termios_locked(dev, &tty->termios,
						      tty_termios_baud_rate(&tty->termios));
	mutex_unlock(&dev->lock);
	sensorium_runtime_uart_push_config(dev);
}

static void sensorium_runtime_uart_throttle(struct tty_struct *tty)
{
	struct sensorium_runtime_device *dev = sensorium_runtime_uart_device_from_tty(tty);

	if (!dev)
		return;
	mutex_lock(&dev->lock);
	dev->u.uart.throttled = true;
	mutex_unlock(&dev->lock);
}

static void sensorium_runtime_uart_unthrottle(struct tty_struct *tty)
{
	struct sensorium_runtime_device *dev = sensorium_runtime_uart_device_from_tty(tty);

	if (!dev)
		return;
	mutex_lock(&dev->lock);
	dev->u.uart.throttled = false;
	sensorium_runtime_uart_inject_locked(dev, NULL, 0);
	mutex_unlock(&dev->lock);
}

static void sensorium_runtime_uart_flush_buffer(struct tty_struct *tty)
{
	struct sensorium_runtime_device *dev = sensorium_runtime_uart_device_from_tty(tty);

	if (!dev)
		return;

	cancel_delayed_work_sync(&dev->u.uart.tx_work);
	mutex_lock(&dev->lock);
	sensorium_runtime_uart_tx_reset_locked(dev);
	mutex_unlock(&dev->lock);
	sensorium_runtime_uart_wakeup_writers(dev);
}

static void sensorium_runtime_uart_wait_until_sent(struct tty_struct *tty, int timeout)
{
	struct sensorium_runtime_device *dev = sensorium_runtime_uart_device_from_tty(tty);

	if (!dev)
		return;

	if (timeout <= 0)
		wait_event_interruptible(dev->u.uart.tx_waitq,
					 !sensorium_runtime_uart_chars_in_buffer(tty));
	else
		wait_event_interruptible_timeout(dev->u.uart.tx_waitq,
						 !sensorium_runtime_uart_chars_in_buffer(tty),
						 timeout);
}

static void sensorium_runtime_uart_hangup(struct tty_struct *tty)
{
	struct sensorium_runtime_device *dev = sensorium_runtime_uart_device_from_tty(tty);

	if (!dev)
		return;

	cancel_delayed_work_sync(&dev->u.uart.tx_work);
	mutex_lock(&dev->lock);
	sensorium_runtime_uart_mark_disconnected_locked(dev, -EPIPE);
	mutex_unlock(&dev->lock);
	sensorium_runtime_uart_wakeup_writers(dev);
}

const struct tty_operations sensorium_runtime_uart_tty_ops = {
	.open = sensorium_runtime_uart_open,
	.close = sensorium_runtime_uart_close,
	.write = sensorium_runtime_uart_write,
	.write_room = sensorium_runtime_uart_write_room,
	.chars_in_buffer = sensorium_runtime_uart_chars_in_buffer,
	.tiocmget = sensorium_runtime_uart_tiocmget,
	.tiocmset = sensorium_runtime_uart_tiocmset,
	.set_termios = sensorium_runtime_uart_set_termios,
	.flush_buffer = sensorium_runtime_uart_flush_buffer,
	.wait_until_sent = sensorium_runtime_uart_wait_until_sent,
	.hangup = sensorium_runtime_uart_hangup,
	.throttle = sensorium_runtime_uart_throttle,
	.unthrottle = sensorium_runtime_uart_unthrottle,
};

const struct tty_port_operations sensorium_runtime_uart_port_ops = {
	.activate = sensorium_runtime_uart_port_activate,
	.shutdown = sensorium_runtime_uart_port_shutdown,
};
