#include "sensorium-runtime-internal.h"

static struct sensorium_runtime_v5_desc *
sensorium_runtime_transport_ring(struct sensorium_runtime_state *runtime)
{
	return (struct sensorium_runtime_v5_desc *)
		((u8 *)runtime->shared_area + runtime->transport_ring_offset);
}

static void *sensorium_runtime_payload_ptr(struct sensorium_runtime_state *runtime,
					   u32 base_offset, u32 zone_size,
					   u32 absolute_offset)
{
	return (u8 *)runtime->shared_area + base_offset +
	       (absolute_offset % zone_size);
}

static bool sensorium_runtime_reserve_payload(u32 head, u32 tail, u32 zone_size,
					      size_t payload_len, u32 *reserved_offset)
{
	u32 start = tail;
	u32 mod = start % zone_size;

	if (!payload_len) {
		*reserved_offset = start;
		return true;
	}

	if (mod + payload_len > zone_size)
		start += zone_size - mod;

	if ((u64)start + payload_len - head > zone_size)
		return false;

	*reserved_offset = start;
	return true;
}

static struct sensorium_runtime_request *
sensorium_runtime_alloc_request(struct sensorium_runtime_state *runtime,
				size_t payload_len)
{
	struct sensorium_runtime_request *req;

	req = mempool_alloc(runtime->request_pool, GFP_KERNEL);
	if (!req)
		return NULL;

	memset(req, 0, sizeof(*req));
	INIT_LIST_HEAD(&req->list);
	init_completion(&req->done);
	req->runtime = runtime;
	req->payload_len = payload_len;
	if (!payload_len) {
		req->payload = NULL;
		req->payload_inline = true;
		return req;
	}

	if (payload_len <= sizeof(req->inline_payload)) {
		req->payload = req->inline_payload;
		req->payload_inline = true;
		return req;
	}

	req->payload = kvmalloc(payload_len, GFP_KERNEL);
	if (!req->payload) {
		mempool_free(req, runtime->request_pool);
		return NULL;
	}
	req->payload_inline = false;
	return req;
}

int sensorium_runtime_parse_i2c_name(const char *name, unsigned int *index)
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

int sensorium_runtime_parse_spi_name(const char *name, unsigned int *index)
{
	size_t len;
	size_t split;

	if (!name || !*name)
		return -EINVAL;

	len = strlen(name);
	split = len;
	while (split > 0 && name[split - 1] >= '0' && name[split - 1] <= '9')
		split--;

	if (split == len || split == 0)
		return -EINVAL;

	return kstrtouint(name + split, 10, index);
}

int sensorium_runtime_parse_tty_name(const char *name, char *base,
				     size_t base_size,
				     unsigned int *index)
{
	size_t len;
	size_t split;

	if (!name || !*name)
		return -EINVAL;

	len = strlen(name);
	split = len;
	while (split > 0 && name[split - 1] >= '0' && name[split - 1] <= '9')
		split--;

	if (split == len || split == 0 || split >= base_size)
		return -EINVAL;

	memcpy(base, name, split);
	base[split] = '\0';
	return kstrtouint(name + split, 10, index);
}

struct sensorium_runtime_uart_group *
sensorium_runtime_find_uart_group_locked(struct sensorium_runtime_state *runtime,
					 const char *base_name)
{
	struct sensorium_runtime_uart_group *group;

	list_for_each_entry(group, &runtime->uart_groups, list) {
		if (!strcmp(group->base_name, base_name))
			return group;
	}

	return NULL;
}

struct sensorium_runtime_device *
sensorium_runtime_find_uart_device_name_locked(struct sensorium_runtime_state *runtime,
					       const char *name)
{
	struct sensorium_runtime_device *dev;

	list_for_each_entry(dev, &runtime->devices, list) {
		if (dev->transport != SENSORIUM_TRANSPORT_UART)
			continue;
		if (!strcmp(dev->name, name))
			return dev;
	}

	return NULL;
}

struct sensorium_runtime_bus *
sensorium_runtime_find_bus_locked(struct sensorium_runtime_state *runtime, u32 handle)
{
	return xa_load(&runtime->bus_index, handle);
}

struct sensorium_runtime_device *
sensorium_runtime_find_device_locked(struct sensorium_runtime_state *runtime, u32 handle)
{
	return xa_load(&runtime->device_index, handle);
}

struct sensorium_runtime_device *
sensorium_runtime_find_i2c_device_locked(struct sensorium_runtime_bus *bus, u16 addr)
{
	return xa_load(&bus->location_index, addr);
}

struct sensorium_runtime_device *
sensorium_runtime_find_spi_device_locked(struct sensorium_runtime_bus *bus,
					 u16 chip_select)
{
	return xa_load(&bus->location_index, chip_select);
}

void sensorium_runtime_free_request(struct sensorium_runtime_request *req)
{
	if (!req)
		return;

	if (!req->payload_inline)
		kvfree(req->payload);
	mempool_free(req, req->runtime->request_pool);
}

void sensorium_runtime_fail_all_locked(struct sensorium_runtime_state *runtime,
				       int status)
{
	struct sensorium_runtime_request *req;

	list_for_each_entry(req, &runtime->requests, list) {
		if (req->replied)
			continue;
		req->status = status;
		req->actual_response_len = 0;
		req->replied = true;
		complete_all(&req->done);
	}

	if (runtime->control_page)
		runtime->control_page->inflight_in_use = 0;
}

static int sensorium_runtime_queue_transport_locked(struct sensorium_runtime_state *runtime,
						    struct sensorium_runtime_request *req)
{
	struct sensorium_runtime_v5_control *ctrl = runtime->control_page;
	struct sensorium_runtime_v5_desc *ring;
	struct sensorium_runtime_v5_desc *desc;
	u32 payload_offset;
	u32 ring_tail;

	if (!runtime->daemon_open || !runtime->v5_started || !ctrl)
		return -ETIMEDOUT;

	if (ctrl->inflight_in_use >= runtime->inflight_credit_limit)
		goto busy;

	if (ctrl->transport_ring_tail - ctrl->transport_ring_head >=
	    ctrl->transport_ring_entries)
		goto busy;

	if (!sensorium_runtime_reserve_payload(ctrl->transport_payload_head,
					       ctrl->transport_payload_tail,
					       runtime->transport_payload_size,
					       req->payload_len, &payload_offset))
		goto busy;

	if (req->payload_len)
		memcpy(sensorium_runtime_payload_ptr(runtime,
						      runtime->transport_payload_offset,
						      runtime->transport_payload_size,
						      payload_offset),
		       req->payload, req->payload_len);

	ring = sensorium_runtime_transport_ring(runtime);
	ring_tail = ctrl->transport_ring_tail;
	desc = &ring[ring_tail % ctrl->transport_ring_entries];
	desc->session_id = runtime->session_id;
	desc->generation = req->generation;
	desc->request_id = req->id;
	desc->queue_class = SENSORIUM_RUNTIME_QUEUE_CLASS_TRANSPORT;
	desc->opcode = req->type;
	desc->device_handle = 0;
	desc->payload_offset = payload_offset;
	desc->payload_len = req->payload_len;
	desc->status = 0;
	desc->reserved = 0;
	smp_wmb();
	ctrl->transport_payload_tail = payload_offset + req->payload_len;
	ctrl->transport_ring_tail = ring_tail + 1;
	ctrl->inflight_in_use++;
	if (runtime->broker_eventfd)
		eventfd_signal(runtime->broker_eventfd);
	wake_up_interruptible(&runtime->bridge_waitq);
	return 0;

busy:
	ctrl->ebusy_generation++;
	ctrl->ebusy_total++;
	return -EBUSY;
}

int sensorium_runtime_send_request(struct sensorium_runtime_state *runtime,
				   u16 type, const void *payload,
				   size_t payload_len,
				   void *response, size_t *response_len)
{
	struct sensorium_runtime_request *req;
	unsigned long timeout;
	size_t response_capacity = 0;
	int status;

	if (payload_len > SENSORIUM_RUNTIME_MAX_PAYLOAD)
		return -EMSGSIZE;

	req = sensorium_runtime_alloc_request(runtime, payload_len);
	if (!req)
		return -ENOMEM;

	req->type = type;
	req->generation = runtime->generation;
	req->response_buf = response;
	if (response && response_len)
		response_capacity = *response_len;
	req->response_capacity = response_capacity;
	req->actual_response_len = 0;
	if (payload_len)
		memcpy(req->payload, payload, payload_len);

	mutex_lock(&runtime->lock);
	req->id = ++runtime->next_request_id;
	req->listed = true;
	list_add_tail(&req->list, &runtime->requests);
	if (xa_err(xa_store(&runtime->request_index, req->id, req, GFP_KERNEL))) {
		list_del(&req->list);
		req->listed = false;
		mutex_unlock(&runtime->lock);
		sensorium_runtime_free_request(req);
		return -ENOMEM;
	}
	status = sensorium_runtime_queue_transport_locked(runtime, req);
	if (status) {
		xa_erase(&runtime->request_index, req->id);
		list_del(&req->list);
		req->listed = false;
		mutex_unlock(&runtime->lock);
		sensorium_runtime_free_request(req);
		return status;
	}
	mutex_unlock(&runtime->lock);

	timeout = wait_for_completion_timeout(&req->done,
					      msecs_to_jiffies(sensorium_runtime_timeout_ms));
	if (!timeout) {
		mutex_lock(&runtime->lock);
		if (req->listed) {
			xa_erase(&runtime->request_index, req->id);
			list_del(&req->list);
			req->listed = false;
			if (runtime->control_page) {
				if (runtime->control_page->inflight_in_use > 0)
					runtime->control_page->inflight_in_use--;
				runtime->control_page->request_timeout_generation++;
				runtime->control_page->request_timeout_total++;
			}
		}
		mutex_unlock(&runtime->lock);
		sensorium_runtime_free_request(req);
		return -ETIMEDOUT;
	}

	status = req->status;
	if (!status && response_len)
		*response_len = req->actual_response_len;

	mutex_lock(&runtime->lock);
	if (req->listed) {
		xa_erase(&runtime->request_index, req->id);
		list_del(&req->list);
		req->listed = false;
	}
	mutex_unlock(&runtime->lock);
	sensorium_runtime_free_request(req);
	return status;
}
