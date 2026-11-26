#pragma once

#include "JoyconTypes.h"

#include <vector>

struct hid_device_;
using hid_device = struct hid_device_;

// Talks to one Joy-Con over hidapi. Not thread-safe: use it from a single thread.
class JoyconHid
{
public:
    explicit JoyconHid(JoyconHand hand);
    ~JoyconHid();

    bool Connect();
    void Disconnect();
    bool IsConnected() const { return device_ != nullptr; }

    // Drains every pending report. Updates button/stick state and appends IMU frames in order.
    bool Poll(JoyconRawState& outState, std::vector<ImuSample>& outImu);

    void SetPlayerLights(uint8_t pattern);
    void SetHomeLight(uint8_t intensity);   // 0 = off, 15 = full (right Joy-Con only)
    void Rumble(float amplitude);           // 0 = stop, 1 = strongest we allow

private:
    bool SetupDevice();
    bool SendSubcommand(uint8_t command, const uint8_t* data, size_t len, bool waitReply);
    void ParseInputReport(const uint8_t* report, size_t len, JoyconRawState& outState, std::vector<ImuSample>& outImu) const;

private:
    JoyconHand hand_;
    hid_device* device_ = nullptr;
    uint8_t packetCounter_ = 0;
};
