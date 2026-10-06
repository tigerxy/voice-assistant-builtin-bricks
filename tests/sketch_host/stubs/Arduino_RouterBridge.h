// Fake RouterBridge: records the RPCs the sketch provides so the test can call them.
#pragma once
#include <functional>
#include <map>
#include <string>
#include "Arduino.h"

class BridgeClass {
 public:
  std::map<std::string, std::function<void(int)>> handlers;
  void begin() {}
  void provide(const char* name, void (*fn)(int)) { handlers[name] = fn; }
  void call(const std::string& name, int arg) { handlers.at(name)(arg); }
};

inline BridgeClass Bridge;
