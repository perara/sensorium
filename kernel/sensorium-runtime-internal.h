#ifndef SENSORIUM_RUNTIME_INTERNAL_H
#define SENSORIUM_RUNTIME_INTERNAL_H

#include <linux/completion.h>
#include <linux/delay.h>
#include <linux/device.h>
#include <linux/device/driver.h>
#include <linux/fs.h>
#include <linux/eventfd.h>
#include <linux/i2c.h>
#include <linux/jiffies.h>
#include <linux/kernel.h>
#include <linux/list.h>
#include <linux/mempool.h>
#include <linux/miscdevice.h>
#include <linux/mm.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/poll.h>
#include <linux/slab.h>
#include <linux/spi/spi.h>
#include <linux/tty.h>
#include <linux/tty_flip.h>
#include <linux/uaccess.h>
#include <linux/vmalloc.h>
#include <linux/workqueue.h>
#include <linux/xarray.h>
#include "sensorium.h"

#define SENSORIUM_RUNTIME_BRIDGE_NAME "sensorium-runtime-bridge"
#define SENSORIUM_RUNTIME_MAGIC 0x5352544dU
#define SENSORIUM_RUNTIME_VERSION 5U
#define SENSORIUM_RUNTIME_MAX_FRAME 262144U
#define SENSORIUM_RUNTIME_MAX_I2C_MSGS 256U
#define SENSORIUM_RUNTIME_MAX_SPI_XFERS 256U
#define SENSORIUM_RUNTIME_MAX_NAME 64U
#define SENSORIUM_RUNTIME_MAX_MESSAGE_PAYLOAD (4U * 1024U * 1024U)
#define SENSORIUM_RUNTIME_V5_INLINE_PAYLOAD 256U
#define SENSORIUM_RUNTIME_V5_MIN_RING_ENTRIES 8U
#define SENSORIUM_RUNTIME_V5_MAX_RING_ENTRIES 1024U
#define SENSORIUM_RUNTIME_V5_DEFAULT_RING_ENTRIES 128U
#define SENSORIUM_RUNTIME_V5_MIN_PAYLOAD_ARENA (2U * 1024U * 1024U)
#define SENSORIUM_RUNTIME_V5_DEFAULT_PAYLOAD_ARENA (8U * 1024U * 1024U)
#define SENSORIUM_RUNTIME_V5_MAX_PAYLOAD_ARENA (64U * 1024U * 1024U)
#define SENSORIUM_RUNTIME_DEFAULT_UART_LINES 1024U
#define SENSORIUM_RUNTIME_MAX_UART_LINE_LIMIT 4096U
#define SENSORIUM_RUNTIME_DEFAULT_UART_TX_CAPACITY 16384U
#define SENSORIUM_RUNTIME_DEFAULT_UART_RX_CAPACITY 16384U
#define SENSORIUM_RUNTIME_MAX_UART_QUEUE_CAPACITY 65536U

#define SENSORIUM_RUNTIME_QUEUE_CLASS_CONTROL 1U
#define SENSORIUM_RUNTIME_QUEUE_CLASS_TRANSPORT 2U
#define SENSORIUM_RUNTIME_QUEUE_CLASS_REPLY 3U

enum sensorium_runtime_msg_type {
	SENSORIUM_RUNTIME_CMD_RESET = 1,
	SENSORIUM_RUNTIME_CMD_BUS_ADD = 2,
	SENSORIUM_RUNTIME_CMD_BUS_REMOVE = 3,
	SENSORIUM_RUNTIME_CMD_DEVICE_ADD = 4,
	SENSORIUM_RUNTIME_CMD_DEVICE_REMOVE = 5,
	SENSORIUM_RUNTIME_CMD_UART_INJECT_RX = 6,
	SENSORIUM_RUNTIME_CMD_UART_SET_MODEM = 7,
	SENSORIUM_RUNTIME_CMD_REPLY = 8,
	SENSORIUM_RUNTIME_CMD_HELLO = 9,
	SENSORIUM_RUNTIME_CMD_HELLO_ACK = 10,
	SENSORIUM_RUNTIME_REQ_I2C_XFER = 101,
	SENSORIUM_RUNTIME_REQ_SPI_XFER = 102,
	SENSORIUM_RUNTIME_REQ_UART_TX = 103,
	SENSORIUM_RUNTIME_REQ_UART_CTRL = 104,
	SENSORIUM_RUNTIME_REQ_UART_CFG = 105,
};

#define SENSORIUM_RUNTIME_MAX_PAYLOAD SENSORIUM_RUNTIME_MAX_MESSAGE_PAYLOAD
#define SENSORIUM_RUNTIME_FEATURE_SHARED_RINGS BIT(0)
#define SENSORIUM_RUNTIME_FEATURE_EVENTFD_NOTIFY BIT(1)
#define SENSORIUM_RUNTIME_FEATURE_INDEXED_REQUESTS BIT(2)
#define SENSORIUM_RUNTIME_REQUIRED_FEATURES \
	(SENSORIUM_RUNTIME_FEATURE_SHARED_RINGS | \
	 SENSORIUM_RUNTIME_FEATURE_EVENTFD_NOTIFY | \
	 SENSORIUM_RUNTIME_FEATURE_INDEXED_REQUESTS)

struct sensorium_runtime_v5_desc {
	u32 session_id;
	u32 generation;
	u32 request_id;
	u16 queue_class;
	u16 opcode;
	u32 device_handle;
	u32 payload_offset;
	u32 payload_len;
	s32 status;
	u32 reserved;
} __packed;

struct sensorium_runtime_v5_control {
	u32 magic;
	u32 abi_version;
	u32 session_id;
	u32 generation;
	u32 flags;
	u32 features;
	u32 control_ring_entries;
	u32 transport_ring_entries;
	u32 reply_ring_entries;
	u32 control_ring_head;
	u32 control_ring_tail;
	u32 transport_ring_head;
	u32 transport_ring_tail;
	u32 reply_ring_head;
	u32 reply_ring_tail;
	u32 control_payload_size;
	u32 transport_payload_size;
	u32 reply_payload_size;
	u32 control_payload_head;
	u32 control_payload_tail;
	u32 transport_payload_head;
	u32 transport_payload_tail;
	u32 reply_payload_head;
	u32 reply_payload_tail;
	u32 inflight_credit_limit;
	u32 inflight_in_use;
	u32 ebusy_generation;
	u32 ebusy_total;
	u32 request_completed_total;
	u32 request_timeout_generation;
	u32 request_timeout_total;
	u32 broker_eventfd_registered;
	u32 kernel_eventfd_registered;
	u32 desynced;
	u32 reserved[5];
} __packed;

struct sensorium_runtime_v5_setup {
	u32 abi_version;
	u32 control_ring_entries;
	u32 transport_ring_entries;
	u32 reply_ring_entries;
	u32 payload_arena_size;
	u32 inflight_credit_limit;
	u32 region_size;
	u32 session_id;
	u32 generation;
	u32 features;
	u32 control_page_offset;
	u32 control_ring_offset;
	u32 transport_ring_offset;
	u32 reply_ring_offset;
	u32 control_payload_offset;
	u32 control_payload_size;
	u32 transport_payload_offset;
	u32 transport_payload_size;
	u32 reply_payload_offset;
	u32 reply_payload_size;
} __packed;

struct sensorium_runtime_v5_eventfds {
	s32 broker_eventfd;
	s32 kernel_eventfd;
} __packed;

#define SENSORIUM_RUNTIME_IOCTL_BASE 'r'
#define SENSORIUM_RUNTIME_IOCTL_SETUP_V5 \
	_IOWR(SENSORIUM_RUNTIME_IOCTL_BASE, 0x01, struct sensorium_runtime_v5_setup)
#define SENSORIUM_RUNTIME_IOCTL_REGISTER_EVENTFDS \
	_IOW(SENSORIUM_RUNTIME_IOCTL_BASE, 0x02, struct sensorium_runtime_v5_eventfds)
#define SENSORIUM_RUNTIME_IOCTL_START_V5 \
	_IO(SENSORIUM_RUNTIME_IOCTL_BASE, 0x03)
#define SENSORIUM_RUNTIME_IOCTL_SUBMIT_CONTROL \
	_IO(SENSORIUM_RUNTIME_IOCTL_BASE, 0x04)
#define SENSORIUM_RUNTIME_IOCTL_SUBMIT_REPLY \
	_IO(SENSORIUM_RUNTIME_IOCTL_BASE, 0x05)

struct sensorium_runtime_bus_cmd {
	u32 handle;
	u32 transport;
	u32 index;
	char name[SENSORIUM_RUNTIME_MAX_NAME];
} __packed;

struct sensorium_runtime_device_cmd {
	u32 handle;
	u32 transport;
	u32 bus_handle;
	u32 location;
	u32 flags;
	u32 max_speed_hz;
	u8 spi_mode;
	u8 spi_bits_per_word;
	u8 reserved[2];
	char name[SENSORIUM_RUNTIME_MAX_NAME];
} __packed;

struct sensorium_runtime_uart_rx_cmd {
	u32 handle;
	u32 reserved;
	u32 len;
	u8 data[];
} __packed;

struct sensorium_runtime_uart_modem_cmd {
	u32 handle;
	u32 mask;
	u32 values;
} __packed;

struct sensorium_runtime_reply_cmd {
	s32 status;
	u32 data_len;
	u8 data[];
} __packed;

struct sensorium_runtime_i2c_msg_desc {
	u16 addr;
	u16 flags;
	u16 len;
	u16 reserved;
} __packed;

struct sensorium_runtime_i2c_req {
	u32 device_handle;
	u32 bus_handle;
	u32 num_msgs;
	u32 data_len;
	u8 data[];
} __packed;

struct sensorium_runtime_spi_xfer_desc {
	u32 len;
	u32 speed_hz;
	u16 delay_usecs;
	u8 bits_per_word;
	u8 cs_change;
	u8 tx_nbits;
	u8 rx_nbits;
	u8 word_delay_usecs;
	u8 has_tx;
	u8 has_rx;
	u8 reserved[2];
} __packed;

struct sensorium_runtime_spi_req {
	u32 device_handle;
	u32 bus_handle;
	u32 mode;
	u32 num_xfers;
	u32 data_len;
	u32 chip_select;
	u8 data[];
} __packed;

struct sensorium_runtime_uart_req {
	u32 device_handle;
	u32 flags;
	u32 len;
	u32 modem_mask;
	u32 modem_values;
	u8 data[];
} __packed;

struct sensorium_runtime_uart_cfg_req {
	u32 device_handle;
	u32 baud_rate;
	u32 cflag;
	u32 iflag;
	u32 oflag;
	u32 lflag;
} __packed;

struct sensorium_runtime_state;
struct sensorium_runtime_uart_group;

struct sensorium_runtime_request {
	struct list_head list;
	struct completion done;
	struct sensorium_runtime_state *runtime;
	u32 id;
	u32 generation;
	u16 type;
	bool listed;
	bool replied;
	bool payload_inline;
	size_t payload_len;
	u32 payload_offset;
	u8 inline_payload[SENSORIUM_RUNTIME_V5_INLINE_PAYLOAD];
	u8 *payload;
	int status;
	void *response_buf;
	size_t response_capacity;
	size_t actual_response_len;
};

struct sensorium_runtime_bus {
	struct list_head list;
	struct sensorium_runtime_state *runtime;
	u32 handle;
	enum sensorium_transport_type transport;
	char name[SENSORIUM_RUNTIME_MAX_NAME];
	struct xarray location_index;
	union {
		struct {
			struct i2c_adapter adapter;
			unsigned int index;
			bool registered;
		} i2c;
		struct {
			struct spi_controller *ctlr;
			unsigned int index;
			bool registered;
		} spi;
	} u;
};

struct sensorium_runtime_device {
	struct list_head list;
	struct sensorium_runtime_state *runtime;
	struct sensorium_runtime_bus *bus;
	u32 handle;
	enum sensorium_transport_type transport;
	u32 location;
	char name[SENSORIUM_RUNTIME_MAX_NAME];
	struct mutex lock;
	union {
		struct {
			struct spi_device *spi;
			bool registered;
			u32 max_speed_hz;
			u8 mode;
			u8 bits_per_word;
		} spi;
		struct {
			struct sensorium_runtime_uart_group *group;
			struct tty_port port;
			bool registered;
			char base_name[32];
			unsigned int index;
			unsigned int modem_inputs;
			unsigned int modem_outputs;
			u32 baud_rate;
			u32 cflag;
			u32 iflag;
			u32 oflag;
			u32 lflag;
			wait_queue_head_t tx_waitq;
			struct delayed_work tx_work;
			size_t tx_capacity;
			size_t tx_head;
			size_t tx_tail;
			size_t tx_count;
			size_t tx_inflight;
			size_t rx_capacity;
			size_t rx_head;
			size_t rx_tail;
			size_t rx_count;
			bool throttled;
			bool disconnected;
			int last_status;
			u8 *tx_queue;
			u8 *rx_queue;
		} uart;
	} u;
};

struct sensorium_runtime_uart_group {
	struct list_head list;
	struct sensorium_runtime_state *runtime;
	struct tty_driver *driver;
	char base_name[32];
	char driver_name[64];
	unsigned int num_ports;
	unsigned int refs;
	struct sensorium_runtime_device **ports;
};

struct sensorium_runtime_state {
	struct sensorium_device *sim;
	struct miscdevice bridge;
	struct mutex lock;
	struct mutex uart_lock;
	wait_queue_head_t bridge_waitq;
	struct list_head buses;
	struct list_head devices;
	struct list_head uart_groups;
	struct list_head requests;
	struct xarray bus_index;
	struct xarray device_index;
	struct xarray request_index;
	bool daemon_open;
	bool v5_configured;
	bool v5_started;
	u32 session_id;
	u32 next_session_id;
	u32 next_request_id;
	u32 generation;
	u32 inflight_credit_limit;
	struct sensorium_runtime_v5_control *control_page;
	void *shared_area;
	size_t shared_area_len;
	u32 control_ring_offset;
	u32 transport_ring_offset;
	u32 reply_ring_offset;
	u32 control_payload_offset;
	u32 control_payload_size;
	u32 transport_payload_offset;
	u32 transport_payload_size;
	u32 reply_payload_offset;
	u32 reply_payload_size;
	struct eventfd_ctx *broker_eventfd;
	struct eventfd_ctx *kernel_eventfd;
	struct kmem_cache *request_cache;
	mempool_t *request_pool;
};

extern unsigned int sensorium_runtime_timeout_ms;
extern unsigned int sensorium_runtime_uart_tx_capacity;
extern unsigned int sensorium_runtime_uart_rx_capacity;

unsigned int sensorium_runtime_uart_port_limit(void);
unsigned int sensorium_runtime_uart_queue_capacity(unsigned int configured);

int sensorium_runtime_parse_i2c_name(const char *name, unsigned int *index);
int sensorium_runtime_parse_spi_name(const char *name, unsigned int *index);
int sensorium_runtime_parse_tty_name(const char *name, char *base,
				     size_t base_size, unsigned int *index);
struct sensorium_runtime_uart_group *
sensorium_runtime_find_uart_group_locked(struct sensorium_runtime_state *runtime,
					 const char *base_name);
struct sensorium_runtime_device *
sensorium_runtime_find_uart_device_name_locked(struct sensorium_runtime_state *runtime,
					       const char *name);
struct sensorium_runtime_bus *
sensorium_runtime_find_bus_locked(struct sensorium_runtime_state *runtime,
				  u32 handle);
struct sensorium_runtime_device *
sensorium_runtime_find_device_locked(struct sensorium_runtime_state *runtime,
				     u32 handle);
struct sensorium_runtime_device *
sensorium_runtime_find_i2c_device_locked(struct sensorium_runtime_bus *bus,
					 u16 addr);
struct sensorium_runtime_device *
sensorium_runtime_find_spi_device_locked(struct sensorium_runtime_bus *bus,
					 u16 chip_select);
void sensorium_runtime_free_request(struct sensorium_runtime_request *req);
void sensorium_runtime_fail_all_locked(struct sensorium_runtime_state *runtime,
				       int status);
int sensorium_runtime_send_request(struct sensorium_runtime_state *runtime,
				   u16 type, const void *payload,
				   size_t payload_len,
				   void *response, size_t *response_len);

extern const struct i2c_algorithm sensorium_runtime_i2c_algorithm;

int sensorium_runtime_i2c_register_bus(struct sensorium_runtime_bus *bus);
int sensorium_runtime_spi_register_bus(struct sensorium_runtime_bus *bus);
void sensorium_runtime_uart_wakeup_writers(struct sensorium_runtime_device *dev);
void sensorium_runtime_uart_mark_disconnected_locked(struct sensorium_runtime_device *dev,
						     int status);
void sensorium_runtime_uart_recover_locked(struct sensorium_runtime_device *dev);
void sensorium_runtime_uart_inject_locked(struct sensorium_runtime_device *dev,
					  const u8 *data, size_t len);
int sensorium_runtime_register_spi(struct sensorium_runtime_device *dev);
int sensorium_runtime_register_uart(struct sensorium_runtime_device *dev);
void sensorium_runtime_destroy_spi_device(struct sensorium_runtime_device *dev);
void sensorium_runtime_destroy_uart_device(struct sensorium_runtime_device *dev);
void sensorium_runtime_destroy_device(struct sensorium_runtime_device *dev);
void sensorium_runtime_destroy_bus(struct sensorium_runtime_bus *bus);

#endif
