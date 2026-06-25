#include "common/png_reader.hpp"

#include <algorithm>
#include <array>
#include <cstdlib>
#include <fstream>
#include <sstream>
#include <stdexcept>

namespace seat_aoi {

namespace {

constexpr std::array<std::uint8_t, 8> kPngSignature = {
    0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A};

std::uint32_t read_be32(const std::vector<std::uint8_t>& data, std::size_t offset) {
  return (static_cast<std::uint32_t>(data[offset]) << 24U) |
         (static_cast<std::uint32_t>(data[offset + 1U]) << 16U) |
         (static_cast<std::uint32_t>(data[offset + 2U]) << 8U) |
         static_cast<std::uint32_t>(data[offset + 3U]);
}

std::vector<std::uint8_t> read_file(const std::string& path) {
  std::ifstream input(path, std::ios::binary);
  if (!input.good()) {
    throw std::runtime_error("无法打开 PNG: " + path);
  }
  input.seekg(0, std::ios::end);
  const auto size = input.tellg();
  if (size <= 0) {
    throw std::runtime_error("PNG 文件为空: " + path);
  }
  input.seekg(0, std::ios::beg);
  std::vector<std::uint8_t> data(static_cast<std::size_t>(size));
  input.read(reinterpret_cast<char*>(data.data()), static_cast<std::streamsize>(data.size()));
  if (!input.good()) {
    throw std::runtime_error("读取 PNG 失败: " + path);
  }
  return data;
}

std::uint8_t paeth(std::uint8_t left, std::uint8_t up, std::uint8_t upper_left) {
  const int p = static_cast<int>(left) + static_cast<int>(up) -
                static_cast<int>(upper_left);
  const int pa = std::abs(p - static_cast<int>(left));
  const int pb = std::abs(p - static_cast<int>(up));
  const int pc = std::abs(p - static_cast<int>(upper_left));
  if (pa <= pb && pa <= pc) {
    return left;
  }
  if (pb <= pc) {
    return up;
  }
  return upper_left;
}

class BitReader {
public:
  explicit BitReader(const std::vector<std::uint8_t>& data) : data_(data) {}

  std::uint32_t read_bits(std::uint32_t count) {
    std::uint32_t value = 0;
    for (std::uint32_t index = 0; index < count; ++index) {
      value |= read_bit() << index;
    }
    return value;
  }

  std::uint32_t peek_bits(std::uint32_t count) const {
    std::uint32_t value = 0;
    for (std::uint32_t index = 0; index < count; ++index) {
      const auto current_bit = bit_offset_ + index;
      if (current_bit / 8U >= data_.size()) {
        break;
      }
      const auto byte = data_[current_bit / 8U];
      value |= static_cast<std::uint32_t>((byte >> (current_bit % 8U)) & 1U) << index;
    }
    return value;
  }

  void skip_bits(std::uint32_t count) {
    if ((bit_offset_ + count + 7U) / 8U > data_.size() + 1U) {
      throw std::runtime_error("PNG deflate 位流截断");
    }
    bit_offset_ += count;
  }

  void align_byte() {
    bit_offset_ = (bit_offset_ + 7U) & ~std::size_t{7U};
  }

  std::uint8_t read_byte() {
    align_byte();
    const auto byte_index = bit_offset_ / 8U;
    if (byte_index >= data_.size()) {
      throw std::runtime_error("PNG deflate 数据截断");
    }
    bit_offset_ += 8U;
    return data_[byte_index];
  }

private:
  std::uint32_t read_bit() {
    if (bit_offset_ / 8U >= data_.size()) {
      throw std::runtime_error("PNG deflate 位流截断");
    }
    const auto byte = data_[bit_offset_ / 8U];
    const auto bit = static_cast<std::uint32_t>((byte >> (bit_offset_ % 8U)) & 1U);
    ++bit_offset_;
    return bit;
  }

  const std::vector<std::uint8_t>& data_;
  std::size_t bit_offset_ = 0;
};

struct HuffmanTable {
  std::array<std::uint16_t, 1U << 15U> symbols{};
  std::array<std::uint8_t, 1U << 15U> lengths{};
};

std::uint16_t reverse_bits(std::uint16_t code, std::uint8_t length) {
  std::uint16_t reversed = 0;
  for (std::uint8_t index = 0; index < length; ++index) {
    reversed = static_cast<std::uint16_t>((reversed << 1U) | (code & 1U));
    code = static_cast<std::uint16_t>(code >> 1U);
  }
  return reversed;
}

HuffmanTable build_huffman(const std::vector<std::uint8_t>& lengths) {
  std::array<std::uint16_t, 16> counts{};
  for (const auto length : lengths) {
    if (length >= counts.size()) {
      throw std::runtime_error("PNG deflate Huffman 长度非法");
    }
    if (length > 0) {
      ++counts[length];
    }
  }

  std::array<std::uint16_t, 16> next_code{};
  std::uint16_t code = 0;
  for (std::size_t bits = 1; bits < counts.size(); ++bits) {
    code = static_cast<std::uint16_t>((code + counts[bits - 1U]) << 1U);
    next_code[bits] = code;
  }

  HuffmanTable table;
  for (std::size_t symbol = 0; symbol < lengths.size(); ++symbol) {
    const auto length = lengths[symbol];
    if (length == 0) {
      continue;
    }
    const auto canonical = next_code[length]++;
    const auto reversed = reverse_bits(canonical, length);
    const auto step = 1U << length;
    for (std::uint32_t key = reversed; key < table.symbols.size(); key += step) {
      table.symbols[key] = static_cast<std::uint16_t>(symbol);
      table.lengths[key] = length;
    }
  }
  return table;
}

std::uint16_t decode_symbol(BitReader* reader, const HuffmanTable& table) {
  const auto key = reader->peek_bits(15);
  const auto length = table.lengths[key];
  if (length == 0) {
    throw std::runtime_error("PNG deflate Huffman 符号非法");
  }
  reader->skip_bits(length);
  return table.symbols[key];
}

HuffmanTable fixed_literal_table() {
  std::vector<std::uint8_t> lengths(288, 0);
  for (std::size_t symbol = 0; symbol <= 143; ++symbol) lengths[symbol] = 8;
  for (std::size_t symbol = 144; symbol <= 255; ++symbol) lengths[symbol] = 9;
  for (std::size_t symbol = 256; symbol <= 279; ++symbol) lengths[symbol] = 7;
  for (std::size_t symbol = 280; symbol <= 287; ++symbol) lengths[symbol] = 8;
  return build_huffman(lengths);
}

HuffmanTable fixed_distance_table() {
  return build_huffman(std::vector<std::uint8_t>(32, 5));
}

void decode_fixed_block(BitReader* reader, std::vector<std::uint8_t>* output) {
  static const auto literal_table = fixed_literal_table();
  static const auto distance_table = fixed_distance_table();
  static constexpr std::array<int, 29> kLengthBase = {
      3,   4,   5,   6,   7,   8,   9,   10,  11,  13,
      15,  17,  19,  23,  27,  31,  35,  43,  51,  59,
      67,  83,  99,  115, 131, 163, 195, 227, 258};
  static constexpr std::array<int, 29> kLengthExtra = {
      0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2,
      2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0};
  static constexpr std::array<int, 30> kDistanceBase = {
      1,    2,    3,    4,    5,    7,    9,    13,   17,   25,
      33,   49,   65,   97,   129,  193,  257,  385,  513,  769,
      1025, 1537, 2049, 3073, 4097, 6145, 8193, 12289, 16385, 24577};
  static constexpr std::array<int, 30> kDistanceExtra = {
      0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6,
      6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13};

  while (true) {
    const auto symbol = decode_symbol(reader, literal_table);
    if (symbol < 256U) {
      output->push_back(static_cast<std::uint8_t>(symbol));
      continue;
    }
    if (symbol == 256U) {
      return;
    }
    if (symbol > 285U) {
      throw std::runtime_error("PNG deflate length 符号非法");
    }
    const auto length_index = static_cast<std::size_t>(symbol - 257U);
    int length = kLengthBase[length_index];
    if (kLengthExtra[length_index] > 0) {
      length += static_cast<int>(reader->read_bits(kLengthExtra[length_index]));
    }
    const auto distance_symbol = decode_symbol(reader, distance_table);
    if (distance_symbol >= kDistanceBase.size()) {
      throw std::runtime_error("PNG deflate distance 符号非法");
    }
    int distance = kDistanceBase[distance_symbol];
    if (kDistanceExtra[distance_symbol] > 0) {
      distance += static_cast<int>(reader->read_bits(kDistanceExtra[distance_symbol]));
    }
    if (distance <= 0 || static_cast<std::size_t>(distance) > output->size()) {
      throw std::runtime_error("PNG deflate 回溯距离非法");
    }
    for (int index = 0; index < length; ++index) {
      output->push_back((*output)[output->size() - static_cast<std::size_t>(distance)]);
    }
  }
}

void decode_stored_block(BitReader* reader, std::vector<std::uint8_t>* output) {
  reader->align_byte();
  const std::uint16_t len = static_cast<std::uint16_t>(
      reader->read_byte() | (static_cast<std::uint16_t>(reader->read_byte()) << 8U));
  const std::uint16_t nlen = static_cast<std::uint16_t>(
      reader->read_byte() | (static_cast<std::uint16_t>(reader->read_byte()) << 8U));
  if (static_cast<std::uint16_t>(~len) != nlen) {
    throw std::runtime_error("PNG deflate stored block LEN 校验失败");
  }
  for (std::uint16_t index = 0; index < len; ++index) {
    output->push_back(reader->read_byte());
  }
}

std::vector<std::uint8_t> zlib_decompress(const std::vector<std::uint8_t>& input,
                                          std::size_t expected_size) {
  if (input.size() < 6U) {
    throw std::runtime_error("PNG zlib 数据过短");
  }
  const auto cmf = input[0];
  const auto flg = input[1];
  if ((cmf & 0x0FU) != 8U || (((static_cast<int>(cmf) << 8) + flg) % 31) != 0) {
    throw std::runtime_error("PNG zlib header 非法");
  }
  if ((flg & 0x20U) != 0) {
    throw std::runtime_error("PNG zlib 预置字典不支持");
  }

  std::vector<std::uint8_t> deflate(input.begin() + 2, input.end() - 4);
  BitReader reader(deflate);
  std::vector<std::uint8_t> output;
  output.reserve(expected_size);
  bool final_block = false;
  while (!final_block) {
    final_block = reader.read_bits(1) != 0;
    const auto block_type = reader.read_bits(2);
    if (block_type == 0U) {
      decode_stored_block(&reader, &output);
    } else if (block_type == 1U) {
      decode_fixed_block(&reader, &output);
    } else if (block_type == 2U) {
      throw std::runtime_error("PNG deflate dynamic Huffman 不支持");
    } else {
      throw std::runtime_error("PNG deflate block type 非法");
    }
    if (output.size() > expected_size) {
      throw std::runtime_error("PNG zlib 解压超过预期长度");
    }
  }
  if (output.size() != expected_size) {
    std::ostringstream message;
    message << "PNG zlib 解压长度不匹配 size=" << output.size()
            << " expected=" << expected_size;
    throw std::runtime_error(message.str());
  }
  return output;
}

std::vector<std::uint8_t> unfilter_png(const std::vector<std::uint8_t>& raw,
                                       std::uint32_t width,
                                       std::uint32_t height,
                                       std::uint32_t channels) {
  const std::size_t stride = static_cast<std::size_t>(width) * channels;
  const std::size_t expected = (stride + 1U) * height;
  if (raw.size() != expected) {
    throw std::runtime_error("PNG 解压长度不匹配");
  }

  std::vector<std::uint8_t> pixels(static_cast<std::size_t>(height) * stride);
  std::vector<std::uint8_t> previous(stride, 0);
  std::size_t offset = 0;
  for (std::uint32_t row = 0; row < height; ++row) {
    const std::uint8_t filter = raw[offset++];
    std::vector<std::uint8_t> current(raw.begin() + static_cast<std::ptrdiff_t>(offset),
                                      raw.begin() + static_cast<std::ptrdiff_t>(offset + stride));
    offset += stride;
    for (std::size_t index = 0; index < stride; ++index) {
      const std::uint8_t left = index >= channels ? current[index - channels] : 0;
      const std::uint8_t up = previous[index];
      const std::uint8_t upper_left = index >= channels ? previous[index - channels] : 0;
      switch (filter) {
        case 0:
          break;
        case 1:
          current[index] = static_cast<std::uint8_t>(current[index] + left);
          break;
        case 2:
          current[index] = static_cast<std::uint8_t>(current[index] + up);
          break;
        case 3:
          current[index] =
              static_cast<std::uint8_t>(current[index] +
                                        static_cast<std::uint8_t>(
                                            (static_cast<std::uint16_t>(left) + up) / 2U));
          break;
        case 4:
          current[index] = static_cast<std::uint8_t>(
              current[index] + paeth(left, up, upper_left));
          break;
        default:
          throw std::runtime_error("PNG filter 不支持");
      }
    }
    std::copy(current.begin(), current.end(),
              pixels.begin() + static_cast<std::ptrdiff_t>(row * stride));
    previous = std::move(current);
  }
  return pixels;
}

}  // namespace

bool read_png_image(const std::string& path, PngImage* out_image, std::string* error) {
  if (out_image == nullptr) {
    if (error != nullptr) {
      *error = "out_image is null";
    }
    return false;
  }

  try {
    const auto data = read_file(path);
    if (data.size() < kPngSignature.size() ||
        !std::equal(kPngSignature.begin(), kPngSignature.end(), data.begin())) {
      throw std::runtime_error("不是 PNG 文件: " + path);
    }

    std::uint32_t width = 0;
    std::uint32_t height = 0;
    int bit_depth = -1;
    int color_type = -1;
    int interlace = -1;
    std::vector<std::uint8_t> compressed;
    std::size_t offset = kPngSignature.size();
    while (offset + 8U <= data.size()) {
      const std::uint32_t length = read_be32(data, offset);
      const std::size_t chunk_type_offset = offset + 4U;
      const std::size_t chunk_data_offset = offset + 8U;
      const std::size_t chunk_end = chunk_data_offset + length;
      if (chunk_end + 4U > data.size()) {
        throw std::runtime_error("PNG chunk 截断: " + path);
      }
      const std::string type(reinterpret_cast<const char*>(data.data() + chunk_type_offset), 4);
      if (type == "IHDR") {
        if (length != 13U) {
          throw std::runtime_error("PNG IHDR 长度无效: " + path);
        }
        width = read_be32(data, chunk_data_offset);
        height = read_be32(data, chunk_data_offset + 4U);
        bit_depth = data[chunk_data_offset + 8U];
        color_type = data[chunk_data_offset + 9U];
        interlace = data[chunk_data_offset + 12U];
      } else if (type == "IDAT") {
        compressed.insert(compressed.end(),
                          data.begin() + static_cast<std::ptrdiff_t>(chunk_data_offset),
                          data.begin() + static_cast<std::ptrdiff_t>(chunk_end));
      } else if (type == "IEND") {
        break;
      }
      offset = chunk_end + 4U;
    }

    if (width == 0 || height == 0) {
      throw std::runtime_error("PNG 缺少有效 IHDR: " + path);
    }
    if (bit_depth != 8 || interlace != 0) {
      throw std::runtime_error("仅支持 8bit 非隔行 PNG: " + path);
    }
    std::uint32_t channels = 0;
    if (color_type == 0) {
      channels = 1;
    } else if (color_type == 2) {
      channels = 3;
    } else {
      throw std::runtime_error("仅支持灰度或 RGB PNG: " + path);
    }
    const std::size_t raw_size =
        (static_cast<std::size_t>(width) * channels + 1U) * height;
    const auto raw = zlib_decompress(compressed, raw_size);

    out_image->width = width;
    out_image->height = height;
    out_image->channels = channels;
    out_image->pixels = unfilter_png(raw, width, height, channels);
    return true;
  } catch (const std::exception& exc) {
    if (error != nullptr) {
      *error = exc.what();
    }
    return false;
  }
}

}  // namespace seat_aoi
