#pragma once

#include <array>
#include <cstdint>
#include <string>
#include <unordered_map>

enum class JoyconHand
{
    Left,
    Right,
};

enum class VrBooleanAction
{
    Unused,
    System,
    Menu,
    A,
    B,
    X,
    Y,
    Grip,
    TriggerClick,
    ThumbstickClick,
};

// One IMU frame as reported by the Joy-Con (3 frames per 0x30 report, ~5 ms apart).
struct ImuSample
{
    int16_t accel[3] = {0, 0, 0};
    int16_t gyro[3] = {0, 0, 0};
};

struct JoyconRawState
{
    bool connected = false;

    bool minus = false;
    bool plus = false;
    bool capture = false;
    bool home = false;

    bool y = false;
    bool x = false;
    bool b = false;
    bool a = false;

    bool left = false;
    bool right = false;
    bool up = false;
    bool down = false;

    bool l = false;
    bool r = false;
    bool zl = false;
    bool zr = false;

    bool sl = false;
    bool sr = false;
    bool stick = false;

    uint16_t rawStickX = 2048;
    uint16_t rawStickY = 2048;

    float normStickX = 0.0f;
    float normStickY = 0.0f;
    bool hasNormStick = false;          // true for Joy-Con 2: stick already normalized by the bridge
};

struct AnalogCalibration
{
    uint16_t xMin = 1200;
    uint16_t xCenter = 2048;
    uint16_t xMax = 2900;

    uint16_t yMin = 1200;
    uint16_t yCenter = 2048;
    uint16_t yMax = 2900;

    uint16_t deadZone = 120;
};

struct HandConfig
{
    std::unordered_map<std::string, VrBooleanAction> buttonBindings;
    bool invertX = false;
    bool invertY = false;
    float analogDeadzoneNormalized = 0.08f;

    // Motion
    std::string forwardAxis = "+x";     // sensor axis that points out of the top of the Joy-Con
    bool flipUp = false;                // flip the detected "up" direction if the hand is upside down
    std::string extendButton;           // hold to push the hand forward ("" = disabled)
    float extendDistance = 0.25f;       // metres
};

struct ButtonRef
{
    JoyconHand hand = JoyconHand::Left;
    std::string button;                 // "" = disabled
};

struct HmdConfig
{
    bool enabled = true;
    int32_t windowX = 0;
    int32_t windowY = 0;
    uint32_t windowWidth = 1920;
    uint32_t windowHeight = 1080;
    uint32_t renderWidth = 0;           // 0 = same as window
    uint32_t renderHeight = 0;
    float verticalFovDeg = 75.0f;
    float refreshHz = 60.0f;
    float headHeight = 1.65f;           // metres above the floor
};

struct LookConfig
{
    ButtonRef toggle{JoyconHand::Left, "capture"};  // short press toggles look mode
    bool alwaysOn = true;                           // stick always moves the camera (toggle turns it off)
    JoyconHand stickHand = JoyconHand::Left;        // stick that moves the camera in look mode
    float yawSpeedDeg = 140.0f;
    float pitchSpeedDeg = 90.0f;
    float maxPitchDeg = 80.0f;
    bool mouseLook = true;                          // hold middle mouse button and move the mouse
    float mouseDegPerPixel = 0.15f;
};

struct RecenterConfig
{
    ButtonRef button{JoyconHand::Left, "capture"};  // hold to recenter
    float holdSeconds = 0.8f;
};

struct ArmConfig
{
    float shoulderX = 0.18f;    // sideways from the eyes (mirrored for the left hand)
    float shoulderY = -0.20f;   // below the eyes
    float shoulderZ = 0.05f;    // behind the eyes (+Z is backwards in SteamVR)
    float upperArm = 0.28f;
    float forearm = 0.30f;
    float elbowFollow = 0.35f;  // 0 = elbow never moves, 1 = arm is a stiff stick
};

struct ImuConfig
{
    float gyroScaleDps = 0.070f;        // degrees/second per LSB (±2000 dps range)
    float accelScaleG = 0.000244f;      // g per LSB (±8 g range)
    float tiltCorrection = 1.0f;        // how fast gravity pulls pitch/roll back (0 = pure gyro)
};

struct Switch2HandConfig
{
    std::string forwardAxis = "+y";
    bool flipUp = false;
};

struct Switch2Config
{
    bool enabled = true;
    uint16_t udpPort = 27760;
    float gyroScaleDps = 0.0609f;       // SDL: 34.8 rad/s full scale
    float accelScaleG = 0.000244f;
    bool invertX = false;
    bool invertY = false;
    float analogDeadzone = 0.08f;
    Switch2HandConfig left;
    Switch2HandConfig right;
};

struct DriverConfig
{
    std::string joyConCalPath = "config/joyconcal.json";
    HandConfig left;
    HandConfig right;
    HmdConfig hmd;
    LookConfig look;
    RecenterConfig recenter;
    ArmConfig arm;
    ImuConfig imu;
    Switch2Config switch2;
};
