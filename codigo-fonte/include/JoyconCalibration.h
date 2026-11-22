#pragma once

#include "JoyconTypes.h"

#include <optional>
#include <string>

class JoyconCalibration
{
public:
    static std::optional<AnalogCalibration> LoadFromJoyConCalJson(
        const std::string& filePath,
        JoyconHand hand);

    static std::array<float, 2> Apply(
        const AnalogCalibration& calibration,
        uint16_t rawX,
        uint16_t rawY,
        bool invertX,
        bool invertY,
        float extraDeadzoneNormalized);
};
