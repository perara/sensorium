#include <fcntl.h>
#include <libcamera/camera.h>
#include <libcamera/camera_manager.h>
#include <libcamera/control_ids.h>
#include <libcamera/framebuffer_allocator.h>
#include <libcamera/request.h>
#include <libcamera/stream.h>
#include <linux/videodev2.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>

#include <algorithm>
#include <cmath>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <condition_variable>
#include <cstdint>
#include <iostream>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <vector>

using namespace libcamera;

namespace {

struct Options {
	StreamRole role = StreamRole::Viewfinder;
	unsigned int width = 1536;
	unsigned int height = 864;
	unsigned int frames = 50;
	unsigned int timeout_ms = 15000;
	unsigned int fps = 0;
	std::string output = "/tmp/libcamera-record.raw";
};

struct CaptureResult {
	std::mutex mutex;
	std::condition_variable cv;
	bool done = false;
	bool success = false;
	unsigned int target_frames = 0;
	unsigned int frames_written = 0;
	unsigned int active_callbacks = 0;
	uint64_t first_timestamp_ns = 0;
	uint64_t last_timestamp_ns = 0;
	std::string error;
};

std::optional<Options> parseOptions(int argc, char **argv)
{
	Options options;

	for (int i = 1; i < argc; ++i) {
		std::string arg = argv[i];

		if (arg == "--role" && i + 1 < argc) {
			std::string role = argv[++i];
			if (role == "raw")
				options.role = StreamRole::Raw;
			else if (role == "viewfinder")
				options.role = StreamRole::Viewfinder;
			else if (role == "still")
				options.role = StreamRole::StillCapture;
			else if (role == "video")
				options.role = StreamRole::VideoRecording;
			else {
				std::cerr << "Unsupported role: " << role << std::endl;
				return std::nullopt;
			}
		} else if (arg == "--width" && i + 1 < argc) {
			options.width = std::stoul(argv[++i]);
		} else if (arg == "--height" && i + 1 < argc) {
			options.height = std::stoul(argv[++i]);
		} else if (arg == "--frames" && i + 1 < argc) {
			options.frames = std::stoul(argv[++i]);
		} else if (arg == "--fps" && i + 1 < argc) {
			options.fps = std::stoul(argv[++i]);
		} else if (arg == "--timeout-ms" && i + 1 < argc) {
			options.timeout_ms = std::stoul(argv[++i]);
		} else if (arg == "--output" && i + 1 < argc) {
			options.output = argv[++i];
		} else {
			std::cerr << "Unknown argument: " << arg << std::endl;
			return std::nullopt;
		}
	}

	if (!options.frames) {
		std::cerr << "--frames must be greater than zero" << std::endl;
		return std::nullopt;
	}

	return options;
}

int applyRawSensorFps(const Options &options)
{
	double rawVblank;
	int vblank;
	int fd;
	struct v4l2_control ctrl = {};
	struct v4l2_ext_control extCtrl = {};
	struct v4l2_ext_controls extControlSet = {};
	int64_t pixelRate;
	int hblank;

	if (options.fps == 0 || options.role != StreamRole::Raw)
		return 0;

	fd = open("/dev/v4l-subdev0", O_RDWR | O_CLOEXEC);
	if (fd < 0) {
		std::cerr << "Failed to open /dev/v4l-subdev0: "
			  << std::strerror(errno) << std::endl;
		return -errno;
	}

	extCtrl.id = V4L2_CID_PIXEL_RATE;
	extControlSet.ctrl_class = V4L2_CTRL_ID2CLASS(V4L2_CID_PIXEL_RATE);
	extControlSet.count = 1;
	extControlSet.controls = &extCtrl;
	if (ioctl(fd, VIDIOC_G_EXT_CTRLS, &extControlSet) < 0) {
		std::cerr << "Failed to read PIXEL_RATE/HBLANK: "
			  << std::strerror(errno) << std::endl;
		close(fd);
		return -errno;
	}

	ctrl.id = V4L2_CID_HBLANK;
	if (ioctl(fd, VIDIOC_G_CTRL, &ctrl) < 0) {
		std::cerr << "Failed to read HBLANK: "
			  << std::strerror(errno) << std::endl;
		close(fd);
		return -errno;
	}

	pixelRate = extCtrl.value64;
	hblank = ctrl.value;
	rawVblank = static_cast<double>(pixelRate) /
		   ((options.width + hblank) * options.fps) - options.height;
	vblank = std::max(1, static_cast<int>(std::llround(rawVblank)));

	ctrl.id = V4L2_CID_VBLANK;
	ctrl.value = vblank;
	if (ioctl(fd, VIDIOC_S_CTRL, &ctrl) < 0) {
		std::cerr << "Failed to set VBLANK for requested fps: "
			  << std::strerror(errno) << std::endl;
		close(fd);
		return -errno;
	}

	close(fd);
	std::cout << "Applied raw sensor fps " << options.fps
		  << " via vertical blanking " << vblank << std::endl;
	return 0;
}

int appendFrameBuffer(int outputFd, FrameBuffer *buffer)
{
	struct MappingInfo {
		void *address = nullptr;
		size_t length = 0;
	};
	std::map<int, MappingInfo> mappings;

	for (unsigned int i = 0; i < buffer->planes().size(); ++i) {
		FrameBuffer::Plane plane = buffer->planes()[i];
		FrameMetadata::Plane metadata = buffer->metadata().planes()[i];
		int planeFd = plane.fd.get();
		size_t mapLength;
		void *base;
		const uint8_t *data;
		ssize_t written;

		if (!mappings.count(planeFd)) {
			off_t end = lseek(planeFd, 0, SEEK_END);
			if (end < 0) {
				std::cerr << "Failed to query dmabuf length: "
					  << std::strerror(errno) << std::endl;
				return -errno;
			}

			mapLength = static_cast<size_t>(end);
			base = mmap(nullptr, mapLength, PROT_READ, MAP_SHARED, planeFd, 0);
			if (base == MAP_FAILED) {
				std::cerr << "Failed to map buffer: "
					  << std::strerror(errno) << std::endl;
				return -errno;
			}

			mappings[planeFd] = { base, mapLength };
		}

		base = mappings[planeFd].address;
		if (plane.offset + metadata.bytesused > mappings[planeFd].length) {
			std::cerr << "Plane payload exceeds mapped buffer" << std::endl;
			return -EIO;
		}

		data = static_cast<const uint8_t *>(base) + plane.offset;
		written = write(outputFd, data, metadata.bytesused);
		if (written < 0 || static_cast<size_t>(written) != metadata.bytesused) {
			std::cerr << "Failed to append output frame: "
				  << std::strerror(errno) << std::endl;
			return written < 0 ? -errno : -EIO;
		}
	}

	for (const auto &[planeFd, mapping] : mappings)
		munmap(mapping.address, mapping.length);

	return 0;
}

class RecordHandler {
public:
	RecordHandler(Camera *camera, Stream *stream, int outputFd,
		      CaptureResult &result)
		: camera_(camera), stream_(stream), outputFd_(outputFd),
		  result_(result)
	{
	}

	void requestComplete(Request *completed)
	{
		struct CallbackGuard {
			explicit CallbackGuard(CaptureResult &result) : result_(result)
			{
				std::lock_guard<std::mutex> lock(result_.mutex);
				++result_.active_callbacks;
			}

			~CallbackGuard()
			{
				std::lock_guard<std::mutex> lock(result_.mutex);
				--result_.active_callbacks;
				result_.cv.notify_all();
			}

			CaptureResult &result_;
		} guard(result_);
		bool requeue = false;
		auto it = completed->buffers().find(stream_);

		if (completed->status() == Request::RequestCancelled)
			return;

		if (it == completed->buffers().end()) {
			std::lock_guard<std::mutex> lock(result_.mutex);
			result_.done = true;
			result_.error = "Completed request has no stream buffer";
			result_.cv.notify_all();
			return;
		}

		{
			uint64_t timestamp = it->second->metadata().timestamp;
			std::lock_guard<std::mutex> ioLock(ioMutex_);
			int writeRet = appendFrameBuffer(outputFd_, it->second);
			std::lock_guard<std::mutex> lock(result_.mutex);

			if (writeRet != 0) {
				result_.done = true;
				result_.error = "Failed to append captured frame";
				result_.cv.notify_all();
				return;
			}

			if (!result_.first_timestamp_ns)
				result_.first_timestamp_ns = timestamp;
			result_.last_timestamp_ns = timestamp;
			++result_.frames_written;
			if (result_.frames_written >= result_.target_frames) {
				result_.done = true;
				result_.success = true;
			} else {
				requeue = true;
			}
			result_.cv.notify_all();
		}

		if (!requeue)
			return;

		completed->reuse(Request::ReuseBuffers);
		if (camera_->queueRequest(completed) == 0)
			return;

		std::lock_guard<std::mutex> lock(result_.mutex);
		result_.done = true;
		result_.error = "Failed to requeue capture request";
		result_.cv.notify_all();
	}

private:
	Camera *camera_;
	Stream *stream_;
	int outputFd_;
	CaptureResult &result_;
	std::mutex ioMutex_;
};

} /* namespace */

int main(int argc, char **argv)
{
	std::optional<Options> parsed = parseOptions(argc, argv);
	CaptureResult result;
	CameraManager manager;
	std::shared_ptr<Camera> camera;
	std::unique_ptr<CameraConfiguration> config;
	std::unique_ptr<FrameBufferAllocator> allocator;
	std::vector<std::unique_ptr<Request>> requests;
	std::unique_ptr<RecordHandler> handler;
	Stream *stream = nullptr;
	int ret;
	int outputFd = -1;
	int exitCode = 1;
	bool acquired = false;
	bool connected = false;
	bool started = false;

	if (!parsed) {
		std::cerr << "Usage: libcamera-record [--role raw|viewfinder|still|video]"
			  << " [--width N] [--height N] [--frames N]"
			  << " [--fps N] [--timeout-ms N] [--output PATH]" << std::endl;
		return 2;
	}

	result.target_frames = parsed->frames;

	outputFd = open(parsed->output.c_str(), O_CREAT | O_TRUNC | O_WRONLY, 0644);
	if (outputFd < 0) {
		std::cerr << "Failed to open " << parsed->output << ": "
			  << std::strerror(errno) << std::endl;
		return 1;
	}

	ret = manager.start();
	if (ret) {
		std::cerr << "Failed to start CameraManager: " << ret << std::endl;
		close(outputFd);
		return 1;
	}

	if (manager.cameras().empty()) {
		std::cerr << "No libcamera cameras found" << std::endl;
		close(outputFd);
		return 1;
	}

	camera = manager.cameras()[0];
	std::cout << "Using camera " << camera->id() << std::endl;

	do {
		ret = camera->acquire();
		if (ret) {
			std::cerr << "Failed to acquire camera: " << ret << std::endl;
			break;
		}
		acquired = true;

		config = camera->generateConfiguration({ parsed->role });
		if (!config || config->empty()) {
			std::cerr << "Failed to generate camera configuration"
				  << std::endl;
			break;
		}

		StreamConfiguration &streamConfig = config->at(0);
		ControlList startControls(camera->controls());
		ControlList *startControlsPtr = nullptr;

		streamConfig.size.width = parsed->width;
		streamConfig.size.height = parsed->height;

		CameraConfiguration::Status configStatus = config->validate();
		switch (configStatus) {
		case CameraConfiguration::Valid:
			break;
		case CameraConfiguration::Adjusted:
			std::cout << "Configuration adjusted to "
				  << streamConfig.toString() << std::endl;
			break;
		case CameraConfiguration::Invalid:
			std::cerr << "Camera configuration is invalid" << std::endl;
			break;
		}
		if (configStatus == CameraConfiguration::Invalid)
			break;

		ret = camera->configure(config.get());
		if (ret) {
			std::cerr << "Failed to configure camera: " << ret
				  << std::endl;
			break;
		}

		std::cout << "Recording " << parsed->frames << " frames as "
			  << streamConfig.toString() << std::endl;

		stream = streamConfig.stream();
		allocator = std::make_unique<FrameBufferAllocator>(camera);
		ret = allocator->allocate(stream);
		if (ret < 0 || allocator->buffers(stream).empty()) {
			std::cerr << "Failed to allocate buffers: " << ret
				  << std::endl;
			break;
		}

		requests.reserve(allocator->buffers(stream).size());

		for (const std::unique_ptr<FrameBuffer> &buffer :
		     allocator->buffers(stream)) {
			std::unique_ptr<Request> request = camera->createRequest();

			if (!request) {
				std::cerr << "Failed to create request" << std::endl;
				ret = -ENOMEM;
				break;
			}

			ret = request->addBuffer(stream, buffer.get());
			if (ret) {
				std::cerr << "Failed to add buffer to request: "
					  << ret << std::endl;
				break;
			}

			requests.push_back(std::move(request));
		}
		if (ret)
			break;

		handler = std::make_unique<RecordHandler>(camera.get(), stream,
							  outputFd, result);
		camera->requestCompleted.connect(handler.get(),
						&RecordHandler::requestComplete);
		connected = true;

		if (parsed->fps > 0) {
			int64_t frameDurationUs = 1000000ULL / parsed->fps;

			startControls.set(controls::FrameDurationLimits,
					  { frameDurationUs, frameDurationUs });
			startControlsPtr = &startControls;
			std::cout << "Requested fps " << parsed->fps << std::endl;
		}

		ret = camera->start(startControlsPtr);
		if (ret) {
			std::cerr << "Failed to start camera: " << ret << std::endl;
			break;
		}
		started = true;

		ret = applyRawSensorFps(*parsed);
		if (ret)
			break;

		for (size_t i = 0; i < requests.size() &&
				    i < static_cast<size_t>(parsed->frames); ++i) {
			ret = camera->queueRequest(requests[i].get());
			if (ret) {
				std::cerr << "Failed to queue request: " << ret
					  << std::endl;
				break;
			}
		}
		if (ret)
			break;

		{
			std::unique_lock<std::mutex> lock(result.mutex);
			if (!result.cv.wait_for(lock,
						std::chrono::milliseconds(parsed->timeout_ms),
						[&] {
							return result.done &&
							       result.active_callbacks == 0;
						})) {
				result.error = "Timed out waiting for recorded frames";
			}
		}

		if (!result.success) {
			if (result.error.empty())
				result.error = "Recording failed";
			std::cerr << result.error << std::endl;
			break;
		}

		exitCode = 0;
	} while (false);

	if (started)
		camera->stop();
	if (connected)
		camera->requestCompleted.disconnect(handler.get(),
						    &RecordHandler::requestComplete);
	handler.reset();
	requests.clear();
	allocator.reset();
	config.reset();
	stream = nullptr;
	if (acquired)
		camera->release();
	camera.reset();
	manager.stop();
	if (outputFd >= 0)
		close(outputFd);

	if (exitCode)
		return 1;

	std::cout << "Recorded " << result.frames_written << " frames to "
		  << parsed->output << std::endl;
	if (result.frames_written > 0) {
		std::cout << "first_timestamp_ns=" << result.first_timestamp_ns
			  << std::endl;
		std::cout << "last_timestamp_ns=" << result.last_timestamp_ns
			  << std::endl;
		if (result.frames_written > 1 &&
		    result.last_timestamp_ns > result.first_timestamp_ns) {
			double spanSeconds =
				static_cast<double>(result.last_timestamp_ns -
						    result.first_timestamp_ns) / 1e9;
			double timestampFps =
				static_cast<double>(result.frames_written - 1) /
				spanSeconds;

			std::cout << "timestamp_span_s=" << spanSeconds << std::endl;
			std::cout << "timestamp_fps=" << timestampFps << std::endl;
		}
	}
	std::cout.flush();
	std::fflush(nullptr);
	return 0;
}
