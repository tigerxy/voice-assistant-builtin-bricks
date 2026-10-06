// Minimal Arduino core for compiling sketch.ino on a normal computer (tests only).
#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstring>

// The test harness controls the clock
extern unsigned long fakeMillis;
inline unsigned long millis() { return fakeMillis; }
inline unsigned long micros() { return fakeMillis * 1000UL; }
inline void delay(unsigned long ms) { fakeMillis += ms; }

inline void randomSeed(unsigned long seed) { srand((unsigned)seed); }
inline long random(long max) { return max <= 0 ? 0 : rand() % max; }
inline long random(long min, long max) { return max <= min ? min : min + rand() % (max - min); }

template <typename T, typename L, typename H>
inline T constrain(T x, L lo, H hi) { return x < (T)lo ? (T)lo : (x > (T)hi ? (T)hi : x); }
using std::max;
using std::min;
