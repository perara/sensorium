#ifndef SENSORIUM_H
#define SENSORIUM_H

#include <linux/list.h>
#include <linux/kconfig.h>
#include <linux/mutex.h>
#include <linux/platform_device.h>
#include <linux/types.h>
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

#if IS_REACHABLE(CONFIG_VIDEOBUF2_DMA_SG)
#define SENSORIUM_VB2_MEMOPS (&vb2_dma_sg_memops)
#else
#define SENSORIUM_VB2_MEMOPS (&vb2_vmalloc_memops)
#endif

#define SENSORIUM_DRIVER_NAME "sensorium"
#define SENSORIUM_MEDIA_DRIVER_NAME "imx7-csi"
#define SENSORIUM_DEFAULT_FAMILY_NAME "imx"
#define SENSORIUM_DEFAULT_SENSOR_NAME "imx708"
#define SENSORIUM_CAPTURE_NAME "sensorium-capture"
#define SENSORIUM_INJECT_NAME "sensorium-inject"

enum sensorium_sensor_pad {
	SENSORIUM_SENSOR_PAD_SINK = 0,
	SENSORIUM_SENSOR_PAD_SOURCE = 1,
	SENSORIUM_SENSOR_PAD_COUNT,
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

struct sensorium_sensor {
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

struct sensorium_device {
	struct platform_device *pdev;
	struct v4l2_device v4l2_dev;
	struct media_device mdev;
	struct mutex lock;
	struct delayed_work frame_work;
	struct sensorium_sensor sensor;
	struct sensorium_node inject;
	struct sensorium_node capture;
	struct sensorium_buffer *held_inject;
	const struct sensorium_family *family;
	const struct sensorium_profile *profile;
	const struct sensorium_mode *active_mode;
	u16 sample_lut[256];
	u32 sequence;
	u64 frame_interval_ns;
	u64 next_frame_ns;
	bool repeat_last_frame;
};

extern const struct sensorium_mode *sensorium_modes;
extern unsigned int sensorium_num_modes;
extern const struct sensorium_profile *sensorium_active_profile;

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
const struct sensorium_mode *sensorium_find_mode(u32 width, u32 height);
const struct sensorium_mode *sensorium_default_mode(void);
size_t sensorium_max_frame_size(void);
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

int sensorium_sensor_register(struct sensorium_device *sim);
void sensorium_sensor_unregister(struct sensorium_device *sim);
int sensorium_sensor_apply_mode(struct sensorium_device *sim,
				const struct sensorium_mode *mode);

int sensorium_inject_register(struct sensorium_device *sim);
void sensorium_inject_unregister(struct sensorium_device *sim);

int sensorium_capture_register(struct sensorium_device *sim);
void sensorium_capture_unregister(struct sensorium_device *sim);

#endif /* SENSORIUM_H */
