#include "cd_subchannel.h"
#include <array>
#include <iomanip>
#include <iostream>
int main()
{
 uint32_t zero, one, end, number, lba, pause, control, adr, stored, capacity, null;
 while (std::cin >> zero >> one >> end >> number >> lba >> pause >> control >> adr >> stored >> capacity >> null) {
  p6_cd::Track t{};
  t.index0 = zero; t.index1 = one; t.end = end; t.mode = p6_cd::Mode::Mode1Raw;
  t.adr = uint8_t(adr); t.control = uint8_t(control); t.stored_pregap = stored != 0;
  std::array<uint8_t, 98> out; out.fill(0xcc);
  auto result = p6_cd::synth_subchannel(t, number, lba, pause != 0,
                                     null ? nullptr : out.data() + 1, capacity);
  std::cout << std::dec << int(result) << ' ' << std::hex << std::setfill('0');
  for (auto byte : out) std::cout << std::setw(2) << unsigned(byte);
  std::cout << '\n';
 }
 return std::cin.eof() ? 0 : 1;
}
