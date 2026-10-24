#include "sysconfig.h"
#include "sysdeps.h"
#include "flashrom.h"
#include "cd32_eeprom.h"
#include <array>
#include <vector>
#include <cassert>
#include <cstring>

struct Chip {
 std::array<uint8_t, 1024> memory{};
 void *chip = p6_cd32_eeprom_new(memory.data());
 Chip() { assert(chip); }
 ~Chip() { eeprom_free(chip); }
 p6_cd32_eeprom_state state() {
  p6_cd32_eeprom_state s{};
  assert(p6_cd32_eeprom_get(chip, &s)); return s;
 }
};
struct Edge { int line, level, result; };
struct Bus {
 Chip chip;
 std::vector<Edge> trace;
 int set(int line, int level) {
  int result = eeprom_i2c_set(chip.chip, line, level);
  trace.push_back({line, level, result}); return result;
 }
 void start() { set(1, 0); set(0, 1); set(1, 1); set(0, 0); }
 void stop() { set(1, 0); set(0, 0); set(1, 1); set(0, 1); }
 int bit(int value) { set(1, 0); set(0, value); return set(1, 1); }
 void write(int value) {
  for (int i = 7; i >= 0; --i) bit((value >> i) & 1);
  assert(bit(1) == 0); // Device ACK on the real ninth rising edge.
 }
 void address(int address) {
  start(); write(0xa0 | ((address >> 8) << 1)); write(address & 255);
 }
 int read(bool last) {
  int value = 0;
  for (int i = 0; i < 8; ++i) value = (value << 1) | bit(1);
  bit(last ? 1 : 0); return value;
 }
 void exercise() {
  address(0x3ff); write(0xa5); write(0x5a); stop();
  assert(chip.memory[0x3ff] == 0xa5 && chip.memory[0x3f0] == 0x5a);
  assert(chip.memory[0] == 0); // Writes wrap inside the 16-byte page.
  address(0); write(0x9d); stop();
  address(0x3ff); start(); write(0xa7); // Repeated START keeps the word address.
  assert(read(false) == 0xa5 && read(true) == 0x9d); stop(); // Read wrap at 1024.
  address(0); stop(); start(); write(0xa1);
  assert(read(true) == 0x9d); stop();
  for (int i = 0; i < 1024; ++i)
   assert(chip.memory[i] == (i == 0 ? 0x9d : i == 0x3ff ? 0xa5 : i == 0x3f0 ? 0x5a : 0));
 }
};
static void equal(const p6_cd32_eeprom_state &a, const p6_cd32_eeprom_state &b)
{
 static_assert(sizeof a == 1060, "Semantic view has no hidden padding");
 assert(memcmp(&a, &b, sizeof a) == 0);
}
static void restore()
{
 Bus source; source.exercise();
 Chip prefix;
 bool byte = false, ack = false, read = false, pending = false;
 for (size_t cut = 0; cut <= source.trace.size(); ++cut) {
  auto saved = prefix.state();
  byte |= saved.state == 4; ack |= saved.state == 9;
  read |= saved.state == 14; pending |= saved.write_offset >= 0;
  Chip fresh; fresh.memory.fill(0xee);
  assert(p6_cd32_eeprom_set(fresh.chip, &saved)); equal(saved, fresh.state());
  for (size_t i = cut; i < source.trace.size(); ++i) {
   const auto &e = source.trace[i];
   assert(eeprom_i2c_set(fresh.chip, e.line, e.level) == e.result);
  }
  equal(source.chip.state(), fresh.state());
  if (cut < source.trace.size()) {
   const auto &e = source.trace[cut];
   assert(eeprom_i2c_set(prefix.chip, e.line, e.level) == e.result);
  }
 }
 assert(byte && ack && read && pending);
}
static void reject()
{
 Bus bus; bus.address(0x3ff); bus.write(0x55);
 auto before = bus.chip.state();
 auto reject = [&](p6_cd32_eeprom_state bad) {
  bad.memory[5] ^= 0xff;
  assert(!p6_cd32_eeprom_set(bus.chip.chip, &bad)); equal(before, bus.chip.state());
 };
 auto bad = before; bad.state = 20; reject(bad);
 bad = before; bad.estate = 3; reject(bad);
 bad = before; bad.last_data = 2; reject(bad);
 bad = before; bad.last_clock = 2; reject(bad);
 bad = before; bad.device_out = 2; reject(bad);
 bad = before; bad.buffer = 256; reject(bad);
 for (int value : {-2, 256}) { bad = before; bad.current_addr = value; reject(bad); }
 for (int value : {-1, 1024}) { bad = before; bad.eeprom_addr = value; reject(bad); }
 for (int value : {-2, 1024}) { bad = before; bad.write_offset = value; reject(bad); }
 assert(!p6_cd32_eeprom_set(bus.chip.chip, nullptr)); equal(before, bus.chip.state());
 assert(!p6_cd32_eeprom_new(nullptr));
 assert(!p6_cd32_eeprom_get(nullptr, &before));
 assert(!p6_cd32_eeprom_get(bus.chip.chip, nullptr));
 Chip memory;
 void *wrong = eeprom_new(memory.memory.data(), 512, nullptr);
 assert(wrong && !p6_cd32_eeprom_get(wrong, &bad));
 assert(!p6_cd32_eeprom_set(wrong, &before)); eeprom_free(wrong);
 // Refusal also preserves the next write's pending page address and ACK phase.
 bus.write(0x66); bus.stop();
 assert(bus.chip.memory[0x3ff] == 0x55 && bus.chip.memory[0x3f0] == 0x66);
}
int main(int argc, char **argv)
{
 assert(argc == 2);
 if (!strcmp(argv[1], "protocol")) { Bus bus; bus.exercise(); }
 else if (!strcmp(argv[1], "restore")) restore();
 else { assert(!strcmp(argv[1], "reject")); reject(); }
}
