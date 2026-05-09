#ifndef SENSORIUM_H
#define SENSORIUM_H

#include <linux/kconfig.h>
#include <linux/i2c.h>
#include <linux/list.h>
#include <linux/miscdevice.h>
#include <linux/mutex.h>
#include <linux/platform_device.h>
#include <linux/types.h>
#include <linux/tty.h>
#include <linux/tty_driver.h>
#include <linux/tty_port.h>
#include <linux/version.h>
#include <linux/workqueue.h>
#include <media/media-device.h>
#include <media/media-entity.h>
#include <media/v4l2-ctrls.h>
#include <media/v4l2-device.h>
#include <media/v4l2-ioctl.h>
#include <media/v4l2-subdev.h>
#include <media/videobuf2-dma-sg.h>
#include <media/videobuf2-v4l2.h>
#include <media/videobuf2-vmalloc.h>

#ifndef MEDIA_PAD_FL_INTERNAL
#define MEDIA_PAD_FL_INTERNAL (1U << 3)
#endif

#if IS_REACHABLE(CONFIG_VIDEOBUF2_VMALLOC)
#define SENSORIUM_VB2_MEMOPS (&vb2_vmalloc_memops)
#elif IS_REACHABLE(CONFIG_VIDEOBUF2_DMA_SG)
#define SENSORIUM_VB2_MEMOPS (&vb2_dma_sg_memops)
#else
#error "sensorium requires a reachable videobuf2 memory backend"
#endif

#define SENSORIUM_DRIVER_NAME "sensorium"
#define SENSORIUM_MEDIA_DRIVER_NAME "imx7-csi"
#define SENSORIUM_DEFAULT_ADAPTER_NAME "camera"
#define SENSORIUM_DEFAULT_TRANSPORT_NAME "virtual"
#define SENSORIUM_DEFAULT_INSTANCE_NAME "default"
#define SENSORIUM_DEFAULT_FAULT_MODE_NAME "none"
#define SENSORIUM_DEFAULT_FAMILY_NAME "imx"
#define SENSORIUM_DEFAULT_SENSOR_NAME "imx708"
#define SENSORIUM_CAPTURE_NAME "sensorium-capture"
#define SENSORIUM_INJECT_NAME "sensorium-inject"

struct iio_dev;

enum sensorium_sensor_pad {
	SENSORIUM_SENSOR_PAD_SINK = 0,
	SENSORIUM_SENSOR_PAD_SOURCE = 1,
	SENSORIUM_SENSOR_PAD_COUNT,
};

enum sensorium_adapter_type {
	SENSORIUM_ADAPTER_CAMERA = 0,
	SENSORIUM_ADAPTER_IIO,
	SENSORIUM_ADAPTER_RUNTIME,
};

enum sensorium_transport_type {
	SENSORIUM_TRANSPORT_VIRTUAL = 0,
	SENSORIUM_TRANSPORT_I2C,
	SENSORIUM_TRANSPORT_SPI,
	SENSORIUM_TRANSPORT_UART,
};

enum sensorium_fault_mode {
	SENSORIUM_FAULT_NONE = 0,
	SENSORIUM_FAULT_STALE_DATA,
	SENSORIUM_FAULT_TIMEOUT,
};

struct sensorium_mode {
	u32 width;
	u32 height;
	u32 code;
	u32 pixelformat;
	u32 bytesperline;
	u32 frame_size;
	u32 pixel_rate;
	u32 hblank;
	u32 vblank;
	u32 frame_interval_ms;
};

struct sensorium_profile {
	const char *name;
	const char *media_model;
	const char *card_name;
	u32 camera_orientation;
	s32 camera_rotation;
	u32 analogue_gain_min;
	u32 analogue_gain_max;
	u32 analogue_gain_default;
	u32 exposure_default;
	const struct sensorium_mode *modes;
	unsigned int num_modes;
};

struct sensorium_family {
	const char *name;
	const char *description;
	const char *default_sensor_name;
	const struct sensorium_profile *profiles;
	unsigned int num_profiles;
};

struct sensorium_buffer {
	struct vb2_v4l2_buffer vb;
	struct list_head list;
};

struct sensorium_device;

struct sensorium_transport {
	const char *name;
	enum sensorium_transport_type type;
	bool frame_ingress;
	bool register_access;
};

struct sensorium_adapter_ops {
	const char *name;
	enum sensorium_adapter_type type;
	bool (*supports_transport)(enum sensorium_transport_type transport);
	int (*register_instance)(struct sensorium_device *sim);
	void (*unregister_instance)(struct sensorium_device *sim);
};

struct sensorium_sensor {
	struct sensorium_device *sim;
	struct v4l2_subdev sd;
	struct media_pad pads[SENSORIUM_SENSOR_PAD_COUNT];
	struct v4l2_mbus_framefmt fmt;
	struct v4l2_ctrl_handler ctrl_handler;
	struct v4l2_ctrl *camera_orientation;
	struct v4l2_ctrl *camera_sensor_rotation;
	struct v4l2_ctrl *exposure;
	struct v4l2_ctrl *pixel_rate;
	struct v4l2_ctrl *hblank;
	struct v4l2_ctrl *vblank;
	struct v4l2_ctrl *hflip;
	struct v4l2_ctrl *vflip;
	const struct sensorium_mode *mode;
	bool streaming;
};

struct sensorium_node {
	struct sensorium_device *sim;
	struct video_device vdev;
	struct vb2_queue vbq;
	struct media_pad pad;
	struct mutex lock;
	struct list_head buffers;
	enum v4l2_buf_type buf_type;
	u32 pixelformat;
	u32 bytesperline;
	u32 sizeimage;
	u8 pixel_stride;
	u8 red_offset;
	u8 green_offset;
	u8 blue_offset;
	bool streaming;
};

struct sensorium_iio_state {
	struct iio_dev *indio_dev;
	struct delayed_work update_work;
	const char *profile_name;
	int temperature_millic;
	int pressure_pascal;
	int humidity_millipercent;
	int temperature_step_millic;
	int pressure_step_pascal;
	int humidity_step_millipercent;
	int temperature_min_millic;
	int temperature_max_millic;
	int pressure_min_pascal;
	int pressure_max_pascal;
	int humidity_min_millipercent;
	int humidity_max_millipercent;
	int temperature_bias_millic;
	int pressure_bias_pascal;
	int humidity_bias_millipercent;
	int temperature_thresh_rising_millic;
	int last_temperature_reported_millic;
	bool humidity_enabled;
	bool temperature_event_enabled;
	u32 update_interval_ms;
};

struct sensorium_runtime_state;

struct sensorium_transport_alias {
	struct miscdevice miscdev;
	bool registered;
	char name[64];
	u8 spi_mode;
	u8 spi_bits_per_word;
	u32 spi_max_speed_hz;
};

struct sensorium_i2c_alias {
	struct i2c_adapter adapter;
	bool registered;
	char name[64];
	unsigned int index;
	u16 addr;
	u8 reg_ptr;
	u8 registers[256];
};

struct sensorium_uart_alias {
	struct tty_driver *driver;
	struct tty_port port;
	bool registered;
	char name[64];
	unsigned int index;
};

struct sensorium_device {
	struct platform_device *pdev;
	struct v4l2_device v4l2_dev;
	struct media_device mdev;
	struct mutex lock;
	struct delayed_work frame_work;
	const struct sensorium_adapter_ops *adapter;
	const struct sensorium_transport *transport;
	enum sensorium_fault_mode fault_mode;
	char instance_name[64];
	char transport_device_name[64];
	struct sensorium_sensor sensor;
	struct sensorium_node inject;
	struct sensorium_node capture;
	struct sensorium_iio_state iio;
	struct sensorium_runtime_state *runtime;
	struct sensorium_transport_alias transport_alias;
	struct sensorium_i2c_alias i2c_alias;
	struct sensorium_uart_alias uart_alias;
	struct sensorium_buffer *held_inject;
	const struct sensorium_family *family;
	const struct sensorium_profile *profile;
	const struct sensorium_mode *active_mode;
	u16 sample_lut[256];
	u32 sequence;
	u64 frame_interval_ns;
	u64 next_frame_ns;
	bool repeat_last_frame;
	bool warned_bayer_alignment;
};

extern unsigned int sensorium_i2c_address;

static inline struct sensorium_sensor *to_sensorium_sensor(struct v4l2_subdev *sd)
{
	return container_of(sd, struct sensorium_sensor, sd);
}

static inline struct sensorium_buffer *to_sensorium_buffer(struct vb2_buffer *vb)
{
	return container_of(vb, struct sensorium_buffer, vb.vb2_buf);
}

static inline struct sensorium_buffer *
to_sensorium_vb2_v4l2_buffer(struct vb2_v4l2_buffer *vb)
{
	return container_of(vb, struct sensorium_buffer, vb);
}

const struct sensorium_family *sensorium_find_family(const char *name);
const struct sensorium_profile *sensorium_find_profile(const struct sensorium_family *family,
						       const char *name);
const struct sensorium_profile *sensorium_default_profile(const struct sensorium_family *family);
const struct sensorium_mode *sensorium_find_mode(const struct sensorium_device *sim,
						 u32 width, u32 height);
const struct sensorium_mode *sensorium_default_mode(const struct sensorium_device *sim);
size_t sensorium_max_frame_size(const struct sensorium_device *sim);
void sensorium_fill_pix_format(const struct sensorium_mode *mode,
			       struct v4l2_pix_format *pix);
void sensorium_fill_inject_pix_format(struct sensorium_device *sim,
				      struct v4l2_pix_format *pix);
int sensorium_set_inject_format(struct sensorium_device *sim, u32 pixelformat);
void sensorium_update_timing_locked(struct sensorium_device *sim);
void sensorium_reset_clock_locked(struct sensorium_device *sim);
void sensorium_arm_clock_locked(struct sensorium_device *sim);
void sensorium_fill_mbus_format(const struct sensorium_mode *mode,
				struct v4l2_mbus_framefmt *fmt);
bool sensorium_queues_busy(struct sensorium_device *sim);
void sensorium_stop_streaming(struct sensorium_device *sim);
void sensorium_frame_work(struct work_struct *work);
const struct sensorium_transport *sensorium_find_transport(const char *name);
const struct sensorium_adapter_ops *sensorium_find_adapter(const char *name);
enum sensorium_fault_mode sensorium_find_fault_mode(const char *name);
int sensorium_set_transport_device_name(struct sensorium_device *sim,
					const char *name);
int sensorium_camera_register_instance(struct sensorium_device *sim);
void sensorium_camera_unregister_instance(struct sensorium_device *sim);

int sensorium_sensor_register(struct sensorium_device *sim);
void sensorium_sensor_unregister(struct sensorium_device *sim);
int sensorium_sensor_apply_mode(struct sensorium_device *sim,
				const struct sensorium_mode *mode);

int sensorium_inject_register(struct sensorium_device *sim);
void sensorium_inject_unregister(struct sensorium_device *sim);

int sensorium_capture_register(struct sensorium_device *sim);
void sensorium_capture_unregister(struct sensorium_device *sim);

int sensorium_iio_register(struct sensorium_device *sim);
void sensorium_iio_unregister(struct sensorium_device *sim);

int sensorium_runtime_register(struct sensorium_device *sim);
void sensorium_runtime_unregister(struct sensorium_device *sim);

int sensorium_transport_alias_register(struct sensorium_device *sim);
void sensorium_transport_alias_unregister(struct sensorium_device *sim);

#endif /* SENSORIUM_H */
