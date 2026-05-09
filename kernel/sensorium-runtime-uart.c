#include "sensorium-runtime-uart-internal.h"

static struct sensorium_runtime_uart_group *
sensorium_runtime_uart_group_get_locked(struct sensorium_runtime_state *runtime,
					const char *base_name)
{
	struct sensorium_runtime_uart_group *group;
	struct tty_driver *driver;
	unsigned int num_ports;
	int ret;

	group = sensorium_runtime_find_uart_group_locked(runtime, base_name);
	if (group) {
		group->refs++;
		return group;
	}

	group = kzalloc(sizeof(*group), GFP_KERNEL);
	if (!group)
		return ERR_PTR(-ENOMEM);

	num_ports = sensorium_runtime_uart_port_limit();
	group->ports = kcalloc(num_ports, sizeof(*group->ports), GFP_KERNEL);
	if (!group->ports) {
		kfree(group);
		return ERR_PTR(-ENOMEM);
	}

	driver = tty_alloc_driver(num_ports,
				  TTY_DRIVER_REAL_RAW | TTY_DRIVER_DYNAMIC_DEV);
	if (IS_ERR(driver)) {
		kfree(group->ports);
		kfree(group);
		return ERR_CAST(driver);
	}

	group->runtime = runtime;
	group->num_ports = num_ports;
	strscpy(group->base_name, base_name, sizeof(group->base_name));
	snprintf(group->driver_name, sizeof(group->driver_name),
		 SENSORIUM_DRIVER_NAME "-runtime-%s", base_name);
	driver->driver_name = group->driver_name;
	driver->name = group->base_name;
	driver->major = 0;
	driver->minor_start = 0;
	driver->type = TTY_DRIVER_TYPE_SERIAL;
	driver->subtype = SERIAL_TYPE_NORMAL;
	driver->flags = TTY_DRIVER_REAL_RAW | TTY_DRIVER_DYNAMIC_DEV;
	driver->init_termios = tty_std_termios;
	driver->init_termios.c_cflag = B115200 | CS8 | CREAD | CLOCAL;
	driver->driver_state = group;
	tty_set_operations(driver, &sensorium_runtime_uart_tty_ops);

	ret = tty_register_driver(driver);
	if (ret) {
		tty_driver_kref_put(driver);
		kfree(group->ports);
		kfree(group);
		return ERR_PTR(ret);
	}

	group->driver = driver;
	group->refs = 1;
	list_add_tail(&group->list, &runtime->uart_groups);
	return group;
}

static void
sensorium_runtime_uart_group_put_locked(struct sensorium_runtime_uart_group *group)
{
	if (!group)
		return;

	if (group->refs)
		group->refs--;
	if (group->refs)
		return;

	list_del(&group->list);
	tty_unregister_driver(group->driver);
	tty_driver_kref_put(group->driver);
	kfree(group->ports);
	kfree(group);
}

int sensorium_runtime_register_uart(struct sensorium_runtime_device *dev)
{
	struct sensorium_runtime_uart_group *group;
	struct device *tty_dev;
	unsigned int tx_capacity;
	unsigned int rx_capacity;
	int ret;

	ret = sensorium_runtime_parse_tty_name(dev->name, dev->u.uart.base_name,
					       sizeof(dev->u.uart.base_name),
					       &dev->u.uart.index);
	if (ret)
		return ret;
	if (dev->location != dev->u.uart.index)
		return -EINVAL;

	tty_port_init(&dev->u.uart.port);
	dev->u.uart.port.ops = &sensorium_runtime_uart_port_ops;
	init_waitqueue_head(&dev->u.uart.tx_waitq);
	INIT_DELAYED_WORK(&dev->u.uart.tx_work, sensorium_runtime_uart_tx_work);
	tx_capacity = sensorium_runtime_uart_queue_capacity(
		sensorium_runtime_uart_tx_capacity);
	rx_capacity = sensorium_runtime_uart_queue_capacity(
		sensorium_runtime_uart_rx_capacity);
	dev->u.uart.tx_queue = kmalloc(tx_capacity, GFP_KERNEL);
	dev->u.uart.rx_queue = kmalloc(rx_capacity, GFP_KERNEL);
	if (!dev->u.uart.tx_queue || !dev->u.uart.rx_queue) {
		kfree(dev->u.uart.tx_queue);
		kfree(dev->u.uart.rx_queue);
		dev->u.uart.tx_queue = NULL;
		dev->u.uart.rx_queue = NULL;
		tty_port_destroy(&dev->u.uart.port);
		return -ENOMEM;
	}
	dev->u.uart.tx_capacity = tx_capacity;
	dev->u.uart.tx_head = 0;
	dev->u.uart.tx_tail = 0;
	dev->u.uart.tx_count = 0;
	dev->u.uart.tx_inflight = 0;
	dev->u.uart.rx_capacity = rx_capacity;
	dev->u.uart.rx_head = 0;
	dev->u.uart.rx_tail = 0;
	dev->u.uart.rx_count = 0;
	dev->u.uart.throttled = false;
	dev->u.uart.disconnected = false;
	dev->u.uart.last_status = 0;

	mutex_lock(&dev->runtime->uart_lock);
	group = sensorium_runtime_uart_group_get_locked(dev->runtime,
							dev->u.uart.base_name);
	if (IS_ERR(group)) {
		ret = PTR_ERR(group);
		mutex_unlock(&dev->runtime->uart_lock);
		kfree(dev->u.uart.tx_queue);
		kfree(dev->u.uart.rx_queue);
		dev->u.uart.tx_queue = NULL;
		dev->u.uart.rx_queue = NULL;
		tty_port_destroy(&dev->u.uart.port);
		return ret;
	}
	if (dev->u.uart.index >= group->num_ports) {
		sensorium_runtime_uart_group_put_locked(group);
		mutex_unlock(&dev->runtime->uart_lock);
		kfree(dev->u.uart.tx_queue);
		kfree(dev->u.uart.rx_queue);
		dev->u.uart.tx_queue = NULL;
		dev->u.uart.rx_queue = NULL;
		tty_port_destroy(&dev->u.uart.port);
		return -ERANGE;
	}
	if (group->ports[dev->u.uart.index]) {
		sensorium_runtime_uart_group_put_locked(group);
		mutex_unlock(&dev->runtime->uart_lock);
		kfree(dev->u.uart.tx_queue);
		kfree(dev->u.uart.rx_queue);
		dev->u.uart.tx_queue = NULL;
		dev->u.uart.rx_queue = NULL;
		tty_port_destroy(&dev->u.uart.port);
		return -EEXIST;
	}
	group->ports[dev->u.uart.index] = dev;
	dev->u.uart.group = group;
	mutex_unlock(&dev->runtime->uart_lock);

	tty_dev = tty_port_register_device(&dev->u.uart.port, group->driver,
					   dev->u.uart.index,
					   &dev->runtime->sim->pdev->dev);
	if (IS_ERR(tty_dev)) {
		ret = PTR_ERR(tty_dev);
		mutex_lock(&dev->runtime->uart_lock);
		if (group->ports[dev->u.uart.index] == dev)
			group->ports[dev->u.uart.index] = NULL;
		sensorium_runtime_uart_group_put_locked(group);
		mutex_unlock(&dev->runtime->uart_lock);
		kfree(dev->u.uart.tx_queue);
		kfree(dev->u.uart.rx_queue);
		dev->u.uart.tx_queue = NULL;
		dev->u.uart.rx_queue = NULL;
		tty_port_destroy(&dev->u.uart.port);
		return ret;
	}

	dev->u.uart.modem_inputs = TIOCM_CTS | TIOCM_DSR | TIOCM_CAR;
	dev->u.uart.baud_rate = 115200;
	dev->u.uart.cflag = group->driver->init_termios.c_cflag;
	dev->u.uart.iflag = group->driver->init_termios.c_iflag;
	dev->u.uart.oflag = group->driver->init_termios.c_oflag;
	dev->u.uart.lflag = group->driver->init_termios.c_lflag;
	dev->u.uart.registered = true;
	return 0;
}

void sensorium_runtime_destroy_uart_device(struct sensorium_runtime_device *dev)
{
	struct sensorium_runtime_uart_group *group = dev->u.uart.group;

	if (!dev->u.uart.registered)
		return;

	cancel_delayed_work_sync(&dev->u.uart.tx_work);
	mutex_lock(&dev->lock);
	sensorium_runtime_uart_tx_reset_locked(dev);
	mutex_unlock(&dev->lock);
	tty_unregister_device(group->driver, dev->u.uart.index);
	tty_port_destroy(&dev->u.uart.port);
	mutex_lock(&dev->runtime->uart_lock);
	if (group &&
	    dev->u.uart.index < group->num_ports &&
	    group->ports[dev->u.uart.index] == dev)
		group->ports[dev->u.uart.index] = NULL;
	sensorium_runtime_uart_group_put_locked(group);
	mutex_unlock(&dev->runtime->uart_lock);
	kfree(dev->u.uart.tx_queue);
	kfree(dev->u.uart.rx_queue);
	dev->u.uart.tx_queue = NULL;
	dev->u.uart.rx_queue = NULL;
	dev->u.uart.group = NULL;
	dev->u.uart.registered = false;
}
