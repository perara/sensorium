#ifndef SENSORIUM_FAMILY_IMX_H
#define SENSORIUM_FAMILY_IMX_H

#define SENSORIUM_IMX_MODE(_width, _height, _hblank, _vblank) \
	{ \
		.width = (_width), \
		.height = (_height), \
		.code = MEDIA_BUS_FMT_SRGGB10_1X10, \
		.pixelformat = V4L2_PIX_FMT_SRGGB10, \
		.bytesperline = (_width) * 2, \
		.frame_size = (_width) * (_height) * 2, \
		.pixel_rate = ((_width) + (_hblank)) * ((_height) + (_vblank)) * 30, \
		.hblank = (_hblank), \
		.vblank = (_vblank), \
		.frame_interval_ms = 33, \
	}

#define SENSORIUM_IMX_PROFILE_ENTRY(_name, _modes) \
	{ \
		.name = (_name), \
		.media_model = _name " simulator", \
		.card_name = _name " simulator", \
		.camera_orientation = V4L2_CAMERA_ORIENTATION_BACK, \
		.camera_rotation = 0, \
		.analogue_gain_min = 1, \
		.analogue_gain_max = 256, \
		.analogue_gain_default = 16, \
		.exposure_default = 1000, \
		.modes = (_modes), \
		.num_modes = ARRAY_SIZE(_modes), \
	}

static const struct sensorium_mode sensorium_imx_modes_template_imx708_wide[] = {
	SENSORIUM_IMX_MODE(4608, 2592, 512, 48),
	SENSORIUM_IMX_MODE(2304, 1296, 512, 48),
	SENSORIUM_IMX_MODE(1536, 864, 512, 48),
};

static const struct sensorium_mode sensorium_imx_modes_template_imx477_12mp[] = {
	SENSORIUM_IMX_MODE(4056, 3040, 512, 48),
	SENSORIUM_IMX_MODE(2028, 1520, 512, 48),
	SENSORIUM_IMX_MODE(1332, 990, 512, 48),
};

static const struct sensorium_mode sensorium_imx_modes_template_imx219_8mp[] = {
	SENSORIUM_IMX_MODE(3280, 2464, 344, 24),
	SENSORIUM_IMX_MODE(1640, 1232, 344, 24),
	SENSORIUM_IMX_MODE(1280, 720, 344, 24),
};

static const struct sensorium_mode sensorium_imx_modes_template_imx8mp_wide[] = {
	SENSORIUM_IMX_MODE(3840, 2160, 512, 48),
	SENSORIUM_IMX_MODE(1920, 1080, 512, 48),
	SENSORIUM_IMX_MODE(1280, 720, 512, 48),
};

static const struct sensorium_mode sensorium_imx_modes_template_imx5mp_wide[] = {
	SENSORIUM_IMX_MODE(2592, 1520, 384, 24),
	SENSORIUM_IMX_MODE(1920, 1080, 384, 24),
	SENSORIUM_IMX_MODE(1280, 720, 384, 24),
};

static const struct sensorium_mode sensorium_imx_modes_template_imx4mp_wide[] = {
	SENSORIUM_IMX_MODE(2688, 1520, 384, 24),
	SENSORIUM_IMX_MODE(1920, 1080, 384, 24),
	SENSORIUM_IMX_MODE(1280, 720, 384, 24),
};

static const struct sensorium_mode sensorium_imx_modes_template_imx5mp_43[] = {
	SENSORIUM_IMX_MODE(2592, 1944, 384, 24),
	SENSORIUM_IMX_MODE(1296, 972, 384, 24),
	SENSORIUM_IMX_MODE(640, 480, 384, 24),
};

static const struct sensorium_mode sensorium_imx_modes_template_imx3mp_43[] = {
	SENSORIUM_IMX_MODE(2048, 1536, 320, 24),
	SENSORIUM_IMX_MODE(1280, 960, 320, 24),
	SENSORIUM_IMX_MODE(640, 480, 320, 24),
};

static const struct sensorium_mode sensorium_imx_modes_template_imx2mp_fhd[] = {
	SENSORIUM_IMX_MODE(1920, 1080, 320, 20),
	SENSORIUM_IMX_MODE(1280, 720, 320, 20),
	SENSORIUM_IMX_MODE(640, 480, 320, 20),
};

static const struct sensorium_mode sensorium_imx_modes_template_imx16mp_43[] = {
	SENSORIUM_IMX_MODE(4656, 3496, 512, 48),
	SENSORIUM_IMX_MODE(2328, 1748, 512, 48),
	SENSORIUM_IMX_MODE(1920, 1080, 512, 48),
};

static const struct sensorium_mode sensorium_imx_modes_template_imx20mp_43[] = {
	SENSORIUM_IMX_MODE(5280, 3956, 640, 48),
	SENSORIUM_IMX_MODE(2640, 1978, 640, 48),
	SENSORIUM_IMX_MODE(1920, 1080, 640, 48),
};

static const struct sensorium_mode sensorium_imx_modes_template_imx24mp_43[] = {
	SENSORIUM_IMX_MODE(5328, 4608, 640, 48),
	SENSORIUM_IMX_MODE(2664, 2304, 640, 48),
	SENSORIUM_IMX_MODE(1920, 1080, 640, 48),
};

static const struct sensorium_mode sensorium_imx_modes_template_imx26mp_43[] = {
	SENSORIUM_IMX_MODE(6224, 4168, 640, 64),
	SENSORIUM_IMX_MODE(3112, 2084, 640, 64),
	SENSORIUM_IMX_MODE(1920, 1080, 640, 64),
};

static const struct sensorium_mode sensorium_imx_modes_template_imx9mp_square[] = {
	SENSORIUM_IMX_MODE(3008, 3008, 384, 24),
	SENSORIUM_IMX_MODE(1504, 1504, 384, 24),
	SENSORIUM_IMX_MODE(1280, 720, 384, 24),
};

static const struct sensorium_profile sensorium_imx_profiles[] = {
	SENSORIUM_IMX_PROFILE_ENTRY("imx708", sensorium_imx_modes_template_imx708_wide),
	SENSORIUM_IMX_PROFILE_ENTRY("imx477", sensorium_imx_modes_template_imx477_12mp),
	SENSORIUM_IMX_PROFILE_ENTRY("imx219", sensorium_imx_modes_template_imx219_8mp),
	SENSORIUM_IMX_PROFILE_ENTRY("imx250", sensorium_imx_modes_template_imx5mp_43),
	SENSORIUM_IMX_PROFILE_ENTRY("imx252", sensorium_imx_modes_template_imx5mp_43),
	SENSORIUM_IMX_PROFILE_ENTRY("imx253", sensorium_imx_modes_template_imx5mp_43),
	SENSORIUM_IMX_PROFILE_ENTRY("imx264", sensorium_imx_modes_template_imx5mp_43),
	SENSORIUM_IMX_PROFILE_ENTRY("imx265", sensorium_imx_modes_template_imx5mp_43),
	SENSORIUM_IMX_PROFILE_ENTRY("imx273", sensorium_imx_modes_template_imx2mp_fhd),
	SENSORIUM_IMX_PROFILE_ENTRY("imx287", sensorium_imx_modes_template_imx2mp_fhd),
	SENSORIUM_IMX_PROFILE_ENTRY("imx290", sensorium_imx_modes_template_imx2mp_fhd),
	SENSORIUM_IMX_PROFILE_ENTRY("imx294", sensorium_imx_modes_template_imx20mp_43),
	SENSORIUM_IMX_PROFILE_ENTRY("imx296", sensorium_imx_modes_template_imx2mp_fhd),
	SENSORIUM_IMX_PROFILE_ENTRY("imx297", sensorium_imx_modes_template_imx2mp_fhd),
	SENSORIUM_IMX_PROFILE_ENTRY("imx304", sensorium_imx_modes_template_imx8mp_wide),
	SENSORIUM_IMX_PROFILE_ENTRY("imx305", sensorium_imx_modes_template_imx8mp_wide),
	SENSORIUM_IMX_PROFILE_ENTRY("imx327", sensorium_imx_modes_template_imx2mp_fhd),
	SENSORIUM_IMX_PROFILE_ENTRY("imx335", sensorium_imx_modes_template_imx5mp_wide),
	SENSORIUM_IMX_PROFILE_ENTRY("imx347", sensorium_imx_modes_template_imx477_12mp),
	SENSORIUM_IMX_PROFILE_ENTRY("imx367", sensorium_imx_modes_template_imx477_12mp),
	SENSORIUM_IMX_PROFILE_ENTRY("imx387", sensorium_imx_modes_template_imx477_12mp),
	SENSORIUM_IMX_PROFILE_ENTRY("imx392", sensorium_imx_modes_template_imx2mp_fhd),
	SENSORIUM_IMX_PROFILE_ENTRY("imx410", sensorium_imx_modes_template_imx26mp_43),
	SENSORIUM_IMX_PROFILE_ENTRY("imx412", sensorium_imx_modes_template_imx477_12mp),
	SENSORIUM_IMX_PROFILE_ENTRY("imx415", sensorium_imx_modes_template_imx8mp_wide),
	SENSORIUM_IMX_PROFILE_ENTRY("imx420", sensorium_imx_modes_template_imx5mp_43),
	SENSORIUM_IMX_PROFILE_ENTRY("imx421", sensorium_imx_modes_template_imx5mp_43),
	SENSORIUM_IMX_PROFILE_ENTRY("imx422", sensorium_imx_modes_template_imx5mp_43),
	SENSORIUM_IMX_PROFILE_ENTRY("imx425", sensorium_imx_modes_template_imx3mp_43),
	SENSORIUM_IMX_PROFILE_ENTRY("imx426", sensorium_imx_modes_template_imx5mp_43),
	SENSORIUM_IMX_PROFILE_ENTRY("imx428", sensorium_imx_modes_template_imx5mp_43),
	SENSORIUM_IMX_PROFILE_ENTRY("imx429", sensorium_imx_modes_template_imx5mp_43),
	SENSORIUM_IMX_PROFILE_ENTRY("imx430", sensorium_imx_modes_template_imx5mp_43),
	SENSORIUM_IMX_PROFILE_ENTRY("imx432", sensorium_imx_modes_template_imx3mp_43),
	SENSORIUM_IMX_PROFILE_ENTRY("imx455", sensorium_imx_modes_template_imx26mp_43),
	SENSORIUM_IMX_PROFILE_ENTRY("imx461", sensorium_imx_modes_template_imx26mp_43),
	SENSORIUM_IMX_PROFILE_ENTRY("imx462", sensorium_imx_modes_template_imx2mp_fhd),
	SENSORIUM_IMX_PROFILE_ENTRY("imx464", sensorium_imx_modes_template_imx4mp_wide),
	SENSORIUM_IMX_PROFILE_ENTRY("imx485", sensorium_imx_modes_template_imx8mp_wide),
	SENSORIUM_IMX_PROFILE_ENTRY("imx492", sensorium_imx_modes_template_imx20mp_43),
	SENSORIUM_IMX_PROFILE_ENTRY("imx515", sensorium_imx_modes_template_imx8mp_wide),
	SENSORIUM_IMX_PROFILE_ENTRY("imx519", sensorium_imx_modes_template_imx16mp_43),
	SENSORIUM_IMX_PROFILE_ENTRY("imx530", sensorium_imx_modes_template_imx24mp_43),
	SENSORIUM_IMX_PROFILE_ENTRY("imx531", sensorium_imx_modes_template_imx24mp_43),
	SENSORIUM_IMX_PROFILE_ENTRY("imx532", sensorium_imx_modes_template_imx24mp_43),
	SENSORIUM_IMX_PROFILE_ENTRY("imx533", sensorium_imx_modes_template_imx9mp_square),
	SENSORIUM_IMX_PROFILE_ENTRY("imx535", sensorium_imx_modes_template_imx24mp_43),
	SENSORIUM_IMX_PROFILE_ENTRY("imx536", sensorium_imx_modes_template_imx24mp_43),
	SENSORIUM_IMX_PROFILE_ENTRY("imx537", sensorium_imx_modes_template_imx24mp_43),
	SENSORIUM_IMX_PROFILE_ENTRY("imx568", sensorium_imx_modes_template_imx5mp_43),
	SENSORIUM_IMX_PROFILE_ENTRY("imx571", sensorium_imx_modes_template_imx26mp_43),
	SENSORIUM_IMX_PROFILE_ENTRY("imx577", sensorium_imx_modes_template_imx477_12mp),
	SENSORIUM_IMX_PROFILE_ENTRY("imx585", sensorium_imx_modes_template_imx8mp_wide),
	SENSORIUM_IMX_PROFILE_ENTRY("imx662", sensorium_imx_modes_template_imx2mp_fhd),
	SENSORIUM_IMX_PROFILE_ENTRY("imx664", sensorium_imx_modes_template_imx4mp_wide),
	SENSORIUM_IMX_PROFILE_ENTRY("imx675", sensorium_imx_modes_template_imx5mp_wide),
	SENSORIUM_IMX_PROFILE_ENTRY("imx676", sensorium_imx_modes_template_imx5mp_wide),
	SENSORIUM_IMX_PROFILE_ENTRY("imx678", sensorium_imx_modes_template_imx8mp_wide),
	SENSORIUM_IMX_PROFILE_ENTRY("imx715", sensorium_imx_modes_template_imx8mp_wide),
	SENSORIUM_IMX_PROFILE_ENTRY("imx900", sensorium_imx_modes_template_imx24mp_43),
	SENSORIUM_IMX_PROFILE_ENTRY("imx908", sensorium_imx_modes_template_imx8mp_wide),
};

static const struct sensorium_family sensorium_family_imx = {
	.name = "imx",
	.description = "Sony IMX sensor profiles",
	.default_sensor_name = SENSORIUM_DEFAULT_SENSOR_NAME,
	.profiles = sensorium_imx_profiles,
	.num_profiles = ARRAY_SIZE(sensorium_imx_profiles),
};

#endif /* SENSORIUM_FAMILY_IMX_H */
