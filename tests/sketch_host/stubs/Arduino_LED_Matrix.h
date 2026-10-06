// Fake 13x8 LED matrix that remembers the last frame drawn (tests only).
#pragma once
#include "Arduino.h"

class ArduinoLEDMatrix {
 public:
  uint8_t frame[8 * 13] = {0};
  int grayscaleBits = 1;
  void begin() {}
  void setGrayscaleBits(int bits) { grayscaleBits = bits; }
  void clear() { memset(frame, 0, sizeof(frame)); }
  void draw(const uint8_t* f) { memcpy(frame, f, sizeof(frame)); }
};
