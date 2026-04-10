#include <linux/module.h>
#include "sensorium.h"

static const char * const sensorium_test_pattern_menu[] = {
	"Disabled",
	"Solid Color",
};

static void sensorium_sensor_fill_sink_fmt(struct v4l2_mbus_framefmt *fmt)
{
	sensorium_fill_mbus_format(&sensorium_modes[0], fmt);
}

static void sensorium_sensor_fill_selection(struct v4l2_rect *rect,
					     const struct sensorium_mode *mode)
{
	rect->left = 0;
	rect->top = 0;
	rect->width = mode->width;
	rect->height = mode->height;
}

static int sensorium_sensor_ctrl_error(struct sensorium_sensor *sensor,
					const char *ctrl_name)
{
	if (!sensor->ctrl_handler.error)
		return 0;

	pr_err("%s: failed to create %s control: %d\n",
	       SENSORIUM_DRIVER_NAME, ctrl_name, sensor->ctrl_handler.error);
	return sensor->ctrl_handler.error;
}

static int sensorium_sensor_enum_mbus_code(struct v4l2_subdev *sd,
					    struct v4l2_subdev_state *state,
					    struct v4l2_subdev_mbus_code_enum *code)
{
	if (code->pad >= SENSORIUM_SENSOR_PAD_COUNT || code->index > 0)
		return -EINVAL;

	code->code = MEDIA_BUS_FMT_SRGGB10_1X10;
	return 0;
}

static int sensorium_sensor_enum_frame_size(struct v4l2_subdev *sd,
					     struct v4l2_subdev_state *state,
					     struct v4l2_subdev_frame_size_enum *fse)
{
	if (fse->pad >= SENSORIUM_SENSOR_PAD_COUNT)
		return -EINVAL;

	if (fse->code != MEDIA_BUS_FMT_SRGGB10_1X10)
		return -EINVAL;

	if (fse->pad == SENSORIUM_SENSOR_PAD_SINK) {
		if (fse->index > 0)
			return -EINVAL;

		fse->min_width = sensorium_modes[0].width;
		fse->max_width = sensorium_modes[0].width;
		fse->min_height = sensorium_modes[0].height;
		fse->max_height = sensorium_modes[0].height;
		return 0;
	}

	if (fse->index >= sensorium_num_modes)
		return -EINVAL;

	fse->min_width = sensorium_modes[fse->index].width;
	fse->max_width = sensorium_modes[fse->index].width;
	fse->min_height = sensorium_modes[fse->index].height;
	fse->max_height = sensorium_modes[fse->index].height;

	return 0;
}

static int sensorium_sensor_get_fmt(struct v4l2_subdev *sd,
				     struct v4l2_subdev_state *state,
				     struct v4l2_subdev_format *fmt)
{
	struct sensorium_sensor *sensor = to_sensorium_sensor(sd);

	if (fmt->pad >= SENSORIUM_SENSOR_PAD_COUNT)
		return -EINVAL;

	if (fmt->pad == SENSORIUM_SENSOR_PAD_SINK) {
		sensorium_sensor_fill_sink_fmt(&fmt->format);
		return 0;
	}

	fmt->format = sensor->fmt;
	return 0;
}

static int sensorium_sensor_get_selection(struct v4l2_subdev *sd,
					   struct v4l2_subdev_state *state,
					   struct v4l2_subdev_selection *sel)
{
	const struct sensorium_mode *pixel_array = &sensorium_modes[0];
	struct sensorium_sensor *sensor = to_sensorium_sensor(sd);
	struct v4l2_rect crop = {
		.left = 0,
		.top = 0,
		.width = sensor->mode ? sensor->mode->width : pixel_array->width,
		.height = sensor->mode ? sensor->mode->height : pixel_array->height,
	};

	if (sel->pad >= SENSORIUM_SENSOR_PAD_COUNT)
		return -EINVAL;

	switch (sel->target) {
	case V4L2_SEL_TGT_NATIVE_SIZE:
	case V4L2_SEL_TGT_CROP_BOUNDS:
	case V4L2_SEL_TGT_CROP_DEFAULT:
		sensorium_sensor_fill_selection(&sel->r, pixel_array);
		return 0;
	case V4L2_SEL_TGT_CROP:
		sel->r = crop;
		return 0;
	default:
		return -EINVAL;
	}
}

int sensorium_sensor_apply_mode(struct sensorium_device *sim,
				 const struct sensorium_mode *mode)
{
	struct sensorium_sensor *sensor = &sim->sensor;
	u32 exposure_max = mode->height + mode->vblank - 8;

	sensor->mode = mode;
	sim->active_mode = mode;
	if (sim->held_inject) {
		vb2_buffer_done(&sim->held_inject->vb.vb2_buf, VB2_BUF_STATE_ERROR);
		sim->held_inject = NULL;
	}
	sensorium_fill_mbus_format(mode, &sensor->fmt);
	sensorium_set_inject_format(sim, sim->inject.pixelformat);

	if (sensor->pixel_rate)
		__v4l2_ctrl_modify_range(sensor->pixel_rate, mode->pixel_rate,
					 mode->pixel_rate, 1, mode->pixel_rate);
	if (sensor->hblank)
		__v4l2_ctrl_modify_range(sensor->hblank, mode->hblank,
					 mode->hblank, 1, mode->hblank);
	if (sensor->vblank)
		__v4l2_ctrl_modify_range(sensor->vblank, 1, 16384, 1,
					 mode->vblank);
	if (sensor->pixel_rate)
		__v4l2_ctrl_s_ctrl_int64(sensor->pixel_rate, mode->pixel_rate);
	if (sensor->hblank)
		__v4l2_ctrl_s_ctrl(sensor->hblank, mode->hblank);
	if (sensor->vblank)
		__v4l2_ctrl_s_ctrl(sensor->vblank, mode->vblank);
	if (sensor->exposure)
		__v4l2_ctrl_modify_range(sensor->exposure, 1, exposure_max, 1,
					 min_t(u32, sensor->exposure->val,
					       exposure_max));
	sensorium_update_timing_locked(sim);
	sensorium_reset_clock_locked(sim);

	return 0;
}

static int sensorium_sensor_set_fmt(struct v4l2_subdev *sd,
				     struct v4l2_subdev_state *state,
				     struct v4l2_subdev_format *fmt)
{
	struct sensorium_sensor *sensor = to_sensorium_sensor(sd);
	struct sensorium_device *sim;
	const struct sensorium_mode *mode;

	if (fmt->pad >= SENSORIUM_SENSOR_PAD_COUNT)
		return -EINVAL;

	sim = container_of(sensor, struct sensorium_device, sensor);

	if (fmt->pad == SENSORIUM_SENSOR_PAD_SINK) {
		sensorium_sensor_fill_sink_fmt(&fmt->format);
		return 0;
	}

	mode = sensorium_find_mode(fmt->format.width, fmt->format.height);
	sensorium_fill_mbus_format(mode, &fmt->format);

	if (fmt->which == V4L2_SUBDEV_FORMAT_TRY)
		return 0;

	mutex_lock(&sim->lock);
	if (sim->capture.streaming && mode != sim->active_mode) {
		mutex_unlock(&sim->lock);
		return -EBUSY;
	}

	if (mode != sim->active_mode)
		sensorium_sensor_apply_mode(sim, mode);
	mutex_unlock(&sim->lock);

	return 0;
}

static int sensorium_sensor_s_stream(struct v4l2_subdev *sd, int enable)
{
	struct sensorium_sensor *sensor = to_sensorium_sensor(sd);
	struct sensorium_device *sim = container_of(sensor,
						     struct sensorium_device,
						     sensor);

	mutex_lock(&sim->lock);
	sensor->streaming = enable;
	if (enable) {
		sensorium_reset_clock_locked(sim);
		sensorium_arm_clock_locked(sim);
	}
	mutex_unlock(&sim->lock);

	if (!enable)
		sensorium_stop_streaming(sim);

	return 0;
}

static int sensorium_s_ctrl(struct v4l2_ctrl *ctrl)
{
	struct sensorium_sensor *sensor =
		container_of(ctrl->handler, struct sensorium_sensor, ctrl_handler);
	struct sensorium_device *sim =
		container_of(sensor, struct sensorium_device, sensor);

	switch (ctrl->id) {
	case V4L2_CID_CAMERA_ORIENTATION:
	case V4L2_CID_CAMERA_SENSOR_ROTATION:
	case V4L2_CID_EXPOSURE:
	case V4L2_CID_ANALOGUE_GAIN:
	case V4L2_CID_HBLANK:
	case V4L2_CID_PIXEL_RATE:
	case V4L2_CID_HFLIP:
	case V4L2_CID_VFLIP:
	case V4L2_CID_TEST_PATTERN:
		return 0;
	case V4L2_CID_VBLANK:
		if (sensor->exposure) {
			u32 exposure_max = sensor->mode->height + ctrl->val - 8;

			__v4l2_ctrl_modify_range(sensor->exposure, 1, exposure_max, 1,
						 min_t(u32, sensor->exposure->val,
						       exposure_max));
		}
		sensorium_update_timing_locked(sim);
		sensorium_reset_clock_locked(sim);
		sensorium_arm_clock_locked(sim);
		return 0;
	default:
		return -EINVAL;
	}
}

static const struct v4l2_ctrl_ops sensorium_ctrl_ops = {
	.s_ctrl = sensorium_s_ctrl,
};

static const struct v4l2_subdev_pad_ops sensorium_sensor_pad_ops = {
	.enum_mbus_code = sensorium_sensor_enum_mbus_code,
	.enum_frame_size = sensorium_sensor_enum_frame_size,
	.get_fmt = sensorium_sensor_get_fmt,
	.set_fmt = sensorium_sensor_set_fmt,
	.get_selection = sensorium_sensor_get_selection,
};

static const struct v4l2_subdev_video_ops sensorium_sensor_video_ops = {
	.s_stream = sensorium_sensor_s_stream,
};

static const struct v4l2_subdev_ops sensorium_sensor_ops = {
	.pad = &sensorium_sensor_pad_ops,
	.video = &sensorium_sensor_video_ops,
};

int sensorium_sensor_register(struct sensorium_device *sim)
{
	struct sensorium_sensor *sensor = &sim->sensor;
	const struct sensorium_mode *mode = sim->active_mode;
	int ret;
	u32 exposure_max;

	v4l2_subdev_init(&sensor->sd, &sensorium_sensor_ops);
	strscpy(sensor->sd.name, sim->profile->name, sizeof(sensor->sd.name));
	sensor->sd.flags = V4L2_SUBDEV_FL_HAS_DEVNODE;
	sensor->sd.owner = THIS_MODULE;
	sensor->sd.entity.function = MEDIA_ENT_F_CAM_SENSOR;

	sensor->pads[SENSORIUM_SENSOR_PAD_SINK].flags = MEDIA_PAD_FL_SINK;
	sensor->pads[SENSORIUM_SENSOR_PAD_SOURCE].flags = MEDIA_PAD_FL_SOURCE;
	ret = media_entity_pads_init(&sensor->sd.entity,
				     SENSORIUM_SENSOR_PAD_COUNT,
				     sensor->pads);
	if (ret) {
		pr_err("%s: media_entity_pads_init failed: %d\n",
		       SENSORIUM_DRIVER_NAME, ret);
		return ret;
	}

	v4l2_ctrl_handler_init(&sensor->ctrl_handler, 10);
	sensor->ctrl_handler.lock = &sim->lock;

	sensor->camera_orientation = v4l2_ctrl_new_std_menu(
		&sensor->ctrl_handler, &sensorium_ctrl_ops,
		V4L2_CID_CAMERA_ORIENTATION,
		V4L2_CAMERA_ORIENTATION_EXTERNAL, 0,
		sim->profile->camera_orientation);
	ret = sensorium_sensor_ctrl_error(sensor, "camera orientation");
	if (ret)
		goto err_ctrl_cleanup;
	if (sensor->camera_orientation)
		sensor->camera_orientation->flags |= V4L2_CTRL_FLAG_READ_ONLY;

	sensor->camera_sensor_rotation = v4l2_ctrl_new_std(
		&sensor->ctrl_handler, &sensorium_ctrl_ops,
		V4L2_CID_CAMERA_SENSOR_ROTATION, 0, 270, 90,
		sim->profile->camera_rotation);
	ret = sensorium_sensor_ctrl_error(sensor, "camera sensor rotation");
	if (ret)
		goto err_ctrl_cleanup;
	if (sensor->camera_sensor_rotation)
		sensor->camera_sensor_rotation->flags |= V4L2_CTRL_FLAG_READ_ONLY;

	exposure_max = mode->height + mode->vblank - 8;
	sensor->exposure = v4l2_ctrl_new_std(&sensor->ctrl_handler,
					     &sensorium_ctrl_ops,
					     V4L2_CID_EXPOSURE, 1, exposure_max, 1,
					     min_t(u32, sim->profile->exposure_default,
						   exposure_max));
	ret = sensorium_sensor_ctrl_error(sensor, "exposure");
	if (ret)
		goto err_ctrl_cleanup;

	v4l2_ctrl_new_std(&sensor->ctrl_handler, &sensorium_ctrl_ops,
			  V4L2_CID_ANALOGUE_GAIN, sim->profile->analogue_gain_min,
			  sim->profile->analogue_gain_max, 1,
			  sim->profile->analogue_gain_default);
	ret = sensorium_sensor_ctrl_error(sensor, "analogue gain");
	if (ret)
		goto err_ctrl_cleanup;

	sensor->vblank = v4l2_ctrl_new_std(&sensor->ctrl_handler,
					   &sensorium_ctrl_ops,
					   V4L2_CID_VBLANK, 1, 16384, 1,
					   mode->vblank);
	ret = sensorium_sensor_ctrl_error(sensor, "vblank");
	if (ret)
		goto err_ctrl_cleanup;

	sensor->hblank = v4l2_ctrl_new_std(&sensor->ctrl_handler,
					   &sensorium_ctrl_ops,
					   V4L2_CID_HBLANK, mode->hblank,
					   mode->hblank, 1, mode->hblank);
	ret = sensorium_sensor_ctrl_error(sensor, "hblank");
	if (ret)
		goto err_ctrl_cleanup;
	if (sensor->hblank)
		sensor->hblank->flags |= V4L2_CTRL_FLAG_READ_ONLY;

	sensor->pixel_rate = v4l2_ctrl_new_std(&sensor->ctrl_handler,
					       NULL, V4L2_CID_PIXEL_RATE,
					       mode->pixel_rate,
					       mode->pixel_rate, 1,
					       mode->pixel_rate);
	ret = sensorium_sensor_ctrl_error(sensor, "pixel rate");
	if (ret)
		goto err_ctrl_cleanup;

	sensor->hflip = v4l2_ctrl_new_std(&sensor->ctrl_handler,
					  &sensorium_ctrl_ops,
					  V4L2_CID_HFLIP, 0, 1, 1, 0);
	ret = sensorium_sensor_ctrl_error(sensor, "hflip");
	if (ret)
		goto err_ctrl_cleanup;
	if (sensor->hflip)
		sensor->hflip->flags |= V4L2_CTRL_FLAG_MODIFY_LAYOUT;

	sensor->vflip = v4l2_ctrl_new_std(&sensor->ctrl_handler,
					  &sensorium_ctrl_ops,
					  V4L2_CID_VFLIP, 0, 1, 1, 0);
	ret = sensorium_sensor_ctrl_error(sensor, "vflip");
	if (ret)
		goto err_ctrl_cleanup;
	if (sensor->vflip)
		sensor->vflip->flags |= V4L2_CTRL_FLAG_MODIFY_LAYOUT;

	v4l2_ctrl_new_std_menu_items(&sensor->ctrl_handler, &sensorium_ctrl_ops,
				     V4L2_CID_TEST_PATTERN,
				     ARRAY_SIZE(sensorium_test_pattern_menu) - 1,
				     0, 0, sensorium_test_pattern_menu);
	ret = sensorium_sensor_ctrl_error(sensor, "test pattern");
	if (ret) {
		pr_err("%s: ctrl handler setup failed: %d\n",
		       SENSORIUM_DRIVER_NAME, ret);
		goto err_ctrl_cleanup;
	}

	sensor->sd.ctrl_handler = &sensor->ctrl_handler;
	mutex_lock(&sim->lock);
	sensorium_sensor_apply_mode(sim, sensorium_default_mode());
	mutex_unlock(&sim->lock);

	ret = v4l2_device_register_subdev(&sim->v4l2_dev, &sensor->sd);
	if (ret) {
		pr_err("%s: v4l2_device_register_subdev failed: %d\n",
		       SENSORIUM_DRIVER_NAME, ret);
		goto err_ctrl_cleanup;
	}

	return 0;

err_ctrl_cleanup:
	v4l2_ctrl_handler_free(&sensor->ctrl_handler);
	media_entity_cleanup(&sensor->sd.entity);
	return ret;
}

void sensorium_sensor_unregister(struct sensorium_device *sim)
{
	struct sensorium_sensor *sensor = &sim->sensor;

	v4l2_device_unregister_subdev(&sensor->sd);
	v4l2_ctrl_handler_free(&sensor->ctrl_handler);
	media_entity_cleanup(&sensor->sd.entity);
}
