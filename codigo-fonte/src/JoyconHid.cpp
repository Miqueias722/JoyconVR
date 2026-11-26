#include "JoyconHid.h"
#include <cmath>
#include <algorithm>

#include <hidapi.h>

#include <cstring>

namespace
{
constexpr unsigned short kNintendoVendor = 0x057e;
constexpr unsigned short kJoyconLeftPid = 0x2006;
constexpr unsigned short kJoyconRightPid = 0x2007;
constexpr size_t kPacketSize = 49;
constexpr size_t kReportBuffer = 362;   // large enough for any Joy-Con input report

struct HidInit
{
    HidInit() { hid_init(); }
};

int16_t ReadInt16(const uint8_t* p)
{
    return static_cast<int16_t>(static_cast<uint16_t>(p[0]) | (static_cast<uint16_t>(p[1]) << 8));
}
}

JoyconHid::JoyconHid(JoyconHand hand)
    : hand_(hand)
{
    static HidInit init;
}

JoyconHid::~JoyconHid()
{
    Disconnect();
}

bool JoyconHid::Connect()
{
    if (device_ != nullptr)
    {
        return true;
    }

    const unsigned short pid = hand_ == JoyconHand::Left ? kJoyconLeftPid : kJoyconRightPid;
    struct hid_device_info* list = hid_enumerate(kNintendoVendor, pid);
    for (struct hid_device_info* cursor = list; cursor != nullptr; cursor = cursor->next)
    {
        device_ = hid_open_path(cursor->path);
        if (device_ != nullptr)
        {
            break;
        }
    }
    hid_free_enumeration(list);

    if (device_ == nullptr)
    {
        return false;
    }

    if (!SetupDevice())
    {
        Disconnect();
        return false;
    }
    return true;
}

void JoyconHid::Disconnect()
{
    if (device_ != nullptr)
    {
        hid_close(device_);
        device_ = nullptr;
    }
}

bool JoyconHid::SendSubcommand(uint8_t command, const uint8_t* data, size_t len, bool waitReply)
{
    if (device_ == nullptr)
    {
        return false;
    }

    uint8_t out[kPacketSize] = {};
    out[0] = 0x01;
    out[1] = packetCounter_;
    packetCounter_ = static_cast<uint8_t>((packetCounter_ + 1) & 0x0F);

    // Neutral rumble data
    out[2] = 0x00; out[3] = 0x01; out[4] = 0x40; out[5] = 0x40;
    out[6] = 0x00; out[7] = 0x01; out[8] = 0x40; out[9] = 0x40;
    out[10] = command;

    if (len > 0 && data != nullptr)
    {
        std::memcpy(&out[11], data, len);
    }

    if (hid_write(device_, out, sizeof(out)) < 0)
    {
        return false;
    }

    if (!waitReply)
    {
        return true;
    }

    // Wait (briefly) for the 0x21 reply to this subcommand.
    uint8_t in[kReportBuffer] = {};
    for (int i = 0; i < 10; ++i)
    {
        const int read = hid_read_timeout(device_, in, sizeof(in), 20);
        if (read < 0)
        {
            return false;
        }
        if (read > 14 && in[0] == 0x21 && in[14] == command)
        {
            return true;
        }
    }
    return true; // some clones never reply; carry on anyway
}

bool JoyconHid::SetupDevice()
{
    hid_set_nonblocking(device_, 0);

    uint8_t imuOn = 0x01;
    if (!SendSubcommand(0x40, &imuOn, 1, true))  // enable IMU (accelerometer + gyroscope)
    {
        return false;
    }
    const uint8_t vibrationOn = 0x01;
    SendSubcommand(0x48, &vibrationOn, 1, true);  // allow rumble

    uint8_t mode = 0x30;                          // standard full report, includes IMU frames
    if (!SendSubcommand(0x03, &mode, 1, true))
    {
        return false;
    }

    SetPlayerLights(hand_ == JoyconHand::Left ? 0x01 : 0x02);
    hid_set_nonblocking(device_, 1);
    return true;
}

void JoyconHid::SetPlayerLights(uint8_t pattern)
{
    SendSubcommand(0x30, &pattern, 1, false);
}

void JoyconHid::SetHomeLight(uint8_t intensity)
{
    if (hand_ != JoyconHand::Right) return;   // only the right Joy-Con has a HOME light
    const uint8_t i = static_cast<uint8_t>(std::min<int>(intensity, 15));
    // Same pattern SDL uses: one cycle at the wanted intensity, then stay there.
    const uint8_t data[4] = {0x01, static_cast<uint8_t>(i << 4), static_cast<uint8_t>(i << 4), 0x00};
    SendSubcommand(0x38, data, sizeof(data), false);
}

namespace
{
// Joy-Con rumble encoding (frequency + amplitude -> 4 bytes), as documented by dekuNukem and
// used by JoyconLib. Amplitude is capped low on purpose: strong rumble drains the battery and can
// flood the Bluetooth link.
void EncodeRumble(float lowFreq, float highFreq, float amp, uint8_t out[4])
{
    amp = std::clamp(amp, 0.0f, 0.6f);
    if (amp <= 0.0f)
    {
        out[0] = 0x00; out[1] = 0x01; out[2] = 0x40; out[3] = 0x40;
        return;
    }
    int hf = static_cast<int>(std::lround(std::log2(highFreq / 10.0f) * 32.0f));
    hf = (hf - 0x60) * 4;
    const int lf = static_cast<int>(std::lround(std::log2(lowFreq / 10.0f) * 32.0f)) - 0x40;
    float hfAmpF;
    if (amp < 0.117f) hfAmpF = (std::log2(amp * 1000.0f) * 32.0f - 0x60) / (5.0f - amp * amp) - 1.0f;
    else if (amp < 0.23f) hfAmpF = std::log2(amp * 1000.0f) * 32.0f - 0x60 - 0x5c;
    else hfAmpF = (std::log2(amp * 1000.0f) * 32.0f - 0x60) * 2.0f - 0xf6;
    const int hfAmp = std::max(0, static_cast<int>(std::lround(hfAmpF)));
    int lfAmp = static_cast<int>(std::lround(hfAmpF * 0.5f));
    const int parity = lfAmp % 2;
    if (parity > 0) --lfAmp;
    lfAmp = (lfAmp >> 1) + 0x40;
    if (parity > 0) lfAmp |= 0x8000;
    out[0] = static_cast<uint8_t>(hf & 0xFF);
    out[1] = static_cast<uint8_t>(((hf >> 8) & 0xFF) + hfAmp);
    out[2] = static_cast<uint8_t>(lf + ((lfAmp >> 8) & 0xFF));
    out[3] = static_cast<uint8_t>(lfAmp & 0xFF);
}
} // namespace

void JoyconHid::Rumble(float amplitude)
{
    if (device_ == nullptr) return;
    uint8_t out[kPacketSize] = {};
    out[0] = 0x10;                      // rumble-only output report
    out[1] = packetCounter_;
    packetCounter_ = static_cast<uint8_t>((packetCounter_ + 1) & 0x0F);
    uint8_t side[4];
    EncodeRumble(160.0f, 320.0f, amplitude, side);
    std::memcpy(&out[2], side, 4);      // left actuator bytes
    std::memcpy(&out[6], side, 4);      // right actuator bytes
    hid_write(device_, out, 10);
}

void JoyconHid::ParseInputReport(const uint8_t* report, size_t len, JoyconRawState& outState, std::vector<ImuSample>& outImu) const
{
    if (len < 12)
    {
        return;
    }

    if (!(report[0] == 0x30 || report[0] == 0x21))
    {
        return;
    }

    const uint8_t right = report[3];
    const uint8_t shared = report[4];
    const uint8_t left = report[5];

    outState.y = (right & 0x01) != 0;
    outState.x = (right & 0x02) != 0;
    outState.b = (right & 0x04) != 0;
    outState.a = (right & 0x08) != 0;
    outState.r = (right & 0x40) != 0;
    outState.zr = (right & 0x80) != 0;

    outState.minus = (shared & 0x01) != 0;
    outState.plus = (shared & 0x02) != 0;
    outState.home = (shared & 0x10) != 0;
    outState.capture = (shared & 0x20) != 0;

    outState.down = (left & 0x01) != 0;
    outState.up = (left & 0x02) != 0;
    outState.right = (left & 0x04) != 0;
    outState.left = (left & 0x08) != 0;
    outState.l = (left & 0x40) != 0;
    outState.zl = (left & 0x80) != 0;

    if (hand_ == JoyconHand::Left)
    {
        outState.sr = (left & 0x10) != 0;
        outState.sl = (left & 0x20) != 0;
        outState.stick = (shared & 0x08) != 0;   // left stick click
    }
    else
    {
        outState.sr = (right & 0x10) != 0;
        outState.sl = (right & 0x20) != 0;
        outState.stick = (shared & 0x04) != 0;   // right stick click
    }

    outState.battery = static_cast<uint8_t>(report[2] >> 4);
    const uint8_t* s = hand_ == JoyconHand::Left ? &report[6] : &report[9];
    outState.rawStickX = static_cast<uint16_t>(s[0] | ((s[1] & 0x0F) << 8));
    outState.rawStickY = static_cast<uint16_t>((s[1] >> 4) | (s[2] << 4));

    if (report[0] == 0x30 && len >= 49)
    {
        for (int frame = 0; frame < 3; ++frame)
        {
            const uint8_t* p = &report[13 + frame * 12];
            ImuSample sample;
            sample.accel[0] = ReadInt16(p + 0);
            sample.accel[1] = ReadInt16(p + 2);
            sample.accel[2] = ReadInt16(p + 4);
            sample.gyro[0] = ReadInt16(p + 6);
            sample.gyro[1] = ReadInt16(p + 8);
            sample.gyro[2] = ReadInt16(p + 10);
            sample.hasTimer = true;
            sample.timer = report[1];
            sample.frame = static_cast<uint8_t>(frame);
            outImu.push_back(sample);
        }
    }
}

bool JoyconHid::Poll(JoyconRawState& outState, std::vector<ImuSample>& outImu)
{
    if (device_ == nullptr)
    {
        outState.connected = false;
        return false;
    }

    uint8_t in[kReportBuffer] = {};
    for (int guard = 0; guard < 64; ++guard)
    {
        const int read = hid_read(device_, in, sizeof(in));
        if (read < 0)
        {
            Disconnect();
            outState.connected = false;
            return false;
        }
        if (read == 0)
        {
            break;
        }
        ParseInputReport(in, static_cast<size_t>(read), outState, outImu);
    }

    outState.connected = true;
    return true;
}
