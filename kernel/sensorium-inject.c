#include <linux/module.h>
#include "sensorium.h"

static int sensorium_inject_queue_setup(struct vb2_queue *vq,
					 unsigned int *num_buffers,
					 unsigned int *num_planes,
					 unsigned int sizes[],
					 struct device *alloc_devs[])
{
	struct sensorium_node *node = vb2_get_drv_priv(vq);

	*num_planes = 1;
	sizes[0] = node->sizeimage;

	return 0;
}

static int sensorium_inject_buf_prepare(struct vb2_buffer *vb)
{
	struct sensorium_node *node = vb2_get_drv_priv(vb->vb2_queue);
	size_t needed = node->sizeimage;

	if (vb2_plane_size(vb, 0) < needed)
		return -EINVAL;

	vb2_set_plane_payload(vb, 0, needed);
	return 0;
}

static void sensorium_inject_buf_queue(struct vb2_buffer *vb)
{
	struct sensorium_node *node = vb2_get_drv_priv(vb->vb2_queue);
	struct sensorium_device *sim = node->sim;
	struct sensorium_buffer *buf = to_sensorium_buffer(vb);
	bool restart_clock;

	mutex_lock(&sim->lock);
	list_add_tail(&buf->list, &node->buffers);
	restart_clock = list_is_singular(&node->buffers);
	if (restart_clock)
		sensorium_arm_clock_locked(sim);
	mutex_unlock(&sim->lock);
}

static int sensorium_inject_start_streaming(struct vb2_queue *vq,
					     unsigned int count)
{
	struct sensorium_node *node = vb2_get_drv_priv(vq);
	struct sensorium_device *sim = node->sim;

	mutex_lock(&sim->lock);
	node->streaming = true;
	sensorium_reset_clock_locked(sim);
	sensorium_arm_clock_locked(sim);
	mutex_unlock(&sim->lock);

	return 0;
}

static void sensorium_return_all_buffers(struct sensorium_node *node,
					  enum vb2_buffer_state state)
{
	struct sensorium_buffer *buf, *tmp;

	list_for_each_entry_safe(buf, tmp, &node->buffers, list) {
		list_del_init(&buf->list);
		vb2_buffer_done(&buf->vb.vb2_buf, state);
	}
}

static void sensorium_inject_stop_streaming(struct vb2_queue *vq)
{
	struct sensorium_node *node = vb2_get_drv_priv(vq);
	struct sensorium_device *sim = node->sim;

	mutex_lock(&sim->lock);
	node->streaming = false;
	mutex_unlock(&sim->lock);

	sensorium_stop_streaming(sim);

	mutex_lock(&sim->lock);
	if (sim->held_inject) {
		vb2_buffer_done(&sim->held_inject->vb.vb2_buf, VB2_BUF_STATE_ERROR);
		sim->held_inject = NULL;
	}
	sensorium_return_all_buffers(node, VB2_BUF_STATE_ERROR);
	mutex_unlock(&sim->lock);
}

static const struct vb2_ops sensorium_inject_qops = {
	.queue_setup = sensorium_inject_queue_setup,
	.buf_prepare = sensorium_inject_buf_prepare,
	.buf_queue = sensorium_inject_buf_queue,
	.start_streaming = sensorium_inject_start_streaming,
	.stop_streaming = sensorium_inject_stop_streaming,
	.wait_prepare = vb2_ops_wait_prepare,
	.wait_finish = vb2_ops_wait_finish,
};

static int sensorium_querycap(struct file *file, void *priv,
			       struct v4l2_capability *cap)
{
	struct sensorium_node *node = video_drvdata(file);

	strscpy(cap->driver, SENSORIUM_DRIVER_NAME, sizeof(cap->driver));
	strscpy(cap->card, node->sim->profile->card_name, sizeof(cap->card));
	strscpy(cap->bus_info, "platform:sensorium", sizeof(cap->bus_info));
	return 0;
}

static int sensorium_inject_enum_fmt(struct file *file, void *priv,
				      struct v4l2_fmtdesc *f)
{
	struct sensorium_node *node = video_drvdata(file);

	if (f->mbus_code && f->mbus_code != node->sim->active_mode->code)
		return -EINVAL;

	switch (f->index) {
	case 0:
		f->pixelformat = V4L2_PIX_FMT_BGR32;
		break;
	case 1:
		f->pixelformat = V4L2_PIX_FMT_RGB32;
		break;
	case 2:
		f->pixelformat = V4L2_PIX_FMT_BGR24;
		break;
	case 3:
		f->pixelformat = V4L2_PIX_FMT_RGB24;
		break;
	case 4:
		f->pixelformat = V4L2_PIX_FMT_SRGGB10;
		break;
	default:
		return -EINVAL;
	}

	return 0;
}

static int sensorium_inject_enum_framesizes(struct file *file, void *priv,
					     struct v4l2_frmsizeenum *fsize)
{
	switch (fsize->pixel_format) {
	case V4L2_PIX_FMT_BGR32:
	case V4L2_PIX_FMT_RGB32:
	case V4L2_PIX_FMT_RGB24:
	case V4L2_PIX_FMT_BGR24:
	case V4L2_PIX_FMT_SRGGB10:
		break;
	default:
		return -EINVAL;
	}

	if (fsize->index >= sensorium_num_modes)
		return -EINVAL;

	fsize->type = V4L2_FRMSIZE_TYPE_DISCRETE;
	fsize->discrete.width = sensorium_modes[fsize->index].width;
	fsize->discrete.height = sensorium_modes[fsize->index].height;

	return 0;
}

static int sensorium_inject_enum_frameintervals(struct file *file, void *priv,
						 struct v4l2_frmivalenum *fival)
{
	const struct sensorium_mode *mode;

	switch (fival->pixel_format) {
	case V4L2_PIX_FMT_BGR32:
	case V4L2_PIX_FMT_RGB32:
	case V4L2_PIX_FMT_RGB24:
	case V4L2_PIX_FMT_BGR24:
	case V4L2_PIX_FMT_SRGGB10:
		break;
	default:
		return -EINVAL;
	}

	if (fival->index >= sensorium_num_modes)
		return -EINVAL;

	mode = &sensorium_modes[fival->index];
	if (fival->width != mode->width || fival->height != mode->height)
		return -EINVAL;

	fival->type = V4L2_FRMIVAL_TYPE_DISCRETE;
	fival->discrete.numerator = 1;
	fival->discrete.denominator = 30;

	return 0;
}

static int sensorium_inject_g_fmt(struct file *file, void *priv,
				   struct v4l2_format *f)
{
	struct sensorium_node *node = video_drvdata(file);

	sensorium_fill_inject_pix_format(node->sim, &f->fmt.pix);
	return 0;
}

static int sensorium_inject_s_fmt(struct file *file, void *priv,
				   struct v4l2_format *f)
{
	struct sensorium_node *node = video_drvdata(file);
	struct sensorium_device *sim = node->sim;
	const struct sensorium_mode *mode;
	int ret = 0;

	mode = sensorium_find_mode(f->fmt.pix.width, f->fmt.pix.height);
	if (f->fmt.pix.pixelformat != V4L2_PIX_FMT_BGR32 &&
	    f->fmt.pix.pixelformat != V4L2_PIX_FMT_RGB32 &&
	    f->fmt.pix.pixelformat != V4L2_PIX_FMT_RGB24 &&
	    f->fmt.pix.pixelformat != V4L2_PIX_FMT_BGR24 &&
	    f->fmt.pix.pixelformat != V4L2_PIX_FMT_SRGGB10)
		f->fmt.pix.pixelformat = V4L2_PIX_FMT_BGR32;

	mutex_lock(&sim->lock);
	if ((node->streaming || vb2_is_busy(&node->vbq)) &&
	    (mode != sim->active_mode ||
	     f->fmt.pix.pixelformat != node->pixelformat))
		ret = -EBUSY;
	else {
		if (mode != sim->active_mode)
			sensorium_sensor_apply_mode(sim, mode);
		if (!ret)
			ret = sensorium_set_inject_format(sim,
							   f->fmt.pix.pixelformat);
	}
	sensorium_fill_inject_pix_format(sim, &f->fmt.pix);
	mutex_unlock(&sim->lock);

	return ret;
}

static int sensorium_inject_try_fmt(struct file *file, void *priv,
				     struct v4l2_format *f)
{
	const struct sensorium_mode *mode;

	mode = sensorium_find_mode(f->fmt.pix.width, f->fmt.pix.height);
	if (f->fmt.pix.pixelformat != V4L2_PIX_FMT_BGR32 &&
	    f->fmt.pix.pixelformat != V4L2_PIX_FMT_RGB32 &&
	    f->fmt.pix.pixelformat != V4L2_PIX_FMT_RGB24 &&
	    f->fmt.pix.pixelformat != V4L2_PIX_FMT_BGR24 &&
	    f->fmt.pix.pixelformat != V4L2_PIX_FMT_SRGGB10)
		f->fmt.pix.pixelformat = V4L2_PIX_FMT_BGR32;

	f->fmt.pix.width = mode->width;
	f->fmt.pix.height = mode->height;
	if (f->fmt.pix.pixelformat == V4L2_PIX_FMT_SRGGB10) {
		f->fmt.pix.bytesperline = mode->bytesperline;
		f->fmt.pix.sizeimage = mode->frame_size;
		f->fmt.pix.colorspace = V4L2_COLORSPACE_RAW;
		f->fmt.pix.ycbcr_enc = V4L2_YCBCR_ENC_DEFAULT;
		f->fmt.pix.quantization = V4L2_QUANTIZATION_DEFAULT;
		f->fmt.pix.xfer_func = V4L2_XFER_FUNC_NONE;
	} else {
		u32 pixel_stride =
			(f->fmt.pix.pixelformat == V4L2_PIX_FMT_BGR32 ||
			 f->fmt.pix.pixelformat == V4L2_PIX_FMT_RGB32) ? 4 : 3;

		f->fmt.pix.bytesperline = mode->width * pixel_stride;
		f->fmt.pix.sizeimage = mode->width * mode->height * pixel_stride;
		f->fmt.pix.colorspace = V4L2_COLORSPACE_SRGB;
		f->fmt.pix.ycbcr_enc = V4L2_YCBCR_ENC_DEFAULT;
		f->fmt.pix.quantization = V4L2_QUANTIZATION_FULL_RANGE;
		f->fmt.pix.xfer_func = V4L2_XFER_FUNC_SRGB;
	}
	f->fmt.pix.field = V4L2_FIELD_NONE;

	return 0;
}

static const struct v4l2_ioctl_ops sensorium_inject_ioctl_ops = {
	.vidioc_querycap = sensorium_querycap,
	.vidioc_enum_fmt_vid_out = sensorium_inject_enum_fmt,
	.vidioc_enum_framesizes = sensorium_inject_enum_framesizes,
	.vidioc_enum_frameintervals = sensorium_inject_enum_frameintervals,
	.vidioc_g_fmt_vid_out = sensorium_inject_g_fmt,
	.vidioc_s_fmt_vid_out = sensorium_inject_s_fmt,
	.vidioc_try_fmt_vid_out = sensorium_inject_try_fmt,
	.vidioc_reqbufs = vb2_ioctl_reqbufs,
	.vidioc_create_bufs = vb2_ioctl_create_bufs,
	.vidioc_prepare_buf = vb2_ioctl_prepare_buf,
	.vidioc_querybuf = vb2_ioctl_querybuf,
	.vidioc_qbuf = vb2_ioctl_qbuf,
	.vidioc_dqbuf = vb2_ioctl_dqbuf,
	.vidioc_streamon = vb2_ioctl_streamon,
	.vidioc_streamoff = vb2_ioctl_streamoff,
	.vidioc_expbuf = vb2_ioctl_expbuf,
};

static const struct v4l2_file_operations sensorium_inject_fops = {
	.owner = THIS_MODULE,
	.open = v4l2_fh_open,
	.release = vb2_fop_release,
	.poll = vb2_fop_poll,
	.mmap = vb2_fop_mmap,
	.unlocked_ioctl = video_ioctl2,
};

int sensorium_inject_register(struct sensorium_device *sim)
{
	struct sensorium_node *node = &sim->inject;
	int ret;

	node->sim = sim;
	node->buf_type = V4L2_BUF_TYPE_VIDEO_OUTPUT;
	mutex_init(&node->lock);
	INIT_LIST_HEAD(&node->buffers);
	sensorium_set_inject_format(sim, sim->inject.pixelformat);

	node->vbq.type = V4L2_BUF_TYPE_VIDEO_OUTPUT;
	node->vbq.io_modes = VB2_MMAP | VB2_DMABUF;
	node->vbq.drv_priv = node;
	node->vbq.buf_struct_size = sizeof(struct sensorium_buffer);
	node->vbq.ops = &sensorium_inject_qops;
	node->vbq.mem_ops = SENSORIUM_VB2_MEMOPS;
	node->vbq.timestamp_flags = V4L2_BUF_FLAG_TIMESTAMP_MONOTONIC;
	node->vbq.lock = &node->lock;
	node->vbq.dev = &sim->pdev->dev;
	ret = vb2_queue_init(&node->vbq);
	if (ret)
		return ret;

	node->pad.flags = MEDIA_PAD_FL_SOURCE;
	strscpy(node->vdev.name, SENSORIUM_INJECT_NAME, sizeof(node->vdev.name));
	node->vdev.v4l2_dev = &sim->v4l2_dev;
	node->vdev.fops = &sensorium_inject_fops;
	node->vdev.ioctl_ops = &sensorium_inject_ioctl_ops;
	node->vdev.lock = &node->lock;
	node->vdev.release = video_device_release_empty;
	node->vdev.vfl_dir = VFL_DIR_TX;
	node->vdev.device_caps = V4L2_CAP_VIDEO_OUTPUT |
				 V4L2_CAP_STREAMING |
				 V4L2_CAP_IO_MC;
	node->vdev.queue = &node->vbq;
	node->vdev.dev_parent = &sim->pdev->dev;
	node->vdev.entity.function = MEDIA_ENT_F_IO_V4L;

	ret = media_entity_pads_init(&node->vdev.entity, 1, &node->pad);
	if (ret)
		return ret;

	video_set_drvdata(&node->vdev, node);
	ret = video_register_device(&node->vdev, VFL_TYPE_VIDEO, -1);
	if (ret) {
		media_entity_cleanup(&node->vdev.entity);
		return ret;
	}

	return 0;
}

void sensorium_inject_unregister(struct sensorium_device *sim)
{
	struct sensorium_node *node = &sim->inject;

	if (video_is_registered(&node->vdev))
		video_unregister_device(&node->vdev);
	else
		media_entity_cleanup(&node->vdev.entity);
}
