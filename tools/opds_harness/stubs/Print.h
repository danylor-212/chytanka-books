#pragma once
// Host stub of Arduino's Print (same as chytanka-main/test/*/stubs/Print.h).
#include <cstddef>
#include <cstdint>

class Print {
 public:
  virtual ~Print() = default;
  virtual size_t write(uint8_t) = 0;
  virtual size_t write(const uint8_t* data, size_t length) {
    size_t written = 0;
    for (; written < length && write(data[written]) != 0; ++written) {
    }
    return written;
  }
  virtual void flush() {}
};
