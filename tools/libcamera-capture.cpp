#include <fcntl.h>
#include <libcamera/camera.h>
#include <libcamera/camera_manager.h>
#include <libcamera/framebuffer_allocator.h>
#include <libcamera/request.h>
#include <libcamera/stream.h>
#include <sys/mman.h>
#include <unistd.h>

#include <cerrno>
#include <cstdio>
#include <condition_variable>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <utility>
#include <vector>

using namespace libcamera;

namespace {

struct Options {
	StreamRole role = StreamRole::Viewfinder;
	unsigned int width = 1536;
	unsigned int height = 864;
	std::string output = "/tmp/libcamera-capture.bin";
	unsigned int timeout_ms = 10000;
};

struct CaptureResult {
	std::mutex mutex;
	std::condition_variable cv;
	bool done = false;
	bool success = false;
	unsigned int active_callbacks = 0;
	std::string error;
};

int writeFrameBuffer(FrameBuffer *buffer, const std::string &path);

class CaptureHandler {
public:
	CaptureHandler(Stream *stream, const std::string &output,
		      CaptureResult &result, bool fastExitOnSuccess)
		: stream_(stream), output_(output), result_(result),
		  fastExitOnSuccess_(fastExitOnSuccess)
	{
	}

	void requestComplete(Request *completed)
	{
		struct CallbackGuard {
			explicit CallbackGuard(CaptureResult &result) : result_(result) {
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
		int writeRet;
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

		writeRet = writeFrameBuffer(it->second, output_);
		{
			std::lock_guard<std::mutex> lock(result_.mutex);
			result_.done = true;
			result_.success = writeRet == 0;
			if (writeRet != 0)
				result_.error = "Failed to write captured frame";
		}
		result_.cv.notify_all();

		if (writeRet == 0 && fastExitOnSuccess_) {
			std::cout << "Captured frame to " << output_ << std::endl;
			std::cout.flush();
			std::fflush(nullptr);
			_Exit(0);
		}
	}

private:
	Stream *stream_;
	std::string output_;
	CaptureResult &result_;
	bool fastExitOnSuccess_;
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
		} else if (arg == "--output" && i + 1 < argc) {
			options.output = argv[++i];
		} else if (arg == "--timeout-ms" && i + 1 < argc) {
			options.timeout_ms = std::stoul(argv[++i]);
		} else {
			std::cerr << "Unknown argument: " << arg << std::endl;
			return std::nullopt;
		}
	}

	return options;
}

int writeFrameBuffer(FrameBuffer *buffer, const std::string &path)
{
	struct MappingInfo {
		void *address = nullptr;
		size_t length = 0;
	};
	std::map<int, MappingInfo> mappings;
	int fd;

	fd = open(path.c_str(), O_CREAT | O_TRUNC | O_WRONLY, 0644);
	if (fd < 0) {
		std::cerr << "Failed to open " << path << ": "
			  << std::strerror(errno) << std::endl;
		return -errno;
	}

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
				close(fd);
				return -errno;
			}

			mapLength = static_cast<size_t>(end);
			base = mmap(nullptr, mapLength, PROT_READ, MAP_SHARED, planeFd, 0);
			if (base == MAP_FAILED) {
				std::cerr << "Failed to map buffer: "
					  << std::strerror(errno) << std::endl;
				close(fd);
				return -errno;
			}

			mappings[planeFd] = { base, mapLength };
		}

		base = mappings[planeFd].address;
		if (plane.offset + metadata.bytesused > mappings[planeFd].length) {
			std::cerr << "Plane payload exceeds mapped buffer" << std::endl;
			close(fd);
			return -EIO;
		}

		data = static_cast<const uint8_t *>(base) + plane.offset;
		written = write(fd, data, metadata.bytesused);
		if (written < 0 || static_cast<size_t>(written) != metadata.bytesused) {
			std::cerr << "Failed to write output frame: "
				  << std::strerror(errno) << std::endl;
			close(fd);
			return written < 0 ? -errno : -EIO;
		}
	}

	close(fd);

	for (const auto &[planeFd, mapping] : mappings)
		munmap(mapping.address, mapping.length);

	return 0;
}

} /* namespace */

int main(int argc, char **argv)
{
	std::optional<Options> parsed = parseOptions(argc, argv);
	CaptureResult result;
	CameraManager manager;
	int ret;
	int exitCode = 1;

	if (!parsed) {
		std::cerr << "Usage: libcamera-capture [--role raw|viewfinder|still|video]"
			  << " [--width N] [--height N] [--output PATH]"
			  << " [--timeout-ms N]" << std::endl;
		return 2;
	}

	ret = manager.start();
	if (ret) {
		std::cerr << "Failed to start CameraManager: " << ret << std::endl;
		return 1;
	}

	if (manager.cameras().empty()) {
		std::cerr << "No libcamera cameras found" << std::endl;
		return 1;
	}

	{
		std::shared_ptr<Camera> camera = manager.cameras()[0];
		std::unique_ptr<CameraConfiguration> config;
		std::unique_ptr<FrameBufferAllocator> allocator;
		std::unique_ptr<Request> request;
		std::unique_ptr<CaptureHandler> handler;
		Stream *stream = nullptr;
		bool acquired = false;
		bool started = false;
		bool connected = false;

		std::cout << "Using camera " << camera->id() << std::endl;

		do {
			ret = camera->acquire();
			if (ret) {
				std::cerr << "Failed to acquire camera: " << ret
					  << std::endl;
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
				std::cerr << "Camera configuration is invalid"
					  << std::endl;
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

			stream = streamConfig.stream();
			allocator = std::make_unique<FrameBufferAllocator>(camera);
			ret = allocator->allocate(stream);
			if (ret < 0 || allocator->buffers(stream).empty()) {
				std::cerr << "Failed to allocate buffers: " << ret
					  << std::endl;
				break;
			}

			request = camera->createRequest();
			if (!request) {
				std::cerr << "Failed to create request" << std::endl;
				break;
			}

			ret = request->addBuffer(stream, allocator->buffers(stream)[0].get());
			if (ret) {
				std::cerr << "Failed to add buffer to request: " << ret
					  << std::endl;
				break;
			}

			handler = std::make_unique<CaptureHandler>(
				stream, parsed->output, result,
				parsed->role != StreamRole::Raw);
			camera->requestCompleted.connect(handler.get(),
							 &CaptureHandler::requestComplete);
			connected = true;

			ret = camera->start();
			if (ret) {
				std::cerr << "Failed to start camera: " << ret
					  << std::endl;
				break;
			}
			started = true;

			ret = camera->queueRequest(request.get());
			if (ret) {
				std::cerr << "Failed to queue request: " << ret
					  << std::endl;
				break;
			}

			{
				std::unique_lock<std::mutex> lock(result.mutex);
				if (!result.cv.wait_for(lock,
							std::chrono::milliseconds(parsed->timeout_ms),
							[&] {
								return result.done &&
								       result.active_callbacks == 0;
							})) {
					result.error = "Timed out waiting for completed request";
				}
			}

			if (!result.success) {
				std::cerr << (result.error.empty() ? "Capture failed"
								   : result.error)
					  << std::endl;
				break;
			}

			exitCode = 0;
		} while (false);

		if (started)
			camera->stop();
		if (connected)
			camera->requestCompleted.disconnect(handler.get(),
						    &CaptureHandler::requestComplete);
		handler.reset();
		request.reset();
		allocator.reset();
		config.reset();
		if (acquired)
			camera->release();
	}

	manager.stop();

	if (exitCode) {
		if (!result.success && !result.error.empty())
			std::cerr << result.error << std::endl;
		return 1;
	}

	std::cout << "Captured frame to " << parsed->output << std::endl;
	std::cout.flush();
	std::fflush(nullptr);
	return 0;
}
