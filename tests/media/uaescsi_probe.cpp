#define main p6_cd_device_fixture_main
#include "cd_device_probe.cpp"
#undef main
#include "execio.h"

struct Mode2Provider { uint32_t calls = 0; };
static uint8_t mode2_value(uint64_t at)
{
    const uint32_t pos = at % 2352, lba = at / 2352;
    if (pos == 15) return 2;
    if (pos >= 16 && pos < 24) {
        static const uint8_t subheader[] = {1, 2, 0, 4};
        return subheader[(pos - 16) & 3];
    }
    if (lba == 16 && pos >= 24 && pos < 31) {
        static const uint8_t pvd[] = {1, 'C', 'D', '0', '0', '1', 1};
        return pvd[pos - 24];
    }
    return uint8_t(at * 19 + at / 251);
}
static bool mode2_read(const void *ctx, uint64_t at, void *out, size_t count, size_t *got)
{
    auto &p = *const_cast<Mode2Provider *>(static_cast<const Mode2Provider *>(ctx)); ++p.calls;
    if (at > 17 * UINT64_C(2352) || count > 17 * UINT64_C(2352) - at) { *got = 0; return false; }
    for (size_t n = 0; n < count; ++n) static_cast<uint8_t *>(out)[n] = mode2_value(at + n);
    *got = count; return true;
}

static int uaescsi_commands()
{
    int16_t dummy[1176]{};
    cd_audio_state bootstrap{};
    start(48000, false, bootstrap, dummy);
    audio_cda_new_buffer(&bootstrap, nullptr, -1, 0, nullptr, nullptr);

    Provider provider{2048, 8};
    Binding binding{{{1}, uint64_t(provider.stride) * provider.sectors, 1}, read, &provider};
    Layout layout{};
    layout.tracks = 1;
    layout.leadout = provider.sectors;
    layout.content_id[0] = 0x53;
    layout.track[0] = {0, 0, provider.sectors, 0, 2048, 0, Mode::Mode1_2048, 1, 4, false};
    Disc disc;
    CHECK(disc.bind(layout, &binding, 1) == Result::Ok);
    CHECK(device_bind(&disc) == Result::Ok);

    CHECK(!device_open_count() && sys_command_open(0) == 1 && device_open_count() == 1);
    CHECK(sys_command_open(0) == -1 && device_open_count() == 2);
    sys_command_close(0);
    CHECK(device_open_count() == 1);

    uint8_t data[4096];
    amigascsi command{};
    command.data = data;
    command.flags = 1;
    auto prepare = [&](uint8_t opcode, int cdb_length, int data_length) {
        memset(data, 0xa5, sizeof data);
        memset(command.cmd, 0, sizeof command.cmd);
        command.cmd[0] = opcode;
        command.cmd_len = cdb_length;
        command.len = data_length;
        command.sense_len = sizeof command.sensedata;
    };

    prepare(0x12, 6, 36);
    command.cmd[4] = 36;
    CHECK(sys_command_scsi_direct_native(0, INQ_ROMD, &command) == 0);
    CHECK(command.actual == 36 && command.cmdactual == 6 && !command.status && !command.sactual);
    CHECK(data[0] == 5 && data[1] == 0x80 && !memcmp(data + 8, "PUAE6   IMMUTABLE CD-ROM", 24));

    prepare(0x25, 10, 8);
    CHECK(sys_command_scsi_direct_native(0, INQ_ROMD, &command) == 0);
    CHECK(command.actual == 8 && command.cmdactual == 10 && data[3] == 7 && data[6] == 8 && data[7] == 0);

    prepare(0x28, 10, 2048);
    command.cmd[5] = 2;
    command.cmd[8] = 1;
    memset(data, 0xa5, sizeof data);
    CHECK(sys_command_scsi_direct_native(0, INQ_ROMD, &command) == 0);
    CHECK(command.actual == 2048 && command.cmdactual == 10);
    for (size_t n = 0; n < 2048; ++n) CHECK(data[n] == value(provider, uint64_t(2) * 2048 + n));

    const uint32_t calls = provider.calls;
    prepare(0xff, 6, sizeof data);
    CHECK(sys_command_scsi_direct_native(0, INQ_ROMD, &command) == IOERR_BadStatus);
    CHECK(!command.actual && !command.cmdactual && command.status == 2 && command.sactual == 18);
    CHECK(command.sensedata[2] == 5 && command.sensedata[12] == 0x20);
    CHECK(provider.calls == calls);
    prepare(0x2a, 10, 2048);
    CHECK(sys_command_scsi_direct_native(0, INQ_ROMD, &command) == IOERR_BadStatus && command.status == 2);
    CHECK(provider.calls == calls && filled(data, sizeof data, 0xa5));

    command.len = UINT16_MAX + 1;
    CHECK(sys_command_scsi_direct_native(0, INQ_ROMD, &command) == IOERR_BADADDRESS);
    command.len = 36;
    CHECK(sys_command_scsi_direct_native(1, INQ_ROMD, &command) == IOERR_BADADDRESS);
    CHECK(sys_command_scsi_direct_native(0, INQ_DASD, &command) == IOERR_BADADDRESS);
    CHECK(!sys_command_open_tape(0, "ignored", true));
    CHECK(sys_command_write(0, data, 0, 1) == -1);

    sys_command_close(0);
    CHECK(!device_open_count());
    CHECK(sys_command_scsi_direct_native(0, INQ_ROMD, &command) == IOERR_BADADDRESS);
    device_unbind();
    return 0;
}

static int mode2_commands()
{
    Mode2Provider provider;
    Binding binding{{{0x42}, 17 * UINT64_C(2352), 1}, mode2_read, &provider};
    Layout layout{}; layout.tracks = 1; layout.leadout = 17; layout.content_id[0] = 0x42;
    layout.track[0] = {0, 0, 17, 0, 2352, 0, Mode::Mode2Raw, 1, 4, true};
    Disc disc; CHECK(disc.bind(layout, &binding, 1) == Result::Ok);
    CHECK(device_bind(&disc) == Result::Ok && sys_command_open(0) == 1);

    uint8_t packet[12]{}, atapi[2048], scsi[2048];
    packet[0] = 0x28; packet[5] = 16; packet[8] = 1;
    AtapiState state{}; atapi_reset(&state);
    const auto reply = atapi_execute(&state, packet, atapi, sizeof atapi);
    CHECK(!reply.check_condition && reply.bytes == sizeof atapi);

    amigascsi command{}; command.data = scsi; command.flags = 1;
    memcpy(command.cmd, packet, sizeof packet); command.cmd_len = 10;
    command.len = sizeof scsi; command.sense_len = sizeof command.sensedata;
    CHECK(sys_command_scsi_direct_native(0, INQ_ROMD, &command) == 0);
    CHECK(command.actual == sizeof scsi && command.cmdactual == 10 && !command.status && !command.sactual);
    CHECK(!memcmp(atapi, scsi, sizeof atapi));
    for (unsigned n = 0; n < sizeof atapi; ++n) CHECK(atapi[n] == mode2_value(16 * UINT64_C(2352) + 24 + n));
    CHECK(atapi[0] == 1 && !memcmp(atapi + 1, "CD001", 5) && atapi[6] == 1);

    sys_command_close(0); CHECK(!device_open_count()); device_unbind();
    return 0;
}

int main()
{
    const int commands = uaescsi_commands();
    return commands ? commands : mode2_commands();
}
