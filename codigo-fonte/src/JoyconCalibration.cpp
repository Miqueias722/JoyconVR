#include "JoyconCalibration.h"

#include <algorithm>
#include <cmath>
#include <fstream>
#include <nlohmann/json.hpp>

using json = nlohmann::json;

namespace
{
std::optional<AnalogCalibration> ParseFromNode(const json& node)
{
    if (!node.is_object())
    {
        return std::nullopt;
    }

    AnalogCalibration c;
    c.xMin = node.value("x_min", c.xMin);
    c.xCenter = node.value("x_center", c.xCenter);
    c.xMax = node.value("x_max", c.xMax);
    c.yMin = node.value("y_min", c.yMin);
    c.yCenter = node.value("y_center", c.yCenter);
    c.yMax = node.value("y_max", c.yMax);
    c.deadZone = node.value("deadzone", c.deadZone);
    return c;
}

float NormalizeAxis(uint16_t raw, uint16_t min, uint16_t center, uint16_t max)
{
    if (raw >= center)
    {
        const float denom = std::max(1.0f, static_cast<float>(max - center));
        return std::clamp((static_cast<float>(raw) - static_cast<float>(center)) / denom, 0.0f, 1.0f);
    }

    const float denom = std::max(1.0f, static_cast<float>(center - min));
    return std::clamp((static_cast<float>(raw) - static_cast<float>(center)) / denom, -1.0f, 0.0f);
}
}

std::optional<AnalogCalibration> JoyconCalibration::LoadFromJoyConCalJson(
    const std::string& filePath,
    JoyconHand hand)
{
    std::ifstream in(filePath);
    if (!in.good())
    {
        return std::nullopt;
    }

    json j;
    in >> j;

    const char* handName = hand == JoyconHand::Left ? "left" : "right";
    auto direct = j.find(handName);
    if (direct != j.end())
    {
        if (auto parsed = ParseFromNode(*direct))
        {
            return parsed;
        }
    }

    auto hands = j.find("hands");
    if (hands != j.end() && hands->is_object())
    {
        auto node = hands->find(handName);
        if (node != hands->end())
        {
            if (auto parsed = ParseFromNode(*node))
            {
                return parsed;
            }
        }
    }

    return std::nullopt;
}

std::array<float, 2> JoyconCalibration::Apply(
    const AnalogCalibration& calibration,
    uint16_t rawX,
    uint16_t rawY,
    bool invertX,
    bool invertY,
    float extraDeadzoneNormalized)
{
    float x = NormalizeAxis(rawX, calibration.xMin, calibration.xCenter, calibration.xMax);
    float y = NormalizeAxis(rawY, calibration.yMin, calibration.yCenter, calibration.yMax);

    const float radial = std::sqrt(x * x + y * y);
    const float cDeadzone = std::clamp(static_cast<float>(calibration.deadZone) / 2048.0f, 0.0f, 0.45f);
    const float totalDeadzone = std::clamp(cDeadzone + std::max(0.0f, extraDeadzoneNormalized), 0.0f, 0.9f);

    if (radial < totalDeadzone)
    {
        x = 0.0f;
        y = 0.0f;
    }
    else if (radial > 0.0001f)
    {
        const float normalized = (radial - totalDeadzone) / (1.0f - totalDeadzone);
        const float scale = std::clamp(normalized / radial, 0.0f, 1.0f);
        x *= scale;
        y *= scale;
    }

    if (invertX)
    {
        x = -x;
    }
    if (invertY)
    {
        y = -y;
    }

    return {std::clamp(x, -1.0f, 1.0f), std::clamp(y, -1.0f, 1.0f)};
}
