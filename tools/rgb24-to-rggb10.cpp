#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>
#include <vector>

namespace {

struct Options {
	unsigned int width = 2304;
	unsigned int height = 1296;
	unsigned int shift = 0;
};

bool parseUnsigned(const char *value, unsigned int &out)
{
	char *end = nullptr;
	unsigned long parsed = std::strtoul(value, &end, 10);

	if (!value[0] || (end && *end))
		return false;

	out = static_cast<unsigned int>(parsed);
	return true;
}

bool parseOptions(int argc, char **argv, Options &options)
{
	for (int i = 1; i < argc; ++i) {
		std::string arg = argv[i];

		if (arg == "--width" && i + 1 < argc) {
			if (!parseUnsigned(argv[++i], options.width))
				return false;
		} else if (arg == "--height" && i + 1 < argc) {
			if (!parseUnsigned(argv[++i], options.height))
				return false;
		} else if (arg == "--shift" && i + 1 < argc) {
			if (!parseUnsigned(argv[++i], options.shift) || options.shift > 6)
				return false;
		} else {
			return false;
		}
	}

	return options.width > 0 && options.height > 0;
}

inline uint16_t toRaw10(uint8_t sample, unsigned int shift)
{
	unsigned int value =
		(static_cast<unsigned int>(sample) * 1023U + 127U) / 255U;
	return static_cast<uint16_t>(value << shift);
}

} /* namespace */

int main(int argc, char **argv)
{
	Options options;

	if (!parseOptions(argc, argv, options)) {
		std::cerr << "Usage: rgb24-to-rggb10 --width N --height N [--shift 0-6]"
			  << std::endl;
		return 2;
	}

	const size_t rgbFrameSize =
		static_cast<size_t>(options.width) * options.height * 3U;
	const size_t rawFrameSize =
		static_cast<size_t>(options.width) * options.height * sizeof(uint16_t);
	std::vector<uint8_t> rgbFrame(rgbFrameSize);
	std::vector<uint16_t> rawFrame(static_cast<size_t>(options.width) * options.height);

	while (std::cin.read(reinterpret_cast<char *>(rgbFrame.data()), rgbFrame.size()) ||
	       static_cast<size_t>(std::cin.gcount()) == rgbFrame.size()) {
		for (unsigned int y = 0; y < options.height; ++y) {
			for (unsigned int x = 0; x < options.width; ++x) {
				const size_t rgbIndex =
					(static_cast<size_t>(y) * options.width + x) * 3U;
				const uint8_t r = rgbFrame[rgbIndex];
				const uint8_t g = rgbFrame[rgbIndex + 1];
				const uint8_t b = rgbFrame[rgbIndex + 2];
				uint8_t sample;

				if ((y & 1U) == 0)
					sample = (x & 1U) == 0 ? r : g;
				else
					sample = (x & 1U) == 0 ? g : b;

				rawFrame[static_cast<size_t>(y) * options.width + x] =
					toRaw10(sample, options.shift);
			}
		}

		std::cout.write(reinterpret_cast<const char *>(rawFrame.data()), rawFrameSize);
		if (!std::cout)
			return 1;
	}

	if (std::cin.eof())
		return 0;

	return 1;
}
