// Runs the real sketch.ino on a normal computer and prints the LED frames for
// a series of scenarios. test_sketch.py compiles this and checks the output.
//
// Output format:
//   PROVIDES name1,name2,...
//   FRAME <scenario>
//   <8 lines of 13 digits, brightness 0-7>
#include "Arduino.h"
unsigned long fakeMillis = 0;

#include "../../sketch/sketch.ino"

#include <cstdio>

static void dump(const char* name) {
  printf("FRAME %s\n", name);
  for (int r = 0; r < 8; r++) {
    for (int c = 0; c < 13; c++) printf("%d", matrix.frame[r * 13 + c]);
    printf("\n");
  }
}

static void step(unsigned long ms = 30) {
  fakeMillis += ms;
  loop();
  fakeMillis -= 30;  // loop() calls delay(30); keep the clock where the test put it
}

int main() {
  fakeMillis = 0;
  setup();

  printf("PROVIDES ");
  bool first = true;
  for (auto& kv : Bridge.handlers) {
    printf("%s%s", first ? "" : ",", kv.first.c_str());
    first = false;
  }
  printf("\n");

  // --- Idle, awake: eyes open (the first loop starts a blink, so move past it)
  Bridge.call("set_state", 0);
  Bridge.call("set_idle_mode", 0);
  step();                  // starts the first blink (setup() already took 1 s)
  fakeMillis += 400;       // the blink is over
  nextBlink = fakeMillis + 100000; nextLook = fakeMillis + 100000; lookDir = 0;
  step(0);
  dump("idle_awake_center");

  lookDir = -1; step(0); dump("idle_awake_left");
  lookDir = 1;  step(0); dump("idle_awake_right");

  // --- Blink: force the blink timer to fire
  nextBlink = fakeMillis;
  step(0);
  dump("idle_blink");
  fakeMillis += 200;
  nextBlink = fakeMillis + 100000;
  step(0);
  dump("idle_after_blink");

  // --- Sleeping face, sampled at three moments of the "z" animation
  Bridge.call("set_idle_mode", 1);
  zStart = 0;
  step(0); dump("sleep_t0");                     // "z" just appeared
  fakeMillis += 1000; step(0); dump("sleep_t1"); // one row higher
  fakeMillis += 2500; step(0); dump("sleep_gap"); // pause between two "z"s

  // --- Idle off
  Bridge.call("set_idle_mode", 2);
  step(0);
  dump("idle_off");
  Bridge.call("set_idle_mode", 0);

  // --- Listening scanner
  Bridge.call("set_state", 1);
  step(0);
  dump("listening");

  // --- Speaking waves
  Bridge.call("set_state", 3);
  for (int i = 0; i < 5; i++) step(0);
  dump("speaking");

  // --- Every emotion while speaking
  const char* names[] = {"heart", "happy", "sad", "surprised", "wink", "angry", "confused", "star"};
  for (int e = 0; e < NUM_EMOTIONS; e++) {
    Bridge.call("show_emotion", e);
    step(0);
    char label[64];
    snprintf(label, sizeof(label), "emotion_%s", e < 8 ? names[e] : "extra");
    dump(label);
  }

  // --- Emotion survives "processing" and "speaking", clears on "listening"
  Bridge.call("show_emotion", 0);
  Bridge.call("set_state", 2); step(0); dump("emotion_during_processing");
  Bridge.call("set_state", 1); step(0); dump("emotion_cleared_by_listening");

  // --- Emotion clears when the assistant goes idle
  Bridge.call("set_state", 3);
  Bridge.call("show_emotion", 0);
  Bridge.call("set_state", 0);
  nextBlink = fakeMillis + 100000; blinkEnd = 0; step(0);
  dump("emotion_cleared_by_idle");

  // --- Invalid emotion index shows nothing special
  Bridge.call("set_state", 3);
  Bridge.call("show_emotion", 99);
  step(0);
  dump("emotion_invalid");

  printf("GRAYSCALE %d\n", matrix.grayscaleBits);
  return 0;
}
