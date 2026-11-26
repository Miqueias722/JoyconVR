#pragma once

// Packet sent by joyconvr_switch2.py (Joy-Con 2 BLE bridge) to the driver over UDP 127.0.0.1.
// All little endian, 40 bytes:
//   0  char[4]  magic "JC2V"
//   4  u8       version (1)
//   5  u8       hand (0 = left, 1 = right)
//   6  u8       connected (1/0)
//   7  u8       source: 0 = Joy-Con 2 (Switch 2), 1 = Joy-Con 1 read by the app (raw Switch 1 IMU)
//   8  u32      Switch 2 button bitmask (as reported by the controller)
//  12  f32      stick X, normalized -1..1 (calibrated with the controller's own calibration)
//  16  f32      stick Y, normalized -1..1, +1 = up
//  20  i16[3]   accelerometer (raw, ~4096 per g)
//  26  i16[3]   gyroscope (raw)
//  32  u32      sensor timestamp (microseconds)
//  36  u32      sequence number

#include "JoyconTypes.h"

#include <cstdint>
#include <cstring>

struct Switch2Packet
{
    JoyconHand hand = JoyconHand::Left;
    bool connected = false;
    bool joycon1 = false;
    uint32_t buttons = 0;
    float stickX = 0.0f;
    float stickY = 0.0f;
    ImuSample imu;
    uint32_t timestampUs = 0;
    uint32_t sequence = 0;
};

namespace switch2
{
constexpr size_t kPacketSize = 40;

// Button bits (Switch 2 controller bitmask)
constexpr uint32_t Y = 0x00000001, X = 0x00000002, B = 0x00000004, A = 0x00000008;
constexpr uint32_t SR_R = 0x00000010, SL_R = 0x00000020, R = 0x00000040, ZR = 0x00000080;
constexpr uint32_t MINUS = 0x00000100, PLUS = 0x00000200, R_STICK = 0x00000400, L_STICK = 0x00000800;
constexpr uint32_t HOME = 0x00001000, CAPTURE = 0x00002000, CHAT = 0x00004000;
constexpr uint32_t DOWN = 0x00010000, UP = 0x00020000, RIGHT = 0x00040000, LEFT = 0x00080000;
constexpr uint32_t SR_L = 0x00100000, SL_L = 0x00200000, L = 0x00400000, ZL = 0x00800000;

inline bool Parse(const uint8_t* d, size_t len, Switch2Packet& out)
{
    if (len < kPacketSize || std::memcmp(d, "JC2V", 4) != 0 || d[4] != 1 || d[5] > 1)
    {
        return false;
    }
    auto u32 = [&](size_t o) { uint32_t v; std::memcpy(&v, d + o, 4); return v; };
    auto i16 = [&](size_t o) { int16_t v; std::memcpy(&v, d + o, 2); return v; };
    auto f32 = [&](size_t o) { float v; std::memcpy(&v, d + o, 4); return v; };

    out.hand = d[5] == 0 ? JoyconHand::Left : JoyconHand::Right;
    out.connected = d[6] != 0;
    out.joycon1 = d[7] == 1;
    out.buttons = u32(8);
    out.stickX = f32(12);
    out.stickY = f32(16);
    for (int i = 0; i < 3; ++i)
    {
        out.imu.accel[i] = i16(20 + 2 * i);
        out.imu.gyro[i] = i16(26 + 2 * i);
    }
    out.timestampUs = u32(32);
    out.sequence = u32(36);
    return true;
}

// Fills the hand-agnostic Joy-Con state used by the rest of the driver.
inline void ToRawState(const Switch2Packet& p, JoyconRawState& s)
{
    const uint32_t b = p.buttons;
    const bool left = p.hand == JoyconHand::Left;
    s.connected = p.connected;

    s.y = b & Y; s.x = b & X; s.b = b & B; s.a = b & A;
    s.r = b & R; s.zr = b & ZR;
    s.minus = b & MINUS; s.plus = b & PLUS;
    s.home = b & HOME; s.capture = b & CAPTURE;
    s.down = b & DOWN; s.up = b & UP; s.right = b & RIGHT; s.left = b & LEFT;
    s.l = b & L; s.zl = b & ZL;
    s.sl = left ? (b & SL_L) : (b & SL_R);
    s.sr = left ? (b & SR_L) : (b & SR_R);
    s.stick = left ? (b & L_STICK) : (b & R_STICK);
    // The right Joy-Con 2 has a "C" (chat) button; expose it as "capture" so the
    // look/recenter button can be set to the right hand too.
    if (!left && (b & CHAT)) s.capture = true;

    s.hasNormStick = true;
    s.normStickX = p.stickX;
    s.normStickY = p.stickY;
}
} // namespace switch2
