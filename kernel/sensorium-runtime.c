#include "sensorium-runtime-internal.h"

unsigned int sensorium_runtime_timeout_ms = 1000;
module_param_named(runtime_timeout_ms, sensorium_runtime_timeout_ms, uint, 0644);
MODULE_PARM_DESC(runtime_timeout_ms,
		 "Runtime bridge timeout in milliseconds for daemon-backed bus operations");

static unsigned int sensorium_runtime_uart_lines =
	SENSORIUM_RUNTIME_DEFAULT_UART_LINES;
module_param_named(runtime_uart_lines, sensorium_runtime_uart_lines, uint, 0644);
MODULE_PARM_DESC(runtime_uart_lines,
		 "Maximum tty lines per runtime UART family (1-4096)");

unsigned int sensorium_runtime_uart_tx_capacity =
	SENSORIUM_RUNTIME_DEFAULT_UART_TX_CAPACITY;
module_param_named(runtime_uart_tx_capacity, sensorium_runtime_uart_tx_capacity,
		   uint, 0644);
MODULE_PARM_DESC(runtime_uart_tx_capacity,
		 "UART TX queue capacity in bytes per runtime port (256-65536)");

unsigned int sensorium_runtime_uart_rx_capacity =
	SENSORIUM_RUNTIME_DEFAULT_UART_RX_CAPACITY;
module_param_named(runtime_uart_rx_capacity, sensorium_runtime_uart_rx_capacity,
		   uint, 0644);
MODULE_PARM_DESC(runtime_uart_rx_capacity,
		 "UART RX queue capacity in bytes per runtime port (256-65536)");

unsigned int sensorium_runtime_uart_port_limit(void)
{
	return clamp_val(sensorium_runtime_uart_lines, 1U,
			 SENSORIUM_RUNTIME_MAX_UART_LINE_LIMIT);
}

unsigned int sensorium_runtime_uart_queue_capacity(unsigned int configured)
{
	return clamp_val(configured, 256U,
			 SENSORIUM_RUNTIME_MAX_UART_QUEUE_CAPACITY);
}

static struct sensorium_runtime_v5_desc *
sensorium_runtime_control_ring(struct sensorium_runtime_state *runtime)
{
	return (struct sensorium_runtime_v5_desc *)
		((u8 *)runtime->shared_area + runtime->control_ring_offset);
}

static struct sensorium_runtime_v5_desc *
sensorium_runtime_reply_ring(struct sensorium_runtime_state *runtime)
{
	return (struct sensorium_runtime_v5_desc *)
		((u8 *)runtime->shared_area + runtime->reply_ring_offset);
}

static void *sensorium_runtime_payload_ptr(struct sensorium_runtime_state *runtime,
					   u32 base_offset, u32 zone_size,
					   u32 absolute_offset)
{
	return (u8 *)runtime->shared_area + base_offset +
	       (absolute_offset % zone_size);
}

static void sensorium_runtime_reset_generation_counters_locked(
	struct sensorium_runtime_state *runtime)
{
	if (!runtime->control_page)
		return;

	runtime->control_page->ebusy_generation = 0;
	runtime->control_page->request_timeout_generation = 0;
}

static void sensorium_runtime_v5_init_shared_locked(
	struct sensorium_runtime_state *runtime)
{
	struct sensorium_runtime_v5_control *ctrl = runtime->control_page;

	if (!ctrl)
		return;

	memset(ctrl, 0, sizeof(*ctrl));
	ctrl->magic = SENSORIUM_RUNTIME_MAGIC;
	ctrl->abi_version = SENSORIUM_RUNTIME_VERSION;
	ctrl->session_id = runtime->session_id;
	ctrl->generation = runtime->generation;
	ctrl->flags = runtime->v5_started ? 1U : 0U;
	ctrl->features = SENSORIUM_RUNTIME_REQUIRED_FEATURES;
	ctrl->control_ring_entries =
		(runtime->transport_ring_offset - runtime->control_ring_offset) /
		sizeof(struct sensorium_runtime_v5_desc);
	ctrl->transport_ring_entries =
		(runtime->reply_ring_offset - runtime->transport_ring_offset) /
		sizeof(struct sensorium_runtime_v5_desc);
	ctrl->reply_ring_entries =
		(runtime->control_payload_offset - runtime->reply_ring_offset) /
		sizeof(struct sensorium_runtime_v5_desc);
	ctrl->control_payload_size = runtime->control_payload_size;
	ctrl->transport_payload_size = runtime->transport_payload_size;
	ctrl->reply_payload_size = runtime->reply_payload_size;
	ctrl->inflight_credit_limit = runtime->inflight_credit_limit;
	ctrl->broker_eventfd_registered = runtime->broker_eventfd ? 1U : 0U;
	ctrl->kernel_eventfd_registered = runtime->kernel_eventfd ? 1U : 0U;
}

static void sensorium_runtime_v5_teardown_locked(struct sensorium_runtime_state *runtime)
{
	if (runtime->broker_eventfd) {
		eventfd_ctx_put(runtime->broker_eventfd);
		runtime->broker_eventfd = NULL;
	}
	if (runtime->kernel_eventfd) {
		eventfd_ctx_put(runtime->kernel_eventfd);
		runtime->kernel_eventfd = NULL;
	}
	if (runtime->shared_area) {
		vfree(runtime->shared_area);
		runtime->shared_area = NULL;
	}
	runtime->control_page = NULL;
	runtime->shared_area_len = 0;
	runtime->control_ring_offset = 0;
	runtime->transport_ring_offset = 0;
	runtime->reply_ring_offset = 0;
	runtime->control_payload_offset = 0;
	runtime->control_payload_size = 0;
	runtime->transport_payload_offset = 0;
	runtime->transport_payload_size = 0;
	runtime->reply_payload_offset = 0;
	runtime->reply_payload_size = 0;
	runtime->v5_configured = false;
	runtime->v5_started = false;
}

static void sensorium_runtime_reset_locked(struct sensorium_runtime_state *runtime)
{
	struct sensorium_runtime_device *dev;
	struct sensorium_runtime_device *dev_tmp;
	struct sensorium_runtime_bus *bus;
	struct sensorium_runtime_bus *bus_tmp;

	sensorium_runtime_fail_all_locked(runtime, -EPIPE);

	list_for_each_entry_safe(dev, dev_tmp, &runtime->devices, list)
		sensorium_runtime_destroy_device(dev);

	list_for_each_entry_safe(bus, bus_tmp, &runtime->buses, list)
		sensorium_runtime_destroy_bus(bus);
}

static bool sensorium_runtime_validate_ring_entries(u32 entries)
{
	return entries >= SENSORIUM_RUNTIME_V5_MIN_RING_ENTRIES &&
	       entries <= SENSORIUM_RUNTIME_V5_MAX_RING_ENTRIES;
}

static bool sensorium_runtime_validate_payload_arena(u32 payload_arena_size)
{
	return payload_arena_size >= SENSORIUM_RUNTIME_V5_MIN_PAYLOAD_ARENA &&
	       payload_arena_size <= SENSORIUM_RUNTIME_V5_MAX_PAYLOAD_ARENA;
}

static int sensorium_runtime_cmd_bus_add(struct sensorium_runtime_state *runtime,
					 const struct sensorium_runtime_bus_cmd *cmd)
{
	struct sensorium_runtime_bus *bus;
	int ret = 0;

	if (cmd->transport > SENSORIUM_TRANSPORT_UART)
		return -EINVAL;

	mutex_lock(&runtime->lock);
	if (sensorium_runtime_find_bus_locked(runtime, cmd->handle)) {
		mutex_unlock(&runtime->lock);
		return -EEXIST;
	}
	mutex_unlock(&runtime->lock);

	bus = kzalloc(sizeof(*bus), GFP_KERNEL);
	if (!bus)
		return -ENOMEM;

	INIT_LIST_HEAD(&bus->list);
	xa_init(&bus->location_index);
	bus->runtime = runtime;
	bus->handle = cmd->handle;
	bus->transport = cmd->transport;
	strscpy(bus->name, cmd->name, sizeof(bus->name));

	if (bus->transport == SENSORIUM_TRANSPORT_I2C) {
		ret = sensorium_runtime_i2c_register_bus(bus);
		if (ret)
			goto err_free_bus;
		bus->u.i2c.adapter.algo = &sensorium_runtime_i2c_algorithm;
		ret = i2c_add_numbered_adapter(&bus->u.i2c.adapter);
		if (ret)
			goto err_free_bus;
		bus->u.i2c.registered = true;
	} else if (bus->transport == SENSORIUM_TRANSPORT_SPI) {
		ret = sensorium_runtime_spi_register_bus(bus);
		if (ret)
			goto err_free_bus;
		bus->u.spi.registered = true;
	}

	mutex_lock(&runtime->lock);
	ret = xa_insert(&runtime->bus_index, cmd->handle, bus, GFP_KERNEL);
	if (ret) {
		mutex_unlock(&runtime->lock);
		goto err_destroy_bus;
	}
	list_add_tail(&bus->list, &runtime->buses);
	mutex_unlock(&runtime->lock);
	return 0;

err_destroy_bus:
	if (bus->transport == SENSORIUM_TRANSPORT_I2C && bus->u.i2c.registered) {
		i2c_del_adapter(&bus->u.i2c.adapter);
		bus->u.i2c.registered = false;
	} else if (bus->transport == SENSORIUM_TRANSPORT_SPI &&
		   bus->u.spi.registered) {
		spi_unregister_controller(bus->u.spi.ctlr);
		bus->u.spi.ctlr = NULL;
		bus->u.spi.registered = false;
	}
err_free_bus:
	xa_destroy(&bus->location_index);
	kfree(bus);
	return ret;
}

static int sensorium_runtime_cmd_bus_remove(struct sensorium_runtime_state *runtime, u32 handle)
{
	struct sensorium_runtime_bus *bus;
	struct sensorium_runtime_device *dev;
	struct sensorium_runtime_device *dev_tmp;

	mutex_lock(&runtime->lock);
	bus = sensorium_runtime_find_bus_locked(runtime, handle);
	if (!bus) {
		mutex_unlock(&runtime->lock);
		return -ENOENT;
	}

	list_for_each_entry_safe(dev, dev_tmp, &runtime->devices, list) {
		if (dev->bus != bus)
			continue;
		sensorium_runtime_destroy_device(dev);
	}
	sensorium_runtime_destroy_bus(bus);
	mutex_unlock(&runtime->lock);
	return 0;
}

static int sensorium_runtime_cmd_device_add(struct sensorium_runtime_state *runtime,
					    const struct sensorium_runtime_device_cmd *cmd)
{
	struct sensorium_runtime_device *dev;
	struct sensorium_runtime_bus *bus;
	int ret = 0;

	if (cmd->transport <= SENSORIUM_TRANSPORT_VIRTUAL ||
	    cmd->transport > SENSORIUM_TRANSPORT_UART)
		return -EINVAL;

	mutex_lock(&runtime->lock);
	if (sensorium_runtime_find_device_locked(runtime, cmd->handle)) {
		mutex_unlock(&runtime->lock);
		return -EEXIST;
	}
	bus = sensorium_runtime_find_bus_locked(runtime, cmd->bus_handle);
	if (!bus || bus->transport != cmd->transport) {
		mutex_unlock(&runtime->lock);
		return -ENOENT;
	}
	if ((cmd->transport == SENSORIUM_TRANSPORT_I2C ||
	     cmd->transport == SENSORIUM_TRANSPORT_SPI) &&
	    xa_load(&bus->location_index, cmd->location)) {
		mutex_unlock(&runtime->lock);
		return -EEXIST;
	}
	if (cmd->transport == SENSORIUM_TRANSPORT_UART &&
	    sensorium_runtime_find_uart_device_name_locked(runtime, cmd->name)) {
		mutex_unlock(&runtime->lock);
		return -EEXIST;
	}
	mutex_unlock(&runtime->lock);

	dev = kzalloc(sizeof(*dev), GFP_KERNEL);
	if (!dev)
		return -ENOMEM;

	INIT_LIST_HEAD(&dev->list);
	dev->runtime = runtime;
	dev->bus = bus;
	dev->handle = cmd->handle;
	dev->transport = cmd->transport;
	dev->location = cmd->location;
	strscpy(dev->name, cmd->name, sizeof(dev->name));
	mutex_init(&dev->lock);
	if (dev->transport == SENSORIUM_TRANSPORT_SPI) {
		if (!cmd->max_speed_hz || !cmd->spi_bits_per_word ||
		    cmd->spi_bits_per_word > 32 || cmd->spi_mode > 3) {
			kfree(dev);
			return -EINVAL;
		}
		dev->u.spi.max_speed_hz = cmd->max_speed_hz;
		dev->u.spi.mode = cmd->spi_mode;
		dev->u.spi.bits_per_word = cmd->spi_bits_per_word;
	}

	switch (dev->transport) {
	case SENSORIUM_TRANSPORT_SPI:
		ret = sensorium_runtime_register_spi(dev);
		break;
	case SENSORIUM_TRANSPORT_UART:
		ret = sensorium_runtime_register_uart(dev);
		break;
	default:
		break;
	}
	if (ret)
		goto err_free_dev;

	mutex_lock(&runtime->lock);
	ret = xa_insert(&runtime->device_index, cmd->handle, dev, GFP_KERNEL);
	if (ret) {
		mutex_unlock(&runtime->lock);
		goto err_unregister_dev;
	}
	if (dev->bus &&
	    (dev->transport == SENSORIUM_TRANSPORT_I2C ||
	     dev->transport == SENSORIUM_TRANSPORT_SPI)) {
		ret = xa_insert(&dev->bus->location_index, dev->location, dev,
				GFP_KERNEL);
		if (ret) {
			xa_erase(&runtime->device_index, cmd->handle);
			mutex_unlock(&runtime->lock);
			goto err_unregister_dev;
		}
	}
	list_add_tail(&dev->list, &runtime->devices);
	mutex_unlock(&runtime->lock);
	return 0;

err_unregister_dev:
	switch (dev->transport) {
	case SENSORIUM_TRANSPORT_SPI:
		sensorium_runtime_destroy_spi_device(dev);
		break;
	case SENSORIUM_TRANSPORT_UART:
		sensorium_runtime_destroy_uart_device(dev);
		break;
	default:
		break;
	}
err_free_dev:
	kfree(dev);
	return ret;
}

static int sensorium_runtime_cmd_device_remove(struct sensorium_runtime_state *runtime,
					       u32 handle)
{
	struct sensorium_runtime_device *dev;

	mutex_lock(&runtime->lock);
	dev = sensorium_runtime_find_device_locked(runtime, handle);
	if (!dev) {
		mutex_unlock(&runtime->lock);
		return -ENOENT;
	}

	sensorium_runtime_destroy_device(dev);
	mutex_unlock(&runtime->lock);
	return 0;
}

static int sensorium_runtime_cmd_uart_inject(struct sensorium_runtime_state *runtime,
					     const void *payload, size_t payload_len)
{
	const struct sensorium_runtime_uart_rx_cmd *cmd = payload;
	struct sensorium_runtime_device *dev;

	if (payload_len < sizeof(*cmd))
		return -EINVAL;
	if (sizeof(*cmd) + cmd->len > payload_len)
		return -EINVAL;

	mutex_lock(&runtime->lock);
	dev = sensorium_runtime_find_device_locked(runtime, cmd->handle);
	mutex_unlock(&runtime->lock);
	if (!dev || dev->transport != SENSORIUM_TRANSPORT_UART)
		return -ENOENT;

	mutex_lock(&dev->lock);
	sensorium_runtime_uart_inject_locked(dev, cmd->data, cmd->len);
	mutex_unlock(&dev->lock);
	return 0;
}

static int sensorium_runtime_cmd_uart_modem(struct sensorium_runtime_state *runtime,
					    const void *payload, size_t payload_len)
{
	const struct sensorium_runtime_uart_modem_cmd *cmd = payload;
	struct sensorium_runtime_device *dev;

	if (payload_len < sizeof(*cmd))
		return -EINVAL;

	mutex_lock(&runtime->lock);
	dev = sensorium_runtime_find_device_locked(runtime, cmd->handle);
	mutex_unlock(&runtime->lock);
	if (!dev || dev->transport != SENSORIUM_TRANSPORT_UART)
		return -ENOENT;

	mutex_lock(&dev->lock);
	dev->u.uart.modem_inputs = (dev->u.uart.modem_inputs & ~cmd->mask) |
				   (cmd->values & cmd->mask);
	if ((cmd->mask & TIOCM_CAR) && !(cmd->values & TIOCM_CAR))
		sensorium_runtime_uart_mark_disconnected_locked(dev, -EPIPE);
	else if (cmd->mask & TIOCM_CAR)
		sensorium_runtime_uart_recover_locked(dev);
	mutex_unlock(&dev->lock);
	sensorium_runtime_uart_wakeup_writers(dev);
	return 0;
}

static int sensorium_runtime_cmd_reply(struct sensorium_runtime_state *runtime, u32 id,
				       u32 generation, const void *payload,
				       size_t payload_len)
{
	const struct sensorium_runtime_reply_cmd *cmd = payload;
	struct sensorium_runtime_request *req;
	size_t copy_len = 0;

	if (payload_len < sizeof(*cmd))
		return -EINVAL;
	if (sizeof(*cmd) + cmd->data_len > payload_len)
		return -EINVAL;
	if (cmd->data_len > SENSORIUM_RUNTIME_MAX_PAYLOAD)
		return -EMSGSIZE;

	mutex_lock(&runtime->lock);
	req = xa_load(&runtime->request_index, id);
	if (!req || req->generation != generation) {
		mutex_unlock(&runtime->lock);
		return -ENOENT;
	}

	req->status = cmd->status;
	if (!cmd->status && req->response_buf && req->response_capacity) {
		copy_len = min_t(size_t, req->response_capacity, cmd->data_len);
		if (copy_len)
			memcpy(req->response_buf, cmd->data, copy_len);
	}
	req->actual_response_len = copy_len;
	req->replied = true;
	if (runtime->control_page) {
		if (runtime->control_page->inflight_in_use > 0)
			runtime->control_page->inflight_in_use--;
		runtime->control_page->request_completed_total++;
	}
	mutex_unlock(&runtime->lock);
	complete_all(&req->done);
	return 0;
}

static int sensorium_runtime_handle_control_message(struct sensorium_runtime_state *runtime,
						    u16 opcode, u32 request_id,
						    u32 generation,
						    const void *payload,
						    size_t payload_len)
{
	switch (opcode) {
	case SENSORIUM_RUNTIME_CMD_RESET:
		if (payload_len != 0)
			return -EINVAL;
		mutex_lock(&runtime->lock);
		sensorium_runtime_reset_locked(runtime);
		runtime->generation++;
		if (!runtime->generation)
			runtime->generation = 1;
		if (runtime->control_page) {
			runtime->control_page->generation = runtime->generation;
			sensorium_runtime_reset_generation_counters_locked(runtime);
		}
		mutex_unlock(&runtime->lock);
		return 0;
	case SENSORIUM_RUNTIME_CMD_BUS_ADD:
		if (payload_len != sizeof(struct sensorium_runtime_bus_cmd))
			return -EINVAL;
		return sensorium_runtime_cmd_bus_add(runtime, payload);
	case SENSORIUM_RUNTIME_CMD_BUS_REMOVE:
		if (payload_len != sizeof(u32))
			return -EINVAL;
		return sensorium_runtime_cmd_bus_remove(runtime, *(u32 *)payload);
	case SENSORIUM_RUNTIME_CMD_DEVICE_ADD:
		if (payload_len != sizeof(struct sensorium_runtime_device_cmd))
			return -EINVAL;
		return sensorium_runtime_cmd_device_add(runtime, payload);
	case SENSORIUM_RUNTIME_CMD_DEVICE_REMOVE:
		if (payload_len != sizeof(u32))
			return -EINVAL;
		return sensorium_runtime_cmd_device_remove(runtime, *(u32 *)payload);
	case SENSORIUM_RUNTIME_CMD_UART_INJECT_RX:
		return sensorium_runtime_cmd_uart_inject(runtime, payload, payload_len);
	case SENSORIUM_RUNTIME_CMD_UART_SET_MODEM:
		if (payload_len != sizeof(struct sensorium_runtime_uart_modem_cmd))
			return -EINVAL;
		return sensorium_runtime_cmd_uart_modem(runtime, payload, payload_len);
	case SENSORIUM_RUNTIME_CMD_REPLY:
		return sensorium_runtime_cmd_reply(runtime, request_id, generation,
						  payload, payload_len);
	default:
		return -EINVAL;
	}
}

static int sensorium_runtime_drain_control_ring(struct sensorium_runtime_state *runtime)
{
	struct sensorium_runtime_v5_control *ctrl = runtime->control_page;
	struct sensorium_runtime_v5_desc *ring;
	struct sensorium_runtime_v5_desc desc;
	void *payload;
	int ret = 0;

	if (!ctrl)
		return -EINVAL;

	ring = sensorium_runtime_control_ring(runtime);
	while (true) {
		mutex_lock(&runtime->lock);
		if (!runtime->daemon_open || !runtime->v5_started) {
			mutex_unlock(&runtime->lock);
			return -EPROTO;
		}
		if (ctrl->control_ring_head == ctrl->control_ring_tail) {
			mutex_unlock(&runtime->lock);
			break;
		}
		desc = ring[ctrl->control_ring_head % ctrl->control_ring_entries];
		if (desc.session_id != runtime->session_id) {
			ctrl->desynced = 1;
			mutex_unlock(&runtime->lock);
			return -EPROTO;
		}
		payload = sensorium_runtime_payload_ptr(runtime,
						       runtime->control_payload_offset,
						       runtime->control_payload_size,
						       desc.payload_offset);
		mutex_unlock(&runtime->lock);

		ret = sensorium_runtime_handle_control_message(runtime, desc.opcode,
							       desc.request_id,
							       desc.generation,
							       payload,
							       desc.payload_len);
		mutex_lock(&runtime->lock);
		ctrl->control_ring_head++;
		ctrl->control_payload_head = desc.payload_offset + desc.payload_len;
		mutex_unlock(&runtime->lock);
		if (ret)
			return ret;
	}

	return 0;
}

static int sensorium_runtime_drain_reply_ring(struct sensorium_runtime_state *runtime)
{
	struct sensorium_runtime_v5_control *ctrl = runtime->control_page;
	struct sensorium_runtime_v5_desc *ring;
	struct sensorium_runtime_v5_desc desc;
	void *payload;
	int ret = 0;

	if (!ctrl)
		return -EINVAL;

	ring = sensorium_runtime_reply_ring(runtime);
	while (true) {
		mutex_lock(&runtime->lock);
		if (!runtime->daemon_open || !runtime->v5_started) {
			mutex_unlock(&runtime->lock);
			return -EPROTO;
		}
		if (ctrl->reply_ring_head == ctrl->reply_ring_tail) {
			mutex_unlock(&runtime->lock);
			break;
		}
		desc = ring[ctrl->reply_ring_head % ctrl->reply_ring_entries];
		if (desc.session_id != runtime->session_id) {
			ctrl->desynced = 1;
			mutex_unlock(&runtime->lock);
			return -EPROTO;
		}
		payload = sensorium_runtime_payload_ptr(runtime,
						       runtime->reply_payload_offset,
						       runtime->reply_payload_size,
						       desc.payload_offset);
		mutex_unlock(&runtime->lock);

		ret = sensorium_runtime_handle_control_message(runtime,
							       SENSORIUM_RUNTIME_CMD_REPLY,
							       desc.request_id,
							       desc.generation,
							       payload,
							       desc.payload_len);
		mutex_lock(&runtime->lock);
		ctrl->reply_ring_head++;
		ctrl->reply_payload_head = desc.payload_offset + desc.payload_len;
		mutex_unlock(&runtime->lock);
		if (ret && ret != -ENOENT)
			return ret;
	}

	return 0;
}

static struct sensorium_runtime_state *
sensorium_runtime_file_to_state(struct file *file)
{
	struct miscdevice *misc = file->private_data;

	return container_of(misc, struct sensorium_runtime_state, bridge);
}

static int sensorium_runtime_bridge_open(struct inode *inode, struct file *file)
{
	struct sensorium_runtime_state *runtime = sensorium_runtime_file_to_state(file);
	int ret = 0;

	mutex_lock(&runtime->lock);
	if (runtime->daemon_open) {
		ret = -EBUSY;
	} else {
		runtime->daemon_open = true;
		runtime->session_id = ++runtime->next_session_id;
		runtime->v5_configured = false;
		runtime->v5_started = false;
	}
	mutex_unlock(&runtime->lock);
	return ret;
}

static int sensorium_runtime_bridge_release(struct inode *inode, struct file *file)
{
	struct sensorium_runtime_state *runtime = sensorium_runtime_file_to_state(file);
	struct sensorium_runtime_device *dev;

	mutex_lock(&runtime->lock);
	runtime->daemon_open = false;
	runtime->v5_started = false;
	sensorium_runtime_fail_all_locked(runtime, -EPIPE);
	list_for_each_entry(dev, &runtime->devices, list) {
		if (dev->transport != SENSORIUM_TRANSPORT_UART)
			continue;
		mutex_lock(&dev->lock);
		sensorium_runtime_uart_mark_disconnected_locked(dev, -EPIPE);
		mutex_unlock(&dev->lock);
		sensorium_runtime_uart_wakeup_writers(dev);
	}
	sensorium_runtime_v5_teardown_locked(runtime);
	mutex_unlock(&runtime->lock);
	wake_up_interruptible(&runtime->bridge_waitq);
	return 0;
}

static long sensorium_runtime_bridge_ioctl(struct file *file,
					   unsigned int cmd, unsigned long arg)
{
	struct sensorium_runtime_state *runtime = sensorium_runtime_file_to_state(file);
	struct sensorium_runtime_v5_setup setup;
	struct sensorium_runtime_v5_eventfds eventfds;
	u32 payload_arena_size;
	u32 control_payload_size;
	u32 reply_payload_size;
	u32 transport_payload_size;
	u32 offset;
	void *shared_area;
	int ret = 0;

	switch (cmd) {
	case SENSORIUM_RUNTIME_IOCTL_SETUP_V5:
		if (copy_from_user(&setup, (void __user *)arg, sizeof(setup)))
			return -EFAULT;
		if (setup.abi_version != SENSORIUM_RUNTIME_VERSION)
			return -EPROTO;
		if (!sensorium_runtime_validate_ring_entries(setup.control_ring_entries) ||
		    !sensorium_runtime_validate_ring_entries(setup.transport_ring_entries) ||
		    !sensorium_runtime_validate_ring_entries(setup.reply_ring_entries))
			return -EINVAL;
		if (!sensorium_runtime_validate_payload_arena(setup.payload_arena_size))
			return -EINVAL;

		payload_arena_size = setup.payload_arena_size;
		control_payload_size = payload_arena_size / 4U;
		reply_payload_size = payload_arena_size / 4U;
		transport_payload_size = payload_arena_size -
			control_payload_size - reply_payload_size;
		if (!control_payload_size || !transport_payload_size || !reply_payload_size)
			return -EINVAL;

		offset = sizeof(struct sensorium_runtime_v5_control);
		setup.control_page_offset = 0;
		setup.control_ring_offset = offset;
		offset += setup.control_ring_entries *
			sizeof(struct sensorium_runtime_v5_desc);
		setup.transport_ring_offset = offset;
		offset += setup.transport_ring_entries *
			sizeof(struct sensorium_runtime_v5_desc);
		setup.reply_ring_offset = offset;
		offset += setup.reply_ring_entries *
			sizeof(struct sensorium_runtime_v5_desc);
		setup.control_payload_offset = offset;
		setup.control_payload_size = control_payload_size;
		offset += control_payload_size;
		setup.transport_payload_offset = offset;
		setup.transport_payload_size = transport_payload_size;
		offset += transport_payload_size;
		setup.reply_payload_offset = offset;
		setup.reply_payload_size = reply_payload_size;
		offset += reply_payload_size;
		setup.region_size = PAGE_ALIGN(offset);
		setup.session_id = 0;
		setup.generation = 0;
		setup.features = SENSORIUM_RUNTIME_REQUIRED_FEATURES;

		shared_area = vmalloc_user(setup.region_size);
		if (!shared_area)
			return -ENOMEM;

		mutex_lock(&runtime->lock);
		if (!runtime->daemon_open) {
			mutex_unlock(&runtime->lock);
			vfree(shared_area);
			return -ENODEV;
		}
		sensorium_runtime_v5_teardown_locked(runtime);
		runtime->shared_area = shared_area;
		runtime->shared_area_len = setup.region_size;
		runtime->control_page = shared_area;
		runtime->control_ring_offset = setup.control_ring_offset;
		runtime->transport_ring_offset = setup.transport_ring_offset;
		runtime->reply_ring_offset = setup.reply_ring_offset;
		runtime->control_payload_offset = setup.control_payload_offset;
		runtime->control_payload_size = setup.control_payload_size;
		runtime->transport_payload_offset = setup.transport_payload_offset;
		runtime->transport_payload_size = setup.transport_payload_size;
		runtime->reply_payload_offset = setup.reply_payload_offset;
		runtime->reply_payload_size = setup.reply_payload_size;
		runtime->inflight_credit_limit = max_t(u32, 1U, setup.inflight_credit_limit);
		runtime->v5_configured = true;
		runtime->v5_started = false;
		sensorium_runtime_v5_init_shared_locked(runtime);
		setup.session_id = runtime->session_id;
		setup.generation = runtime->generation;
		mutex_unlock(&runtime->lock);

		if (copy_to_user((void __user *)arg, &setup, sizeof(setup)))
			return -EFAULT;
		return 0;

	case SENSORIUM_RUNTIME_IOCTL_REGISTER_EVENTFDS:
		if (copy_from_user(&eventfds, (void __user *)arg, sizeof(eventfds)))
			return -EFAULT;
		mutex_lock(&runtime->lock);
		if (!runtime->v5_configured) {
			mutex_unlock(&runtime->lock);
			return -EPROTO;
		}
		if (runtime->broker_eventfd) {
			eventfd_ctx_put(runtime->broker_eventfd);
			runtime->broker_eventfd = NULL;
		}
		if (runtime->kernel_eventfd) {
			eventfd_ctx_put(runtime->kernel_eventfd);
			runtime->kernel_eventfd = NULL;
		}
		if (eventfds.broker_eventfd >= 0) {
			runtime->broker_eventfd =
				eventfd_ctx_fdget(eventfds.broker_eventfd);
			if (IS_ERR(runtime->broker_eventfd)) {
				ret = PTR_ERR(runtime->broker_eventfd);
				runtime->broker_eventfd = NULL;
				mutex_unlock(&runtime->lock);
				return ret;
			}
		}
		if (eventfds.kernel_eventfd >= 0) {
			runtime->kernel_eventfd =
				eventfd_ctx_fdget(eventfds.kernel_eventfd);
			if (IS_ERR(runtime->kernel_eventfd)) {
				ret = PTR_ERR(runtime->kernel_eventfd);
				runtime->kernel_eventfd = NULL;
				if (runtime->broker_eventfd) {
					eventfd_ctx_put(runtime->broker_eventfd);
					runtime->broker_eventfd = NULL;
				}
				mutex_unlock(&runtime->lock);
				return ret;
			}
		}
		sensorium_runtime_v5_init_shared_locked(runtime);
		mutex_unlock(&runtime->lock);
		return 0;

	case SENSORIUM_RUNTIME_IOCTL_START_V5:
		mutex_lock(&runtime->lock);
		if (!runtime->daemon_open || !runtime->v5_configured) {
			mutex_unlock(&runtime->lock);
			return -EPROTO;
		}
		runtime->v5_started = true;
		sensorium_runtime_v5_init_shared_locked(runtime);
		mutex_unlock(&runtime->lock);
		return 0;

	case SENSORIUM_RUNTIME_IOCTL_SUBMIT_CONTROL:
		return sensorium_runtime_drain_control_ring(runtime);

	case SENSORIUM_RUNTIME_IOCTL_SUBMIT_REPLY:
		return sensorium_runtime_drain_reply_ring(runtime);

	default:
		return -ENOTTY;
	}
}

static int sensorium_runtime_bridge_mmap(struct file *file, struct vm_area_struct *vma)
{
	struct sensorium_runtime_state *runtime = sensorium_runtime_file_to_state(file);
	unsigned long requested = vma->vm_end - vma->vm_start;

	mutex_lock(&runtime->lock);
	if (!runtime->shared_area || !runtime->shared_area_len) {
		mutex_unlock(&runtime->lock);
		return -ENODEV;
	}
	if (requested > runtime->shared_area_len) {
		mutex_unlock(&runtime->lock);
		return -EINVAL;
	}
	mutex_unlock(&runtime->lock);

	return remap_vmalloc_range(vma, runtime->shared_area, 0);
}

static __poll_t sensorium_runtime_bridge_poll(struct file *file, poll_table *wait)
{
	struct sensorium_runtime_state *runtime = sensorium_runtime_file_to_state(file);
	__poll_t mask = 0;

	poll_wait(file, &runtime->bridge_waitq, wait);
	mutex_lock(&runtime->lock);
	if (runtime->control_page &&
	    runtime->control_page->transport_ring_head !=
		    runtime->control_page->transport_ring_tail)
		mask |= EPOLLIN | EPOLLRDNORM;
	mutex_unlock(&runtime->lock);
	return mask;
}

static const struct file_operations sensorium_runtime_bridge_fops = {
	.owner = THIS_MODULE,
	.open = sensorium_runtime_bridge_open,
	.release = sensorium_runtime_bridge_release,
	.unlocked_ioctl = sensorium_runtime_bridge_ioctl,
#ifdef CONFIG_COMPAT
	.compat_ioctl = sensorium_runtime_bridge_ioctl,
#endif
	.mmap = sensorium_runtime_bridge_mmap,
	.poll = sensorium_runtime_bridge_poll,
	.llseek = noop_llseek,
};

int sensorium_runtime_register(struct sensorium_device *sim)
{
	struct sensorium_runtime_state *runtime;
	int ret;

	runtime = kzalloc(sizeof(*runtime), GFP_KERNEL);
	if (!runtime)
		return -ENOMEM;

	runtime->sim = sim;
	mutex_init(&runtime->lock);
	mutex_init(&runtime->uart_lock);
	init_waitqueue_head(&runtime->bridge_waitq);
	INIT_LIST_HEAD(&runtime->buses);
	INIT_LIST_HEAD(&runtime->devices);
	INIT_LIST_HEAD(&runtime->uart_groups);
	INIT_LIST_HEAD(&runtime->requests);
	xa_init(&runtime->bus_index);
	xa_init(&runtime->device_index);
	xa_init(&runtime->request_index);
	runtime->inflight_credit_limit = SENSORIUM_RUNTIME_V5_DEFAULT_RING_ENTRIES;
	runtime->request_cache = kmem_cache_create("sensorium_runtime_request",
						   sizeof(struct sensorium_runtime_request),
						   0, SLAB_HWCACHE_ALIGN, NULL);
	if (!runtime->request_cache) {
		kfree(runtime);
		return -ENOMEM;
	}
	runtime->request_pool =
		mempool_create_slab_pool(SENSORIUM_RUNTIME_V5_DEFAULT_RING_ENTRIES,
					 runtime->request_cache);
	if (!runtime->request_pool) {
		kmem_cache_destroy(runtime->request_cache);
		kfree(runtime);
		return -ENOMEM;
	}

	runtime->bridge.minor = MISC_DYNAMIC_MINOR;
	runtime->bridge.name = SENSORIUM_RUNTIME_BRIDGE_NAME;
	runtime->bridge.fops = &sensorium_runtime_bridge_fops;
	runtime->bridge.parent = &sim->pdev->dev;
	runtime->bridge.mode = 0600;

	ret = misc_register(&runtime->bridge);
	if (ret) {
		mempool_destroy(runtime->request_pool);
		kmem_cache_destroy(runtime->request_cache);
		kfree(runtime);
		return ret;
	}

	sim->runtime = runtime;
	pr_info("%s: runtime bridge available at /dev/%s (ABI v%u)\n",
		SENSORIUM_DRIVER_NAME, SENSORIUM_RUNTIME_BRIDGE_NAME,
		SENSORIUM_RUNTIME_VERSION);
	return 0;
}

void sensorium_runtime_unregister(struct sensorium_device *sim)
{
	struct sensorium_runtime_state *runtime = sim->runtime;

	if (!runtime)
		return;

	mutex_lock(&runtime->lock);
	runtime->daemon_open = false;
	sensorium_runtime_fail_all_locked(runtime, -EPIPE);
	sensorium_runtime_reset_locked(runtime);
	sensorium_runtime_v5_teardown_locked(runtime);
	mutex_unlock(&runtime->lock);

	misc_deregister(&runtime->bridge);
	mempool_destroy(runtime->request_pool);
	kmem_cache_destroy(runtime->request_cache);
	xa_destroy(&runtime->request_index);
	xa_destroy(&runtime->device_index);
	xa_destroy(&runtime->bus_index);
	kfree(runtime);
	sim->runtime = NULL;
}
