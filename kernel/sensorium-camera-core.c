#include <linux/jiffies.h>
#include <linux/kernel.h>
#include <linux/math64.h>
#include <linux/unaligned.h>
#include "sensorium.h"

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

static void sensorium_warn_if_raw_bayer_misaligned(struct sensorium_device *sim,
						   const void *src)
{
	const size_t sample_count =
		min_t(size_t, sim->active_mode->frame_size / sizeof(u16), 128);
	const u8 *samples = src;
	size_t i;

	if (sim->warned_bayer_alignment)
		return;

	for (i = 0; i < sample_count; ++i) {
		u16 sample = get_unaligned_le16(samples + i * sizeof(u16));

		if (sample <= 0x03ff)
			continue;

		sim->warned_bayer_alignment = true;
		pr_warn("%s: raw SRGGB10 ingress sample 0x%04x exceeds 10-bit range; expected unpacked low-bit-aligned samples in the low 10 bits\n",
			SENSORIUM_DRIVER_NAME, sample);
		return;
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

		if (sim->inject.pixelformat == V4L2_PIX_FMT_SRGGB10) {
			sensorium_warn_if_raw_bayer_misaligned(sim, src);
			memcpy(dst, src, mode->frame_size);
		} else {
			sensorium_convert_rgb_to_rggb10(sim, src, dst);
		}
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

void sensorium_frame_work(struct work_struct *work)
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

int sensorium_camera_register_instance(struct sensorium_device *sim)
{
	int ret;

	media_device_init(&sim->mdev);
	strscpy(sim->mdev.driver_name, SENSORIUM_MEDIA_DRIVER_NAME,
		sizeof(sim->mdev.driver_name));
	strscpy(sim->mdev.model, sim->profile->media_model,
		sizeof(sim->mdev.model));
	strscpy(sim->mdev.bus_info, "platform:sensorium",
		sizeof(sim->mdev.bus_info));
	sim->mdev.dev = &sim->pdev->dev;
	strscpy(sim->v4l2_dev.name, SENSORIUM_DRIVER_NAME,
		sizeof(sim->v4l2_dev.name));

	ret = v4l2_device_register(&sim->pdev->dev, &sim->v4l2_dev);
	if (ret) {
		pr_err("%s: v4l2_device_register failed: %d\n",
		       SENSORIUM_DRIVER_NAME, ret);
		goto err_media_cleanup;
	}

	sim->v4l2_dev.mdev = &sim->mdev;

	ret = sensorium_sensor_register(sim);
	if (ret) {
		pr_err("%s: sensor register failed: %d\n",
		       SENSORIUM_DRIVER_NAME, ret);
		goto err_v4l2_unregister;
	}

	ret = sensorium_inject_register(sim);
	if (ret) {
		pr_err("%s: inject register failed: %d\n",
		       SENSORIUM_DRIVER_NAME, ret);
		goto err_sensor_unregister;
	}

	ret = sensorium_capture_register(sim);
	if (ret) {
		pr_err("%s: capture register failed: %d\n",
		       SENSORIUM_DRIVER_NAME, ret);
		goto err_inject_unregister;
	}

	ret = sensorium_create_links(sim);
	if (ret) {
		pr_err("%s: media link creation failed: %d\n",
		       SENSORIUM_DRIVER_NAME, ret);
		goto err_capture_unregister;
	}

	ret = v4l2_device_register_subdev_nodes(&sim->v4l2_dev);
	if (ret) {
		pr_err("%s: subdev node registration failed: %d\n",
		       SENSORIUM_DRIVER_NAME, ret);
		goto err_capture_unregister;
	}

	ret = media_device_register(&sim->mdev);
	if (ret) {
		pr_err("%s: media device registration failed: %d\n",
		       SENSORIUM_DRIVER_NAME, ret);
		goto err_capture_unregister;
	}

	return 0;

err_capture_unregister:
	sensorium_capture_unregister(sim);
err_inject_unregister:
	sensorium_inject_unregister(sim);
err_sensor_unregister:
	sensorium_sensor_unregister(sim);
err_v4l2_unregister:
	v4l2_device_unregister(&sim->v4l2_dev);
err_media_cleanup:
	media_device_cleanup(&sim->mdev);
	return ret;
}

void sensorium_camera_unregister_instance(struct sensorium_device *sim)
{
	media_device_unregister(&sim->mdev);
	sensorium_capture_unregister(sim);
	sensorium_inject_unregister(sim);
	sensorium_sensor_unregister(sim);
	v4l2_device_unregister(&sim->v4l2_dev);
	media_device_cleanup(&sim->mdev);
}
