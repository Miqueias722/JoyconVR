#include "JoyconConfig.h"

#include <fstream>
#include <nlohmann/json.hpp>

using json = nlohmann::json;

namespace
{
VrBooleanAction ParseAction(const std::string& value)
{
    if (value == "system") return VrBooleanAction::System;
    if (value == "menu") return VrBooleanAction::Menu;
    if (value == "a") return VrBooleanAction::A;
    if (value == "b") return VrBooleanAction::B;
    if (value == "x") return VrBooleanAction::X;
    if (value == "y") return VrBooleanAction::Y;
    if (value == "grip") return VrBooleanAction::Grip;
    if (value == "trigger_click") return VrBooleanAction::TriggerClick;
    if (value == "thumbstick_click") return VrBooleanAction::ThumbstickClick;
    return VrBooleanAction::Unused; // "none", "unused", "unuse", anything unknown
}

JoyconHand ParseHand(const json& j, JoyconHand fallback)
{
    if (!j.is_string()) return fallback;
    const std::string s = j.get<std::string>();
    if (s == "left" || s == "esquerdo" || s == "esquerda") return JoyconHand::Left;
    if (s == "right" || s == "direito" || s == "direita") return JoyconHand::Right;
    return fallback;
}

void ApplyHandConfig(const json& handJson, HandConfig& hand)
{
    if (!handJson.is_object())
    {
        return;
    }

    hand.invertX = handJson.value("invert_x", hand.invertX);
    hand.invertY = handJson.value("invert_y", hand.invertY);
    hand.analogDeadzoneNormalized = handJson.value("analog_deadzone", hand.analogDeadzoneNormalized);
    hand.forwardAxis = handJson.value("forward_axis", hand.forwardAxis);
    hand.flipUp = handJson.value("flip_up", hand.flipUp);
    hand.extendButton = handJson.value("extend_button", hand.extendButton);
    hand.extendDistance = handJson.value("extend_distance", hand.extendDistance);

    const auto bindingIt = handJson.find("button_bindings");
    if (bindingIt == handJson.end() || !bindingIt->is_object())
    {
        return;
    }

    hand.buttonBindings.clear();
    for (auto it = bindingIt->begin(); it != bindingIt->end(); ++it)
    {
        if (it.value().is_string())
        {
            hand.buttonBindings[it.key()] = ParseAction(it.value().get<std::string>());
        }
    }
}

void ApplyButtonRef(const json& j, ButtonRef& ref)
{
    if (!j.is_object()) return;
    ref.hand = ParseHand(j.value("hand", json()), ref.hand);
    ref.button = j.value("button", ref.button);
}
} // namespace

DriverConfig JoyconConfig::Default()
{
    DriverConfig config;

    config.left.buttonBindings = {
        {"minus", VrBooleanAction::System},
        {"zl", VrBooleanAction::TriggerClick},
        {"l", VrBooleanAction::Grip},
        {"stick", VrBooleanAction::ThumbstickClick},
        {"up", VrBooleanAction::Y},
        {"down", VrBooleanAction::A},
        {"left", VrBooleanAction::X},
        {"right", VrBooleanAction::B},
    };
    config.left.invertY = true;

    config.right.buttonBindings = {
        {"plus", VrBooleanAction::System},
        {"home", VrBooleanAction::Menu},
        {"zr", VrBooleanAction::TriggerClick},
        {"r", VrBooleanAction::Grip},
        {"stick", VrBooleanAction::ThumbstickClick},
        {"x", VrBooleanAction::Y},
        {"b", VrBooleanAction::A},
        {"y", VrBooleanAction::X},
        {"a", VrBooleanAction::B},
    };
    config.right.invertY = true;

    return config;
}

DriverConfig JoyconConfig::LoadFromFile(const std::string& configPath, std::string* error)
{
    DriverConfig config = Default();

    std::ifstream in(configPath);
    if (!in.good())
    {
        if (error) *error = "config file not found: " + configPath;
        return config;
    }

    json j;
    try
    {
        j = json::parse(in, nullptr, true, /*ignore_comments*/ true);
    }
    catch (const std::exception& e)
    {
        if (error) *error = std::string("config.json is invalid, using defaults: ") + e.what();
        return config;
    }

    config.joyConCalPath = j.value("joyconcal_path", config.joyConCalPath);

    if (auto it = j.find("hands"); it != j.end() && it->is_object())
    {
        ApplyHandConfig(it->value("left", json::object()), config.left);
        ApplyHandConfig(it->value("right", json::object()), config.right);
    }

    if (auto it = j.find("hmd"); it != j.end() && it->is_object())
    {
        HmdConfig& h = config.hmd;
        h.enabled = it->value("enabled", h.enabled);
        h.windowX = it->value("window_x", h.windowX);
        h.windowY = it->value("window_y", h.windowY);
        h.windowWidth = it->value("window_width", h.windowWidth);
        h.windowHeight = it->value("window_height", h.windowHeight);
        h.renderWidth = it->value("render_width", h.renderWidth);
        h.renderHeight = it->value("render_height", h.renderHeight);
        h.verticalFovDeg = it->value("fov_vertical_deg", h.verticalFovDeg);
        h.refreshHz = it->value("refresh_hz", h.refreshHz);
        h.headHeight = it->value("head_height_m", h.headHeight);
    }

    if (auto it = j.find("look"); it != j.end() && it->is_object())
    {
        LookConfig& l = config.look;
        ApplyButtonRef(it->value("toggle", json()), l.toggle);
        l.stickHand = ParseHand(it->value("stick_hand", json()), l.stickHand);
        l.alwaysOn = it->value("always_on", l.alwaysOn);
        l.yawSpeedDeg = it->value("yaw_speed_deg", l.yawSpeedDeg);
        l.pitchSpeedDeg = it->value("pitch_speed_deg", l.pitchSpeedDeg);
        l.maxPitchDeg = it->value("max_pitch_deg", l.maxPitchDeg);
        l.mouseLook = it->value("mouse_look", l.mouseLook);
        l.mouseDegPerPixel = it->value("mouse_deg_per_pixel", l.mouseDegPerPixel);
    }

    if (auto it = j.find("recenter"); it != j.end() && it->is_object())
    {
        ApplyButtonRef(*it, config.recenter.button);
        config.recenter.holdSeconds = it->value("hold_seconds", config.recenter.holdSeconds);
    }

    if (auto it = j.find("arm"); it != j.end() && it->is_object())
    {
        ArmConfig& a = config.arm;
        a.shoulderX = it->value("shoulder_x", a.shoulderX);
        a.shoulderY = it->value("shoulder_y", a.shoulderY);
        a.shoulderZ = it->value("shoulder_z", a.shoulderZ);
        a.upperArm = it->value("upper_arm", a.upperArm);
        a.forearm = it->value("forearm", a.forearm);
        a.elbowFollow = it->value("elbow_follow", a.elbowFollow);
    }

    if (auto it = j.find("imu"); it != j.end() && it->is_object())
    {
        ImuConfig& m = config.imu;
        m.gyroScaleDps = it->value("gyro_scale_dps", m.gyroScaleDps);
        m.accelScaleG = it->value("accel_scale_g", m.accelScaleG);
        m.tiltCorrection = it->value("tilt_correction", m.tiltCorrection);
        m.predictionMs = it->value("prediction_ms", m.predictionMs);
    }

    if (auto it = j.find("vibration"); it != j.end() && it->is_object())
    {
        config.vibration.enabled = it->value("enabled", config.vibration.enabled);
        config.vibration.strength = it->value("strength", config.vibration.strength);
    }

    if (auto it = j.find("switch2"); it != j.end() && it->is_object())
    {
        Switch2Config& c = config.switch2;
        c.enabled = it->value("enabled", c.enabled);
        c.udpPort = it->value("udp_port", c.udpPort);
        c.gyroScaleDps = it->value("gyro_scale_dps", c.gyroScaleDps);
        c.accelScaleG = it->value("accel_scale_g", c.accelScaleG);
        c.invertX = it->value("invert_x", c.invertX);
        c.invertY = it->value("invert_y", c.invertY);
        c.analogDeadzone = it->value("analog_deadzone", c.analogDeadzone);
        for (auto [key, hand] : {std::pair<const char*, Switch2HandConfig*>{"left", &c.left}, {"right", &c.right}})
        {
            const json h = it->value(key, json::object());
            if (h.is_object())
            {
                hand->forwardAxis = h.value("forward_axis", hand->forwardAxis);
                hand->flipUp = h.value("flip_up", hand->flipUp);
            }
        }
    }

    return config;
}
