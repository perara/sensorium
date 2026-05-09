#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/tty_flip.h>
#include "sensorium-transport-alias-internal.h"

static int sensorium_parse_tty_name(const char *name, char *base,
				    size_t base_size, unsigned int *index)
{
	size_t len;
	size_t split;
	int ret;

	if (!name || !*name)
		return -EINVAL;

	len = strlen(name);
	split = len;
	while (split > 0 && name[split - 1] >= '0' && name[split - 1] <= '9')
		split--;

	if (split == len || split == 0)
		return -EINVAL;
	if (split >= base_size)
		return -EINVAL;

	memcpy(base, name, split);
	base[split] = '\0';
	ret = kstrtouint(name + split, 10, index);
	return ret;
}

static int sensorium_uart_open(struct tty_struct *tty, struct file *file)
{
	struct sensorium_uart_alias *uart = tty->driver->driver_state;

	return tty_port_open(&uart->port, tty, file);
}

static void sensorium_uart_close(struct tty_struct *tty, struct file *file)
{
	struct sensorium_uart_alias *uart = tty->driver->driver_state;

	tty_port_close(&uart->port, tty, file);
}

static int sensorium_uart_port_activate(struct tty_port *port,
					struct tty_struct *tty)
{
	return 0;
}

static void sensorium_uart_port_shutdown(struct tty_port *port)
{
}

static ssize_t sensorium_uart_write(struct tty_struct *tty,
				    const u8 *buf, size_t count)
{
	struct sensorium_uart_alias *uart = tty->driver->driver_state;
	ssize_t written;

	written = tty_insert_flip_string(&uart->port, buf, count);
	if (written > 0)
		tty_flip_buffer_push(&uart->port);
	return written;
}

static unsigned int sensorium_uart_write_room(struct tty_struct *tty)
{
	return 65535;
}

static unsigned int sensorium_uart_chars_in_buffer(struct tty_struct *tty)
{
	return 0;
}

static const struct tty_operations sensorium_uart_tty_ops = {
	.open = sensorium_uart_open,
	.close = sensorium_uart_close,
	.write = sensorium_uart_write,
	.write_room = sensorium_uart_write_room,
	.chars_in_buffer = sensorium_uart_chars_in_buffer,
};

static const struct tty_port_operations sensorium_uart_port_ops = {
	.activate = sensorium_uart_port_activate,
	.shutdown = sensorium_uart_port_shutdown,
};

int sensorium_uart_alias_register(struct sensorium_device *sim)
{
	struct sensorium_uart_alias *uart = &sim->uart_alias;
	struct tty_driver *driver;
	struct device *tty_dev;
	unsigned int lines;
	int ret;

	ret = sensorium_parse_tty_name(sim->transport_device_name, uart->name,
				       sizeof(uart->name), &uart->index);
	if (ret) {
		pr_err("%s: invalid UART transport device name '%s' (expected tty-style suffix such as ttyAMA0)\n",
		       SENSORIUM_DRIVER_NAME, sim->transport_device_name);
		return ret;
	}

	lines = uart->index + 1;
	driver = tty_alloc_driver(lines, TTY_DRIVER_REAL_RAW |
				       TTY_DRIVER_DYNAMIC_DEV);
	if (IS_ERR(driver))
		return PTR_ERR(driver);

	tty_port_init(&uart->port);
	uart->port.ops = &sensorium_uart_port_ops;
	driver->driver_name = SENSORIUM_DRIVER_NAME "-uart";
	driver->name = uart->name;
	driver->major = 0;
	driver->minor_start = 0;
	driver->type = TTY_DRIVER_TYPE_SERIAL;
	driver->subtype = SERIAL_TYPE_NORMAL;
	driver->flags = TTY_DRIVER_REAL_RAW | TTY_DRIVER_DYNAMIC_DEV;
	driver->init_termios = tty_std_termios;
	driver->init_termios.c_cflag = B115200 | CS8 | CREAD | CLOCAL;
	driver->driver_state = uart;
	tty_set_operations(driver, &sensorium_uart_tty_ops);

	ret = tty_register_driver(driver);
	if (ret) {
		pr_err("%s: failed to register UART tty driver for /dev/%s: %d\n",
		       SENSORIUM_DRIVER_NAME, sim->transport_device_name, ret);
		tty_driver_kref_put(driver);
		tty_port_destroy(&uart->port);
		return ret;
	}

	tty_dev = tty_port_register_device(&uart->port, driver, uart->index,
					   &sim->pdev->dev);
	if (IS_ERR(tty_dev)) {
		pr_err("%s: failed to register UART device /dev/%s: %ld\n",
		       SENSORIUM_DRIVER_NAME, sim->transport_device_name,
		       PTR_ERR(tty_dev));
		tty_unregister_driver(driver);
		tty_driver_kref_put(driver);
		tty_port_destroy(&uart->port);
		return PTR_ERR(tty_dev);
	}

	uart->driver = driver;
	uart->registered = true;
	return 0;
}

void sensorium_uart_alias_unregister(struct sensorium_device *sim)
{
	struct sensorium_uart_alias *uart = &sim->uart_alias;

	if (!uart->registered)
		return;

	tty_unregister_device(uart->driver, uart->index);
	tty_unregister_driver(uart->driver);
	tty_port_destroy(&uart->port);
	tty_driver_kref_put(uart->driver);
	uart->driver = NULL;
	uart->registered = false;
}
