#include <linux/jiffies.h>
#include <linux/kernel.h>
#include <linux/math64.h>
#include <linux/module.h>
#include <linux/slab.h>
#include "sensorium.h"
#include "sensorium-family-imx.h"

const struct sensorium_mode *sensorium_modes =
	sensorium_imx_modes_template_imx708_wide;
unsigned int sensorium_num_modes =
	ARRAY_SIZE(sensorium_imx_modes_template_imx708_wide);
const struct sensorium_profile *sensorium_active_profile =
	&sensorium_imx_profiles[0];

static const struct sensorium_family * const sensorium_families[] = {
	&sensorium_family_imx,
};

static struct sensorium_device *sensorium;
static void sensorium_pdev_release(struct device *dev)
{
}

static struct platform_device sensorium_pdev = {
	.name = SENSORIUM_DRIVER_NAME,
	.dev.release = sensorium_pdev_release,
};

static bool sensorium_repeat_last_frame = true;
module_param_named(repeat_last_frame, sensorium_repeat_last_frame, bool, 0644);
MODULE_PARM_DESC(repeat_last_frame,
		 "Repeat the last injected frame when ingress underruns");

static char *sensorium_family_name = SENSORIUM_DEFAULT_FAMILY_NAME;
module_param_named(family, sensorium_family_name, charp, 0644);
MODULE_PARM_DESC(family, "Select the simulated sensor family");

static char *sensorium_sensor_name = SENSORIUM_DEFAULT_SENSOR_NAME;
module_param_named(sensor, sensorium_sensor_name, charp, 0644);
MODULE_PARM_DESC(sensor,
		 "Select the simulated sensor profile");

struct sensorium_ingress_format {
	u32 pixelformat;
	u8 pixel_stride;
	u8 red_offset;
	u8 green_offset;
	u8 blue_offset;
	bool raw_passthrough;
};

static const struct sensorium_ingress_format sensorium_ingress_formats[] = {
	{
		.pixelformat = V4L2_PIX_FMT_BGR32,
		.pixel_stride = 4,
		.red_offset = 2,
		.green_offset = 1,
		.blue_offset = 0,
	},
	{
		.pixelformat = V4L2_PIX_FMT_RGB32,
		.pixel_stride = 4,
		.red_offset = 0,
		.green_offset = 1,
		.blue_offset = 2,
	},
	{
		.pixelformat = V4L2_PIX_FMT_BGR24,
		.pixel_stride = 3,
		.red_offset = 2,
		.green_offset = 1,
		.blue_offset = 0,
	},
	{
		.pixelformat = V4L2_PIX_FMT_RGB24,
		.pixel_stride = 3,
		.red_offset = 0,
		.green_offset = 1,
		.blue_offset = 2,
	},
	{
		.pixelformat = V4L2_PIX_FMT_SRGGB10,
		.pixel_stride = 2,
		.raw_passthrough = true,
	},
};

static const struct sensorium_ingress_format *
sensorium_find_ingress_format(u32 pixelformat)
{
	unsigned int i;

	for (i = 0; i < ARRAY_SIZE(sensorium_ingress_formats); ++i) {
		if (sensorium_ingress_formats[i].pixelformat == pixelformat)
			return &sensorium_ingress_formats[i];
	}

	return NULL;
}

const struct sensorium_family *sensorium_find_family(const char *name)
{
	unsigned int i;

	if (!name || !*name)
		return sensorium_families[0];

	for (i = 0; i < ARRAY_SIZE(sensorium_families); ++i) {
		if (!strcmp(sensorium_families[i]->name, name))
			return sensorium_families[i];
	}

	return NULL;
}

const struct sensorium_profile *
sensorium_find_profile(const struct sensorium_family *family, const char *name)
{
	unsigned int i;

	if (!family)
		return NULL;

	if (!name || !*name)
		return sensorium_default_profile(family);

	for (i = 0; i < family->num_profiles; ++i) {
		if (!strcmp(family->profiles[i].name, name))
			return &family->profiles[i];
	}

	return NULL;
}

const struct sensorium_profile *
sensorium_default_profile(const struct sensorium_family *family)
{
	unsigned int i;

	if (!family)
		return NULL;

	if (family->default_sensor_name) {
		for (i = 0; i < family->num_profiles; ++i) {
			if (!strcmp(family->profiles[i].name,
				    family->default_sensor_name))
				return &family->profiles[i];
		}
	}

	return family->num_profiles ? &family->profiles[0] : NULL;
}

const struct sensorium_mode *sensorium_find_mode(u32 width, u32 height)
{
	unsigned int i;

	for (i = 0; i < sensorium_num_modes; i++) {
		if (sensorium_modes[i].width == width &&
		    sensorium_modes[i].height == height)
			return &sensorium_modes[i];
	}

	return sensorium_default_mode();
}

const struct sensorium_mode *sensorium_default_mode(void)
{
	return &sensorium_modes[0];
}

size_t sensorium_max_frame_size(void)
{
	size_t max_size = 0;
	unsigned int i;

	for (i = 0; i < sensorium_num_modes; i++)
		max_size = max(max_size, (size_t)sensorium_modes[i].frame_size);

	return max_size;
}

void sensorium_fill_pix_format(const struct sensorium_mode *mode,
			       struct v4l2_pix_format *pix)
{
	pix->width = mode->width;
	pix->height = mode->height;
	pix->pixelformat = mode->pixelformat;
	pix->field = V4L2_FIELD_NONE;
	pix->bytesperline = mode->bytesperline;
	pix->sizeimage = mode->frame_size;
	pix->colorspace = V4L2_COLORSPACE_RAW;
	pix->ycbcr_enc = V4L2_YCBCR_ENC_DEFAULT;
	pix->quantization = V4L2_QUANTIZATION_DEFAULT;
	pix->xfer_func = V4L2_XFER_FUNC_NONE;
}

static void sensorium_update_inject_layout(struct sensorium_device *sim)
{
	struct sensorium_node *node = &sim->inject;
	const struct sensorium_mode *mode = sim->active_mode;
	const struct sensorium_ingress_format *format =
		sensorium_find_ingress_format(node->pixelformat);

	if (!format || format->raw_passthrough) {
		node->pixelformat = V4L2_PIX_FMT_SRGGB10;
		node->bytesperline = mode->bytesperline;
		node->sizeimage = mode->frame_size;
		node->pixel_stride = 2;
		node->red_offset = 0;
		node->green_offset = 0;
		node->blue_offset = 0;
		return;
	}

	node->bytesperline = mode->width * format->pixel_stride;
	node->sizeimage = mode->width * mode->height * format->pixel_stride;
	node->pixel_stride = format->pixel_stride;
	node->red_offset = format->red_offset;
	node->green_offset = format->green_offset;
	node->blue_offset = format->blue_offset;
}

void sensorium_fill_inject_pix_format(struct sensorium_device *sim,
				      struct v4l2_pix_format *pix)
{
	const struct sensorium_mode *mode = sim->active_mode;

	pix->width = mode->width;
	pix->height = mode->height;
	pix->pixelformat = sim->inject.pixelformat;
	pix->field = V4L2_FIELD_NONE;
	pix->bytesperline = sim->inject.bytesperline;
	pix->sizeimage = sim->inject.sizeimage;

	if (sim->inject.pixelformat == V4L2_PIX_FMT_SRGGB10) {
		pix->colorspace = V4L2_COLORSPACE_RAW;
		pix->ycbcr_enc = V4L2_YCBCR_ENC_DEFAULT;
		pix->quantization = V4L2_QUANTIZATION_DEFAULT;
		pix->xfer_func = V4L2_XFER_FUNC_NONE;
	} else {
		pix->colorspace = V4L2_COLORSPACE_SRGB;
		pix->ycbcr_enc = V4L2_YCBCR_ENC_DEFAULT;
		pix->quantization = V4L2_QUANTIZATION_FULL_RANGE;
		pix->xfer_func = V4L2_XFER_FUNC_SRGB;
	}
}

int sensorium_set_inject_format(struct sensorium_device *sim, u32 pixelformat)
{
	if (!sensorium_find_ingress_format(pixelformat))
		return -EINVAL;

	sim->inject.pixelformat = pixelformat;
	sensorium_update_inject_layout(sim);

	return 0;
}

void sensorium_fill_mbus_format(const struct sensorium_mode *mode,
				struct v4l2_mbus_framefmt *fmt)
{
	fmt->width = mode->width;
	fmt->height = mode->height;
	fmt->code = mode->code;
	fmt->field = V4L2_FIELD_NONE;
	fmt->colorspace = V4L2_COLORSPACE_RAW;
	fmt->ycbcr_enc = V4L2_YCBCR_ENC_DEFAULT;
	fmt->quantization = V4L2_QUANTIZATION_DEFAULT;
	fmt->xfer_func = V4L2_XFER_FUNC_NONE;
}

static void sensorium_convert_rgb_to_rggb10(struct sensorium_device *sim,
					     const u8 *src, void *dst)
{
	const struct sensorium_mode *mode = sim->active_mode;
	const struct sensorium_node *node = &sim->inject;
	u16 *out = dst;
	u32 x, y;

	for (y = 0; y < mode->height; ++y) {
		const u8 *row = src + y * sim->inject.bytesperline;
		u16 *dst_row = out + y * mode->width;

		if ((y & 1) == 0) {
			for (x = 0; x + 1 < mode->width; x += 2) {
				const u8 *pixel0 = row + x * node->pixel_stride;
				const u8 *pixel1 = pixel0 + node->pixel_stride;

				dst_row[x] = sim->sample_lut[pixel0[node->red_offset]];
				dst_row[x + 1] = sim->sample_lut[pixel1[node->green_offset]];
			}
		} else {
			for (x = 0; x + 1 < mode->width; x += 2) {
				const u8 *pixel0 = row + x * node->pixel_stride;
				const u8 *pixel1 = pixel0 + node->pixel_stride;

				dst_row[x] = sim->sample_lut[pixel0[node->green_offset]];
				dst_row[x + 1] = sim->sample_lut[pixel1[node->blue_offset]];
			}
		}
	}
}

bool sensorium_queues_busy(struct sensorium_device *sim)
{
	return vb2_is_busy(&sim->inject.vbq) || vb2_is_busy(&sim->capture.vbq);
}

static bool sensorium_can_run(struct sensorium_device *sim)
{
	return sim->inject.streaming &&
	       sim->capture.streaming;
}

void sensorium_update_timing_locked(struct sensorium_device *sim)
{
	const struct sensorium_mode *mode = sim->active_mode;
	u64 pixel_rate = mode->pixel_rate;
	u64 hblank = mode->hblank;
	u64 vblank = mode->vblank;
	u64 frame_pixels;
	u64 frame_ns;

	if (sim->sensor.vblank && sim->sensor.vblank->val > 0)
		vblank = sim->sensor.vblank->val;

	frame_pixels = (mode->width + hblank) * (mode->height + vblank);
	if (!pixel_rate || !frame_pixels)
		frame_ns = (u64)mode->frame_interval_ms * NSEC_PER_MSEC;
	else
		frame_ns = div_u64(frame_pixels * NSEC_PER_SEC, pixel_rate);

	if (!frame_ns)
		frame_ns = NSEC_PER_SEC / 30;

	sim->frame_interval_ns = frame_ns;
}

void sensorium_reset_clock_locked(struct sensorium_device *sim)
{
	if (!sim->frame_interval_ns)
		sensorium_update_timing_locked(sim);

	sim->next_frame_ns = ktime_get_ns();
}

static unsigned long sensorium_delay_to_next_frame_locked(struct sensorium_device *sim)
{
	u64 now = ktime_get_ns();

	if (!sim->next_frame_ns || sim->next_frame_ns <= now)
		return 0;

	return nsecs_to_jiffies(sim->next_frame_ns - now);
}

void sensorium_arm_clock_locked(struct sensorium_device *sim)
{
	if (!sensorium_can_run(sim) || list_empty(&sim->capture.buffers))
		return;

	if (!sim->next_frame_ns)
		sensorium_reset_clock_locked(sim);

	mod_delayed_work(system_wq, &sim->frame_work,
			 sensorium_delay_to_next_frame_locked(sim));
}

static void sensorium_complete_buffer(struct sensorium_buffer *buf,
				       enum vb2_buffer_state state)
{
	list_del_init(&buf->list);
	vb2_buffer_done(&buf->vb.vb2_buf, state);
}

static void sensorium_release_held_inject(struct sensorium_device *sim,
					   enum vb2_buffer_state state)
{
	if (!sim->held_inject)
		return;

	vb2_buffer_done(&sim->held_inject->vb.vb2_buf, state);
	sim->held_inject = NULL;
}

static bool sensorium_try_deliver_frame(struct sensorium_device *sim)
{
	const struct sensorium_mode *mode = sim->active_mode;
	struct sensorium_buffer *inject_buf = NULL;
	struct sensorium_buffer *capture_buf = NULL;
	size_t capture_size;
	void *src = NULL;
	void *dst = NULL;
	bool delivered = false;

	mutex_lock(&sim->lock);

	if (!sensorium_can_run(sim))
		goto out_unlock;

	if (list_empty(&sim->capture.buffers))
		goto out_unlock;

	capture_buf = list_first_entry(&sim->capture.buffers,
				       struct sensorium_buffer, list);
	capture_size = vb2_plane_size(&capture_buf->vb.vb2_buf, 0);
	if (capture_size < mode->frame_size) {
		sensorium_complete_buffer(capture_buf, VB2_BUF_STATE_ERROR);
		goto out_unlock;
	}

	dst = vb2_plane_vaddr(&capture_buf->vb.vb2_buf, 0);
	if (!dst) {
		sensorium_complete_buffer(capture_buf, VB2_BUF_STATE_ERROR);
		goto out_unlock;
	}

	while (!list_empty(&sim->inject.buffers)) {
		size_t inject_size;

		inject_buf = list_first_entry(&sim->inject.buffers,
					      struct sensorium_buffer, list);
		inject_size = vb2_plane_size(&inject_buf->vb.vb2_buf, 0);
		if (inject_size < sim->inject.sizeimage ||
		    vb2_get_plane_payload(&inject_buf->vb.vb2_buf, 0) < sim->inject.sizeimage) {
			sensorium_complete_buffer(inject_buf, VB2_BUF_STATE_ERROR);
			inject_buf = NULL;
			continue;
		}

		src = vb2_plane_vaddr(&inject_buf->vb.vb2_buf, 0);
		break;
	}

	if (src) {
		if (sim->repeat_last_frame) {
			sensorium_release_held_inject(sim, VB2_BUF_STATE_DONE);
			list_del_init(&inject_buf->list);
			sim->held_inject = inject_buf;
		}

		if (sim->inject.pixelformat == V4L2_PIX_FMT_SRGGB10)
			memcpy(dst, src, mode->frame_size);
		else
			sensorium_convert_rgb_to_rggb10(sim, src, dst);
		if (!sim->repeat_last_frame)
			sensorium_complete_buffer(inject_buf, VB2_BUF_STATE_DONE);
	} else if (sim->repeat_last_frame && sim->held_inject) {
		src = vb2_plane_vaddr(&sim->held_inject->vb.vb2_buf, 0);
		if (!src)
			goto out_unlock;
		if (sim->inject.pixelformat == V4L2_PIX_FMT_SRGGB10)
			memcpy(dst, src, mode->frame_size);
		else
			sensorium_convert_rgb_to_rggb10(sim, src, dst);
	} else {
		goto out_unlock;
	}

	vb2_set_plane_payload(&capture_buf->vb.vb2_buf, 0, mode->frame_size);
	capture_buf->vb.sequence = sim->sequence++;
	capture_buf->vb.field = V4L2_FIELD_NONE;
	capture_buf->vb.vb2_buf.timestamp = sim->next_frame_ns ?
		sim->next_frame_ns : ktime_get_ns();
	sensorium_complete_buffer(capture_buf, VB2_BUF_STATE_DONE);
	delivered = true;

out_unlock:
	mutex_unlock(&sim->lock);
	return delivered;
}

static void sensorium_frame_work(struct work_struct *work)
{
	struct sensorium_device *sim;
	u64 now;
	u64 interval;

	sim = container_of(to_delayed_work(work), struct sensorium_device,
			   frame_work);

	sensorium_try_deliver_frame(sim);

	mutex_lock(&sim->lock);
	if (!sensorium_can_run(sim)) {
		mutex_unlock(&sim->lock);
		return;
	}

	interval = sim->frame_interval_ns;
	if (!interval) {
		sensorium_update_timing_locked(sim);
		interval = sim->frame_interval_ns;
	}

	now = ktime_get_ns();
	if (!sim->next_frame_ns)
		sim->next_frame_ns = now;

	do {
		sim->next_frame_ns += interval;
	} while (sim->next_frame_ns <= now);

	if (!list_empty(&sim->capture.buffers))
		mod_delayed_work(system_wq, &sim->frame_work,
				 sensorium_delay_to_next_frame_locked(sim));
	mutex_unlock(&sim->lock);
}

void sensorium_stop_streaming(struct sensorium_device *sim)
{
	cancel_delayed_work_sync(&sim->frame_work);
	mutex_lock(&sim->lock);
	sim->next_frame_ns = 0;
	mutex_unlock(&sim->lock);
}

static int sensorium_create_links(struct sensorium_device *sim)
{
	int ret;

	ret = media_create_pad_link(&sim->inject.vdev.entity, 0,
				    &sim->sensor.sd.entity,
				    SENSORIUM_SENSOR_PAD_SINK,
				    MEDIA_LNK_FL_ENABLED | MEDIA_LNK_FL_IMMUTABLE);
	if (ret)
		return ret;

	ret = media_create_pad_link(&sim->sensor.sd.entity,
				    SENSORIUM_SENSOR_PAD_SOURCE,
				    &sim->capture.vdev.entity, 0,
				    MEDIA_LNK_FL_ENABLED | MEDIA_LNK_FL_IMMUTABLE);
	if (ret)
		return ret;

	return 0;
}

static int sensorium_probe(struct platform_device *pdev)
{
	const struct sensorium_family *family;
	int ret;

	pr_info("%s: init start\n", SENSORIUM_DRIVER_NAME);

	sensorium = kzalloc(sizeof(*sensorium), GFP_KERNEL);
	if (!sensorium)
		return -ENOMEM;

	family = sensorium_find_family(sensorium_family_name);
	if (!family) {
		pr_err("%s: unknown sensor family '%s'\n",
		       SENSORIUM_DRIVER_NAME, sensorium_family_name);
		kfree(sensorium);
		sensorium = NULL;
		return -EINVAL;
	}

	sensorium->family = family;
	sensorium->profile = sensorium_find_profile(family, sensorium_sensor_name);
	if (!sensorium->profile) {
		pr_err("%s: unknown sensor '%s' for family '%s'\n",
		       SENSORIUM_DRIVER_NAME, sensorium_sensor_name, family->name);
		kfree(sensorium);
		sensorium = NULL;
		return -EINVAL;
	}
	sensorium_active_profile = sensorium->profile;
	sensorium_modes = sensorium->profile->modes;
	sensorium_num_modes = sensorium->profile->num_modes;

	mutex_init(&sensorium->lock);
	INIT_DELAYED_WORK(&sensorium->frame_work, sensorium_frame_work);
	sensorium->active_mode = sensorium_default_mode();
	sensorium_set_inject_format(sensorium, V4L2_PIX_FMT_BGR32);
	sensorium->repeat_last_frame = sensorium_repeat_last_frame;
	sensorium->pdev = pdev;
	{
		unsigned int i;

		for (i = 0; i < ARRAY_SIZE(sensorium->sample_lut); ++i)
			sensorium->sample_lut[i] =
				((i * 1023U + 127U) / 255U) << 6;
	}
	sensorium->frame_interval_ns =
		(u64)sensorium->active_mode->frame_interval_ms * NSEC_PER_MSEC;

	media_device_init(&sensorium->mdev);
	strscpy(sensorium->mdev.driver_name, SENSORIUM_MEDIA_DRIVER_NAME,
		sizeof(sensorium->mdev.driver_name));
	strscpy(sensorium->mdev.model, sensorium->profile->media_model,
		sizeof(sensorium->mdev.model));
	strscpy(sensorium->mdev.bus_info, "platform:sensorium",
		sizeof(sensorium->mdev.bus_info));
	sensorium->mdev.dev = &pdev->dev;
	strscpy(sensorium->v4l2_dev.name, SENSORIUM_DRIVER_NAME,
		sizeof(sensorium->v4l2_dev.name));

	ret = v4l2_device_register(&pdev->dev, &sensorium->v4l2_dev);
	if (ret) {
		pr_err("%s: v4l2_device_register failed: %d\n",
		       SENSORIUM_DRIVER_NAME, ret);
		goto err_free_frame;
	}

	sensorium->v4l2_dev.mdev = &sensorium->mdev;

	ret = sensorium_sensor_register(sensorium);
	if (ret) {
		pr_err("%s: sensor register failed: %d\n",
		       SENSORIUM_DRIVER_NAME, ret);
		goto err_v4l2_unregister;
	}

	ret = sensorium_inject_register(sensorium);
	if (ret) {
		pr_err("%s: inject register failed: %d\n",
		       SENSORIUM_DRIVER_NAME, ret);
		goto err_sensor_unregister;
	}

	ret = sensorium_capture_register(sensorium);
	if (ret) {
		pr_err("%s: capture register failed: %d\n",
		       SENSORIUM_DRIVER_NAME, ret);
		goto err_inject_unregister;
	}

	ret = sensorium_create_links(sensorium);
	if (ret) {
		pr_err("%s: media link creation failed: %d\n",
		       SENSORIUM_DRIVER_NAME, ret);
		goto err_capture_unregister;
	}

	ret = v4l2_device_register_subdev_nodes(&sensorium->v4l2_dev);
	if (ret) {
		pr_err("%s: subdev node registration failed: %d\n",
		       SENSORIUM_DRIVER_NAME, ret);
		goto err_capture_unregister;
	}

	ret = media_device_register(&sensorium->mdev);
	if (ret) {
		pr_err("%s: media device registration failed: %d\n",
		       SENSORIUM_DRIVER_NAME, ret);
		goto err_capture_unregister;
	}

	pr_info("%s: registered virtual media pipeline for %s/%s\n",
		SENSORIUM_DRIVER_NAME, sensorium->family->name,
		sensorium->profile->name);
	platform_set_drvdata(pdev, sensorium);
	return 0;

err_capture_unregister:
	sensorium_capture_unregister(sensorium);
err_inject_unregister:
	sensorium_inject_unregister(sensorium);
err_sensor_unregister:
	sensorium_sensor_unregister(sensorium);
err_v4l2_unregister:
	v4l2_device_unregister(&sensorium->v4l2_dev);
err_free_frame:
	media_device_cleanup(&sensorium->mdev);
	kfree(sensorium);
	sensorium = NULL;
	return ret;
}

static void sensorium_remove(struct platform_device *pdev)
{
	struct sensorium_device *sim = platform_get_drvdata(pdev);

	if (!sim)
		return;

	sensorium_stop_streaming(sim);
	media_device_unregister(&sim->mdev);
	sensorium_capture_unregister(sim);
	sensorium_inject_unregister(sim);
	sensorium_sensor_unregister(sim);
	v4l2_device_unregister(&sim->v4l2_dev);
	media_device_cleanup(&sim->mdev);
	kfree(sim);
	sensorium = NULL;
}

static struct platform_driver sensorium_pdrv = {
	.probe = sensorium_probe,
	.remove_new = sensorium_remove,
	.driver = {
		.name = SENSORIUM_DRIVER_NAME,
	},
};

static int __init sensorium_init(void)
{
	int ret;

	ret = platform_device_register(&sensorium_pdev);
	if (ret)
		return ret;

	ret = platform_driver_register(&sensorium_pdrv);
	if (ret) {
		platform_device_unregister(&sensorium_pdev);
		return ret;
	}

	return 0;
}

static void __exit sensorium_exit(void)
{
	platform_driver_unregister(&sensorium_pdrv);
	platform_device_unregister(&sensorium_pdev);
}

module_init(sensorium_init);
module_exit(sensorium_exit);

MODULE_DESCRIPTION("Sensorium virtual media controller camera simulator");
MODULE_AUTHOR("Sensorium contributors");
MODULE_LICENSE("GPL");
