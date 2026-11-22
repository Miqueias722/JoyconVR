// JoyconVR - Joy-Con motion controllers + a virtual "monitor" headset for SteamVR.
// Based on JoyconInputOpenVR by LogicCuteGuy (MIT).

#include "JoyconCalibration.h"
#include "JoyconConfig.h"
#include "JoyconHid.h"
#include "JoyconTypes.h"
#include "MotionTracker.h"
#include "Switch2Packet.h"

#include <openvr_driver.h>

#ifdef _WIN32
#include <winsock2.h>
#include <windows.h>
#endif

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

namespace
{
const char* kTrackingSystemName = "joyconvr";
constexpr double kImuDt = 0.005; // Joy-Con IMU frames are 5 ms apart

void DriverLog(const char* fmt, ...)
{
    char buf[1024];
    va_list args;
    va_start(args, fmt);
    std::vsnprintf(buf, sizeof(buf), fmt, args);
    va_end(args);
    if (vr::VRDriverLog())
    {
        vr::VRDriverLog()->Log((std::string("[joyconvr] ") + buf + "\n").c_str());
    }
}

std::string DriverRootDir()
{
#ifdef _WIN32
    HMODULE module = nullptr;
    if (GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                           reinterpret_cast<LPCSTR>(&DriverRootDir), &module))
    {
        char path[MAX_PATH] = {};
        GetModuleFileNameA(module, path, MAX_PATH);
        std::string p = path;
        // <root>\bin\win64\driver_joyconvr.dll  ->  <root>
        for (int i = 0; i < 3; ++i)
        {
            const size_t slash = p.find_last_of("\\/");
            if (slash == std::string::npos) return ".";
            p = p.substr(0, slash);
        }
        return p;
    }
#endif
    return ".";
}

std::string JoinPath(const std::string& root, const std::string& rel)
{
    if (rel.size() > 1 && (rel[1] == ':' || rel[0] == '\\' || rel[0] == '/'))
    {
        return rel; // already absolute
    }
    return root + "/" + rel;
}

bool ReadButton(const JoyconRawState& s, const std::string& name)
{
    if (name == "minus") return s.minus;
    if (name == "plus") return s.plus;
    if (name == "capture") return s.capture;
    if (name == "home") return s.home;
    if (name == "a") return s.a;
    if (name == "b") return s.b;
    if (name == "x") return s.x;
    if (name == "y") return s.y;
    if (name == "left") return s.left;
    if (name == "right") return s.right;
    if (name == "up") return s.up;
    if (name == "down") return s.down;
    if (name == "l") return s.l;
    if (name == "r") return s.r;
    if (name == "zl") return s.zl;
    if (name == "zr") return s.zr;
    if (name == "sl") return s.sl;
    if (name == "sr") return s.sr;
    if (name == "stick") return s.stick;
    return false;
}

std::array<float, 2> StickValue(const JoyconRawState& s, const HandConfig& cfg, const AnalogCalibration& cal, const Switch2Config& sw2)
{
    if (s.hasNormStick)
    {
        float x = s.normStickX, y = s.normStickY;
        const float r = std::sqrt(x * x + y * y);
        if (r < sw2.analogDeadzone)
        {
            x = y = 0.0f;
        }
        else
        {
            const float k = std::min(1.0f, (r - sw2.analogDeadzone) / (1.0f - sw2.analogDeadzone)) / r;
            x *= k;
            y *= k;
        }
        if (sw2.invertX) x = -x;
        if (sw2.invertY) y = -y;
        return {std::clamp(x, -1.0f, 1.0f), std::clamp(y, -1.0f, 1.0f)};
    }
    return JoyconCalibration::Apply(cal, s.rawStickX, s.rawStickY, cfg.invertX, cfg.invertY, cfg.analogDeadzoneNormalized);
}

vr::HmdQuaternion_t ToVr(const Quat& q)
{
    vr::HmdQuaternion_t r;
    r.w = q.w; r.x = q.x; r.y = q.y; r.z = q.z;
    return r;
}

vr::DriverPose_t BasePose()
{
    vr::DriverPose_t pose{};
    pose.qWorldFromDriverRotation.w = 1.0;
    pose.qDriverFromHeadRotation.w = 1.0;
    pose.qRotation.w = 1.0;
    pose.poseIsValid = true;
    pose.deviceIsConnected = true;
    pose.result = vr::TrackingResult_Running_OK;
    return pose;
}

// ---------------------------------------------------------------------------------------------
// Shared state written by the tracking thread, read by vrserver callbacks.
// ---------------------------------------------------------------------------------------------
struct HandSnapshot
{
    JoyconRawState raw;
    bool connected = false;
    vr::DriverPose_t pose = BasePose();
};

struct Snapshot
{
    HandSnapshot hands[2];
    vr::DriverPose_t hmdPose = BasePose();
    bool lookMode = false;
};

class Runtime;

// ---------------------------------------------------------------------------------------------
// Virtual monitor display
// ---------------------------------------------------------------------------------------------

// window_width / window_height = 0 in config.json means "use the size of the main monitor".
// EnumDisplaySettings returns real pixels, independent of Windows display scaling (125%, 150%...).
void AutoDetectMonitor(HmdConfig& h)
{
    if (h.windowWidth != 0 && h.windowHeight != 0) return;
    uint32_t w = 0, hgt = 0;
#ifdef _WIN32
    DEVMODEW dm{};
    dm.dmSize = sizeof(dm);
    if (EnumDisplaySettingsW(nullptr, ENUM_CURRENT_SETTINGS, &dm) && dm.dmPelsWidth > 0 && dm.dmPelsHeight > 0)
    {
        w = dm.dmPelsWidth;
        hgt = dm.dmPelsHeight;
    }
    else
    {
        w = static_cast<uint32_t>(GetSystemMetrics(SM_CXSCREEN));
        hgt = static_cast<uint32_t>(GetSystemMetrics(SM_CYSCREEN));
    }
#endif
    if (w < 320 || hgt < 240)
    {
        w = 1920;
        hgt = 1080;
    }
    if (h.windowWidth == 0) h.windowWidth = w;
    if (h.windowHeight == 0) h.windowHeight = hgt;
    DriverLog("monitor detected: %ux%u", h.windowWidth, h.windowHeight);
}

class MonitorDisplay : public vr::IVRDisplayComponent
{
public:
    explicit MonitorDisplay(const HmdConfig& cfg) : cfg_(cfg) {}

    void GetWindowBounds(int32_t* pnX, int32_t* pnY, uint32_t* pnWidth, uint32_t* pnHeight) override
    {
        *pnX = cfg_.windowX;
        *pnY = cfg_.windowY;
        *pnWidth = cfg_.windowWidth;
        *pnHeight = cfg_.windowHeight;
    }

    bool IsDisplayOnDesktop() override { return true; }
    bool IsDisplayRealDisplay() override { return false; }

    void GetRecommendedRenderTargetSize(uint32_t* pnWidth, uint32_t* pnHeight) override
    {
        *pnWidth = cfg_.renderWidth ? cfg_.renderWidth : cfg_.windowWidth;
        *pnHeight = cfg_.renderHeight ? cfg_.renderHeight : cfg_.windowHeight;
    }

    // Both eyes cover the whole window: what you see is a single flat view (IPD is 0).
    void GetEyeOutputViewport(vr::EVREye, uint32_t* pnX, uint32_t* pnY, uint32_t* pnWidth, uint32_t* pnHeight) override
    {
        *pnX = 0;
        *pnY = 0;
        *pnWidth = cfg_.windowWidth;
        *pnHeight = cfg_.windowHeight;
    }

    void GetProjectionRaw(vr::EVREye, float* pfLeft, float* pfRight, float* pfTop, float* pfBottom) override
    {
        const double aspect = static_cast<double>(cfg_.windowWidth) / std::max<uint32_t>(1, cfg_.windowHeight);
        const double tanV = std::tan(std::clamp<double>(cfg_.verticalFovDeg, 20.0, 150.0) * M_PI / 360.0);
        const double tanH = tanV * aspect;
        *pfLeft = static_cast<float>(-tanH);
        *pfRight = static_cast<float>(tanH);
        *pfTop = static_cast<float>(-tanV);
        *pfBottom = static_cast<float>(tanV);
    }

#if defined(__MINGW32__)
    vr::DistortionCoordinates_t* ComputeDistortion_MsAbi(vr::DistortionCoordinates_t* out, vr::EVREye, float fU, float fV) override
    {
        Fill(*out, fU, fV);
        return out;
    }
#else
    vr::DistortionCoordinates_t ComputeDistortion(vr::EVREye, float fU, float fV) override
    {
        vr::DistortionCoordinates_t c{};
        Fill(c, fU, fV);
        return c;
    }
#endif

    bool ComputeInverseDistortion(vr::HmdVector2_t*, vr::EVREye, uint32_t, float, float) override
    {
        return false;
    }

private:
    static void Fill(vr::DistortionCoordinates_t& c, float u, float v)
    {
        c.rfRed[0] = c.rfGreen[0] = c.rfBlue[0] = u;
        c.rfRed[1] = c.rfGreen[1] = c.rfBlue[1] = v;
    }

    HmdConfig cfg_;
};

// ---------------------------------------------------------------------------------------------
// Base for our tracked devices (handles the GetPose ABI shim)
// ---------------------------------------------------------------------------------------------
class DeviceBase : public vr::ITrackedDeviceServerDriver
{
public:
    void EnterStandby() override {}

    void DebugRequest(const char*, char* pchResponseBuffer, uint32_t unResponseBufferSize) override
    {
        if (unResponseBufferSize > 0) pchResponseBuffer[0] = '\0';
    }

#if defined(__MINGW32__)
    vr::DriverPose_t* GetPose_MsAbi(vr::DriverPose_t* out) override
    {
        *out = CurrentPose();
        return out;
    }
#else
    vr::DriverPose_t GetPose() override { return CurrentPose(); }
#endif

    virtual vr::DriverPose_t CurrentPose() = 0;

    uint32_t ObjectId() const { return objectId_.load(); }

protected:
    std::atomic<uint32_t> objectId_{vr::k_unTrackedDeviceIndexInvalid};
    vr::PropertyContainerHandle_t container_ = vr::k_ulInvalidPropertyContainer;
};

class Runtime
{
public:
    std::mutex mutex;
    Snapshot snap;
    DriverConfig config;
};

// ---------------------------------------------------------------------------------------------
// Virtual headset
// ---------------------------------------------------------------------------------------------
class VirtualHmd : public DeviceBase
{
public:
    explicit VirtualHmd(Runtime& rt) : rt_(rt), display_(rt.config.hmd) {}

    vr::EVRInitError Activate(uint32_t objectId) override
    {
        objectId_ = objectId;
        container_ = vr::VRProperties()->TrackedDeviceToPropertyContainer(objectId);
        auto* p = vr::VRProperties();
        p->SetStringProperty(container_, vr::Prop_ModelNumber_String, "JoyconVR Monitor");
        p->SetStringProperty(container_, vr::Prop_ManufacturerName_String, "JoyconVR");
        p->SetStringProperty(container_, vr::Prop_TrackingSystemName_String, kTrackingSystemName);
        p->SetStringProperty(container_, vr::Prop_SerialNumber_String, "JoyconVR-HMD");
        p->SetUint64Property(container_, vr::Prop_CurrentUniverseId_Uint64, 46);
        p->SetFloatProperty(container_, vr::Prop_UserIpdMeters_Float, 0.0f);
        p->SetFloatProperty(container_, vr::Prop_DisplayFrequency_Float, rt_.config.hmd.refreshHz);
        p->SetFloatProperty(container_, vr::Prop_UserHeadToEyeDepthMeters_Float, 0.0f);
        p->SetFloatProperty(container_, vr::Prop_SecondsFromVsyncToPhotons_Float, 0.011f);
        p->SetBoolProperty(container_, vr::Prop_IsOnDesktop_Bool, false);
        p->SetBoolProperty(container_, vr::Prop_DisplayDebugMode_Bool, true);
        DriverLog("virtual monitor headset active (%ux%u)", rt_.config.hmd.windowWidth, rt_.config.hmd.windowHeight);
        return vr::VRInitError_None;
    }

    void Deactivate() override { objectId_ = vr::k_unTrackedDeviceIndexInvalid; }

    void* GetComponent(const char* name) override
    {
        if (std::strcmp(name, vr::IVRDisplayComponent_Version) == 0)
        {
            return static_cast<vr::IVRDisplayComponent*>(&display_);
        }
        return nullptr;
    }

    vr::DriverPose_t CurrentPose() override
    {
        std::lock_guard<std::mutex> lock(rt_.mutex);
        return rt_.snap.hmdPose;
    }

private:
    Runtime& rt_;
    MonitorDisplay display_;
};

// ---------------------------------------------------------------------------------------------
// Joy-Con controller (presented as an Index-compatible controller)
// ---------------------------------------------------------------------------------------------
class JoyconController : public DeviceBase
{
public:
    JoyconController(JoyconHand hand, Runtime& rt) : hand_(hand), rt_(rt) {}

    vr::EVRInitError Activate(uint32_t objectId) override
    {
        objectId_ = objectId;
        container_ = vr::VRProperties()->TrackedDeviceToPropertyContainer(objectId);
        const bool left = hand_ == JoyconHand::Left;
        auto* p = vr::VRProperties();

        p->SetStringProperty(container_, vr::Prop_ModelNumber_String, left ? "Knuckles Left" : "Knuckles Right");
        p->SetStringProperty(container_, vr::Prop_RenderModelName_String,
                             left ? "{indexcontroller}valve_controller_knu_1_0_left" : "{indexcontroller}valve_controller_knu_1_0_right");
        p->SetStringProperty(container_, vr::Prop_InputProfilePath_String,
                             left ? "{joyconvr}/input/joycon_left_profile.json" : "{joyconvr}/input/joycon_right_profile.json");
        p->SetStringProperty(container_, vr::Prop_SerialNumber_String, left ? "JoyconVR-L" : "JoyconVR-R");
        p->SetStringProperty(container_, vr::Prop_TrackingSystemName_String, kTrackingSystemName);
        p->SetStringProperty(container_, vr::Prop_ManufacturerName_String, "Valve");
        p->SetStringProperty(container_, vr::Prop_ControllerType_String, left ? "joycon_left" : "joycon_right");
        p->SetUint64Property(container_, vr::Prop_CurrentUniverseId_Uint64, 46);
        p->SetInt32Property(container_, vr::Prop_ControllerRoleHint_Int32,
                            left ? vr::TrackedControllerRole_LeftHand : vr::TrackedControllerRole_RightHand);
        p->SetInt32Property(container_, vr::Prop_ControllerHandSelectionPriority_Int32, 100);

        CreateInputs();
        DriverLog("%s Joy-Con controller active", left ? "left" : "right");
        return vr::VRInitError_None;
    }

    void Deactivate() override { objectId_ = vr::k_unTrackedDeviceIndexInvalid; }

    void* GetComponent(const char*) override { return nullptr; }

    vr::DriverPose_t CurrentPose() override
    {
        std::lock_guard<std::mutex> lock(rt_.mutex);
        return rt_.snap.hands[Index()].pose;
    }

    // Called from RunFrame.
    void UpdateInputs(const JoyconRawState& rawIn, bool suppressStick, const DriverConfig& config)
    {
        if (ObjectId() == vr::k_unTrackedDeviceIndexInvalid) return;

        const bool left = hand_ == JoyconHand::Left;
        const HandConfig& cfg = left ? config.left : config.right;
        const AnalogCalibration& cal = left ? leftCal : rightCal;
        auto* in = vr::VRDriverInput();

        const auto stick = StickValue(rawIn, cfg, cal, config.switch2);
        const float sx = suppressStick ? 0.0f : stick[0];
        const float sy = suppressStick ? 0.0f : stick[1];

        for (const auto& raw : rawButtons_)
        {
            in->UpdateBooleanComponent(raw.second, ReadButton(rawIn, raw.first), 0.0);
        }

        std::unordered_map<VrBooleanAction, bool> actions;
        for (const auto& kv : cfg.buttonBindings)
        {
            if (kv.second != VrBooleanAction::Unused)
            {
                actions[kv.second] = actions[kv.second] || ReadButton(rawIn, kv.first);
            }
        }
        for (const auto& kv : actionHandles_)
        {
            in->UpdateBooleanComponent(kv.second, actions[kv.first], 0.0);
        }

        const bool trigger = actions[VrBooleanAction::TriggerClick];
        const bool grip = actions[VrBooleanAction::Grip];
        in->UpdateScalarComponent(triggerValue_, trigger ? 1.0f : 0.0f, 0.0);
        in->UpdateBooleanComponent(triggerTouch_, trigger, 0.0);
        in->UpdateScalarComponent(gripValue_, grip ? 1.0f : 0.0f, 0.0);
        in->UpdateScalarComponent(gripForce_, grip ? 1.0f : 0.0f, 0.0);
        in->UpdateBooleanComponent(gripTouch_, grip, 0.0);
        in->UpdateBooleanComponent(aTouch_, actions[VrBooleanAction::A], 0.0);
        in->UpdateBooleanComponent(bTouch_, actions[VrBooleanAction::B], 0.0);
        in->UpdateScalarComponent(thumbX_, sx, 0.0);
        in->UpdateScalarComponent(thumbY_, sy, 0.0);
        in->UpdateBooleanComponent(thumbTouch_, std::abs(sx) > 0.05f || std::abs(sy) > 0.05f || actions[VrBooleanAction::ThumbstickClick], 0.0);
    }

    AnalogCalibration leftCal;
    AnalogCalibration rightCal;

private:
    int Index() const { return hand_ == JoyconHand::Left ? 0 : 1; }

    void CreateInputs()
    {
        auto* in = vr::VRDriverInput();
        auto boolean = [&](const char* path) {
            vr::VRInputComponentHandle_t h = 0;
            in->CreateBooleanComponent(container_, path, &h);
            return h;
        };
        auto scalar = [&](const char* path, vr::EVRScalarUnits units) {
            vr::VRInputComponentHandle_t h = 0;
            in->CreateScalarComponent(container_, path, &h, vr::VRScalarType_Absolute, units);
            return h;
        };

        actionHandles_[VrBooleanAction::System] = boolean("/input/system/click");
        actionHandles_[VrBooleanAction::Menu] = boolean("/input/application_menu/click");
        actionHandles_[VrBooleanAction::A] = boolean("/input/a/click");
        actionHandles_[VrBooleanAction::B] = boolean("/input/b/click");
        actionHandles_[VrBooleanAction::X] = boolean("/input/x/click");
        actionHandles_[VrBooleanAction::Y] = boolean("/input/y/click");
        actionHandles_[VrBooleanAction::Grip] = boolean("/input/grip/click");
        actionHandles_[VrBooleanAction::TriggerClick] = boolean("/input/trigger/click");
        actionHandles_[VrBooleanAction::ThumbstickClick] = boolean("/input/thumbstick/click");

        aTouch_ = boolean("/input/a/touch");
        bTouch_ = boolean("/input/b/touch");
        triggerTouch_ = boolean("/input/trigger/touch");
        gripTouch_ = boolean("/input/grip/touch");
        thumbTouch_ = boolean("/input/thumbstick/touch");

        triggerValue_ = scalar("/input/trigger/value", vr::VRScalarUnits_NormalizedOneSided);
        gripValue_ = scalar("/input/grip/value", vr::VRScalarUnits_NormalizedOneSided);
        gripForce_ = scalar("/input/grip/force", vr::VRScalarUnits_NormalizedOneSided);
        thumbX_ = scalar("/input/thumbstick/x", vr::VRScalarUnits_NormalizedTwoSided);
        thumbY_ = scalar("/input/thumbstick/y", vr::VRScalarUnits_NormalizedTwoSided);

        vr::VRInputComponentHandle_t haptic = 0;
        in->CreateHapticComponent(container_, "/output/haptic", &haptic);

        const char* raws[] = {"minus", "plus", "capture", "home", "up", "down", "left", "right", "l", "r",
                              "zl", "zr", "sl", "sr", "a", "b", "x", "y", "stick"};
        for (const char* name : raws)
        {
            const std::string path = std::string("/input/joycon_") + name + "/click";
            rawButtons_[name] = boolean(path.c_str());
        }
    }

    JoyconHand hand_;
    Runtime& rt_;
    std::unordered_map<VrBooleanAction, vr::VRInputComponentHandle_t> actionHandles_;
    std::unordered_map<std::string, vr::VRInputComponentHandle_t> rawButtons_;
    vr::VRInputComponentHandle_t aTouch_ = 0, bTouch_ = 0, triggerTouch_ = 0, gripTouch_ = 0, thumbTouch_ = 0;
    vr::VRInputComponentHandle_t triggerValue_ = 0, gripValue_ = 0, gripForce_ = 0, thumbX_ = 0, thumbY_ = 0;
};

// ---------------------------------------------------------------------------------------------
// Provider + tracking thread
// ---------------------------------------------------------------------------------------------
class Provider : public vr::IServerTrackedDeviceProvider
{
public:
    vr::EVRInitError Init(vr::IVRDriverContext* pDriverContext) override
    {
        VR_INIT_SERVER_DRIVER_CONTEXT(pDriverContext);

        root_ = DriverRootDir();
        std::string error;
        rt_.config = JoyconConfig::LoadFromFile(JoinPath(root_, "config/config.json"), &error);
        if (!error.empty()) DriverLog("%s", error.c_str());
        AutoDetectMonitor(rt_.config.hmd);
        DriverLog("driver folder: %s", root_.c_str());

        const std::string calPath = JoinPath(root_, rt_.config.joyConCalPath);
        if (auto c = JoyconCalibration::LoadFromJoyConCalJson(calPath, JoyconHand::Left)) leftCal_ = *c;
        if (auto c = JoyconCalibration::LoadFromJoyConCalJson(calPath, JoyconHand::Right)) rightCal_ = *c;

        trackers_[0].Configure(JoyconHand::Left, rt_.config.left, rt_.config.arm, rt_.config.imu);
        trackers_[1].Configure(JoyconHand::Right, rt_.config.right, rt_.config.arm, rt_.config.imu);

        UpdateHmdPose();

        if (rt_.config.hmd.enabled)
        {
            hmd_ = std::make_unique<VirtualHmd>(rt_);
            vr::VRServerDriverHost()->TrackedDeviceAdded("JoyconVR-HMD", vr::TrackedDeviceClass_HMD, hmd_.get());
        }

        controllers_[0] = std::make_unique<JoyconController>(JoyconHand::Left, rt_);
        controllers_[1] = std::make_unique<JoyconController>(JoyconHand::Right, rt_);
        for (auto& c : controllers_)
        {
            c->leftCal = leftCal_;
            c->rightCal = rightCal_;
        }
        vr::VRServerDriverHost()->TrackedDeviceAdded("JoyconVR-L", vr::TrackedDeviceClass_Controller, controllers_[0].get());
        vr::VRServerDriverHost()->TrackedDeviceAdded("JoyconVR-R", vr::TrackedDeviceClass_Controller, controllers_[1].get());

        running_ = true;
        thread_ = std::thread(&Provider::TrackingThread, this);
        DriverLog("initialized");
        return vr::VRInitError_None;
    }

    void Cleanup() override
    {
        if (running_.exchange(false) && thread_.joinable())
        {
            thread_.join();
        }
        controllers_[0].reset();
        controllers_[1].reset();
        hmd_.reset();
        DriverLog("cleanup complete");
    }

    const char* const* GetInterfaceVersions() override { return vr::k_InterfaceVersions; }

    void RunFrame() override
    {
        // Copy under the lock: the tracking thread can swap rt_.config when config.json changes.
        Snapshot snap;
        DriverConfig config;
        {
            std::lock_guard<std::mutex> lock(rt_.mutex);
            snap = rt_.snap;
            config = rt_.config;
        }
        for (int i = 0; i < 2; ++i)
        {
            const JoyconHand hand = i == 0 ? JoyconHand::Left : JoyconHand::Right;
            const bool suppress = snap.lookMode && config.look.stickHand == hand;
            controllers_[i]->UpdateInputs(snap.hands[i].raw, suppress, config);
        }

        vr::VREvent_t ev;
        while (vr::VRServerDriverHost()->PollNextEvent(&ev, sizeof(ev)))
        {
        }
    }

    bool ShouldBlockStandbyMode() override { return false; }
    void EnterStandby() override {}
    void LeaveStandby() override {}

private:
    struct HandRuntime
    {
        bool lastToggle = false;
        bool lastRecenter = false;
        std::chrono::steady_clock::time_point pressStart;
        bool recenterFired = false;
        std::chrono::steady_clock::time_point lastConnectAttempt{};
        double extend = 0.0;
        enum class Source { None, Hid, Switch2 } source = Source::None;
        std::chrono::steady_clock::time_point lastUdp{};
        bool udpConnected = false;
        uint32_t lastTimestamp = 0;
        bool haveTimestamp = false;
        std::chrono::steady_clock::time_point lastUdpSample{};
    };

    void ConfigureTracker(int i, bool switch2Source)
    {
        const JoyconHand hand = i == 0 ? JoyconHand::Left : JoyconHand::Right;
        HandConfig hc = i == 0 ? rt_.config.left : rt_.config.right;
        ImuConfig imu = rt_.config.imu;
        if (switch2Source)
        {
            const Switch2HandConfig& s2 = i == 0 ? rt_.config.switch2.left : rt_.config.switch2.right;
            hc.forwardAxis = s2.forwardAxis;
            hc.flipUp = s2.flipUp;
            imu.gyroScaleDps = rt_.config.switch2.gyroScaleDps;
            imu.accelScaleG = rt_.config.switch2.accelScaleG;
        }
        trackers_[i].Configure(hand, hc, rt_.config.arm, imu);
    }

    void ResetTracker(int i, bool switch2Source)
    {
        trackers_[i] = MotionTracker();
        ConfigureTracker(i, switch2Source);
    }

    // Live settings: when config.json is saved, apply it without restarting SteamVR.
    // The virtual screen (hmd) and the Joy-Con 2 UDP port only change on restart.
    void CheckConfigReload()
    {
        const auto now = std::chrono::steady_clock::now();
        if (now - lastConfigCheck_ < std::chrono::seconds(1)) return;
        lastConfigCheck_ = now;

        const std::string path = JoinPath(root_, "config/config.json");
        std::error_code ec;
        const auto stamp = std::filesystem::last_write_time(std::filesystem::u8path(path), ec);
        if (ec) return;
        if (!haveConfigStamp_)
        {
            configStamp_ = stamp;
            haveConfigStamp_ = true;
            return;
        }
        if (stamp == configStamp_) return;
        configStamp_ = stamp;

        std::string error;
        DriverConfig fresh = JoyconConfig::LoadFromFile(path, &error);
        if (!error.empty())
        {
            DriverLog("config.json not reloaded: %s", error.c_str());
            return;
        }
        fresh.hmd = rt_.config.hmd;
        fresh.switch2.udpPort = rt_.config.switch2.udpPort;
        fresh.switch2.enabled = rt_.config.switch2.enabled;

        bool axisChanged[2];
        for (int i = 0; i < 2; ++i)
        {
            const bool sw2 = handRt_[i].source == HandRuntime::Source::Switch2;
            const auto& oldH = i == 0 ? rt_.config.left : rt_.config.right;
            const auto& newH = i == 0 ? fresh.left : fresh.right;
            const auto& oldS = i == 0 ? rt_.config.switch2.left : rt_.config.switch2.right;
            const auto& newS = i == 0 ? fresh.switch2.left : fresh.switch2.right;
            axisChanged[i] = sw2 ? (oldS.forwardAxis != newS.forwardAxis || oldS.flipUp != newS.flipUp)
                                 : (oldH.forwardAxis != newH.forwardAxis || oldH.flipUp != newH.flipUp);
        }
        {
            std::lock_guard<std::mutex> lock(rt_.mutex);
            rt_.config = fresh;
        }
        for (int i = 0; i < 2; ++i)
        {
            ConfigureTracker(i, handRt_[i].source == HandRuntime::Source::Switch2);
            if (axisChanged[i]) trackers_[i].Recenter();
        }
        DriverLog("config.json reloaded (live)");
    }

#ifdef _WIN32
    void OpenUdp()
    {
        if (!rt_.config.switch2.enabled) return;
        WSADATA wsa;
        if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) return;
        udp_ = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
        if (udp_ == INVALID_SOCKET) return;
        sockaddr_in addr{};
        addr.sin_family = AF_INET;
        addr.sin_port = htons(rt_.config.switch2.udpPort);
        addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        if (bind(udp_, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) != 0)
        {
            DriverLog("could not open UDP port %u for Joy-Con 2", rt_.config.switch2.udpPort);
            closesocket(udp_);
            udp_ = INVALID_SOCKET;
            return;
        }
        u_long nonBlocking = 1;
        ioctlsocket(udp_, FIONBIO, &nonBlocking);
        DriverLog("listening for Joy-Con 2 bridge on 127.0.0.1:%u", rt_.config.switch2.udpPort);
    }

    void CloseUdp()
    {
        if (udp_ != INVALID_SOCKET)
        {
            closesocket(udp_);
            udp_ = INVALID_SOCKET;
            WSACleanup();
        }
    }

    // Reads every pending Joy-Con 2 packet. Returns which hands got data.
    void PollUdp(JoyconRawState raw[2], bool got[2])
    {
        if (udp_ == INVALID_SOCKET) return;
        uint8_t buf[256];
        for (int guard = 0; guard < 256; ++guard)
        {
            const int n = recv(udp_, reinterpret_cast<char*>(buf), sizeof(buf), 0);
            if (n <= 0) break;
            Switch2Packet p;
            if (!switch2::Parse(buf, static_cast<size_t>(n), p)) continue;
            const int i = p.hand == JoyconHand::Left ? 0 : 1;
            if (hids_[i].IsConnected()) continue; // a Switch 1 Joy-Con on this hand wins
            HandRuntime& h = handRt_[i];
            const auto now = std::chrono::steady_clock::now();
            h.lastUdp = now;
            h.udpConnected = p.connected;
            if (!p.connected) continue;

            if (h.source != HandRuntime::Source::Switch2)
            {
                h.source = HandRuntime::Source::Switch2;
                h.haveTimestamp = false;
                ResetTracker(i, true);
                DriverLog("%s Joy-Con 2 connected (bridge)", i == 0 ? "left" : "right");
            }

            switch2::ToRawState(p, raw[i]);
            got[i] = true;

            // Time step: prefer the controller's own microsecond clock, fall back to arrival time.
            double dt = 0.0;
            if (h.haveTimestamp)
            {
                const uint32_t d = p.timestampUs - h.lastTimestamp;
                if (d >= 500 && d <= 50000) dt = d * 1e-6;
            }
            if (dt == 0.0 && h.haveTimestamp)
            {
                dt = std::clamp(std::chrono::duration<double>(now - h.lastUdpSample).count(), 0.002, 0.03);
            }
            if (dt == 0.0) dt = 0.008;
            h.lastTimestamp = p.timestampUs;
            h.lastUdpSample = now;
            h.haveTimestamp = true;

            trackers_[i].AddSample(p.imu, dt);
        }
    }
#endif

    void UpdateHmdPose()
    {
        const Quat yaw = Quat::AxisAngle({0, 1, 0}, headYaw_);
        const Quat pitch = Quat::AxisAngle({1, 0, 0}, headPitch_);
        vr::DriverPose_t pose = BasePose();
        pose.qRotation = ToVr(yaw * pitch);
        pose.vecPosition[0] = 0.0;
        pose.vecPosition[1] = rt_.config.hmd.headHeight;
        pose.vecPosition[2] = 0.0;
        std::lock_guard<std::mutex> lock(rt_.mutex);
        rt_.snap.hmdPose = pose;
    }

    void RecenterAll()
    {
        for (auto& t : trackers_) t.Recenter();
        headYaw_ = 0.0;
        headPitch_ = 0.0;
        DriverLog("recentered");
    }

    void UpdateLights()
    {
        for (int i = 0; i < 2; ++i)
        {
            if (hids_[i].IsConnected())
            {
                const uint8_t base = i == 0 ? 0x01 : 0x02;
                hids_[i].SetPlayerLights(lookMode_ ? 0x0F : base);
            }
        }
    }

    void HandleButtons(const JoyconRawState raw[2], double dt)
    {
        const LookConfig& look = rt_.config.look;
        const RecenterConfig& rec = rt_.config.recenter;
        const auto now = std::chrono::steady_clock::now();

        const int ti = look.toggle.hand == JoyconHand::Left ? 0 : 1;
        const int ri = rec.button.hand == JoyconHand::Left ? 0 : 1;
        const bool togglePressed = !look.toggle.button.empty() && ReadButton(raw[ti], look.toggle.button);
        const bool recenterPressed = !rec.button.button.empty() && ReadButton(raw[ri], rec.button.button);
        const bool shared = ti == ri && look.toggle.button == rec.button.button;

        HandRuntime& t = handRt_[ti];
        HandRuntime& r = handRt_[ri];

        // Recenter on long hold.
        if (recenterPressed && !r.lastRecenter)
        {
            r.pressStart = now;
            r.recenterFired = false;
        }
        if (recenterPressed && !r.recenterFired &&
            std::chrono::duration<double>(now - r.pressStart).count() >= rec.holdSeconds)
        {
            r.recenterFired = true;
            RecenterAll();
        }

        // Toggle look mode: on press, or (if shared with recenter) on a short release.
        if (shared)
        {
            if (!togglePressed && t.lastToggle && !r.recenterFired)
            {
                lookMode_ = !lookMode_;
                UpdateLights();
            }
        }
        else if (togglePressed && !t.lastToggle)
        {
            lookMode_ = !lookMode_;
            UpdateLights();
        }

        t.lastToggle = togglePressed;
        r.lastRecenter = recenterPressed;

        // Look around with a stick. With always_on the stick moves the camera unless the
        // toggle button turned it off (lookMode_ then means "switched away from the default").
        if (lookMode_ != look.alwaysOn)
        {
            const int si = look.stickHand == JoyconHand::Left ? 0 : 1;
            const HandConfig& cfg = si == 0 ? rt_.config.left : rt_.config.right;
            const AnalogCalibration& cal = si == 0 ? leftCal_ : rightCal_;
            const auto stick = StickValue(raw[si], cfg, cal, rt_.config.switch2);
            headYaw_ -= stick[0] * look.yawSpeedDeg * M_PI / 180.0 * dt;
            headPitch_ += stick[1] * look.pitchSpeedDeg * M_PI / 180.0 * dt;
        }

#ifdef _WIN32
        // Look around with the mouse while the middle button is held.
        if (look.mouseLook)
        {
            POINT p;
            const bool held = (GetAsyncKeyState(VK_MBUTTON) & 0x8000) != 0;
            if (held && GetCursorPos(&p))
            {
                if (mouseHeld_)
                {
                    headYaw_ -= (p.x - lastMouse_.x) * look.mouseDegPerPixel * M_PI / 180.0;
                    headPitch_ -= (p.y - lastMouse_.y) * look.mouseDegPerPixel * M_PI / 180.0;
                }
                lastMouse_ = p;
            }
            mouseHeld_ = held;
        }
#endif

        const double maxPitch = look.maxPitchDeg * M_PI / 180.0;
        headPitch_ = std::clamp(headPitch_, -maxPitch, maxPitch);
        headYaw_ = std::remainder(headYaw_, 2.0 * M_PI);

        // Arm extension buttons.
        for (int i = 0; i < 2; ++i)
        {
            const HandConfig& cfg = i == 0 ? rt_.config.left : rt_.config.right;
            const double target = (!cfg.extendButton.empty() && ReadButton(raw[i], cfg.extendButton)) ? cfg.extendDistance : 0.0;
            handRt_[i].extend += (target - handRt_[i].extend) * std::min(1.0, dt * 12.0);
        }
    }

    void TrackingThread()
    {
#ifdef _WIN32
        SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_ABOVE_NORMAL);
#endif
#ifdef _WIN32
        OpenUdp();
#endif
        JoyconRawState raw[2];
        std::vector<ImuSample> imu;
        auto last = std::chrono::steady_clock::now();
        auto lastPoseSubmit = last;
        bool wasConnected[2] = {false, false};

        while (running_)
        {
            const auto now = std::chrono::steady_clock::now();
            const double dt = std::chrono::duration<double>(now - last).count();
            last = now;

            for (int i = 0; i < 2; ++i)
            {
                if (!hids_[i].IsConnected())
                {
                    if (now - handRt_[i].lastConnectAttempt > std::chrono::seconds(2))
                    {
                        handRt_[i].lastConnectAttempt = now;
                        if (hids_[i].Connect())
                        {
                            DriverLog("%s Joy-Con connected", i == 0 ? "left" : "right");
                            ResetTracker(i, false);
                            handRt_[i].source = HandRuntime::Source::Hid;
                            UpdateLights();
                        }
                    }
                }

                if (hids_[i].IsConnected())
                {
                    imu.clear();
                    raw[i].hasNormStick = false;
                    hids_[i].Poll(raw[i], imu);
                    for (const ImuSample& s : imu)
                    {
                        trackers_[i].AddSample(s, kImuDt);
                    }
                }

                if (wasConnected[i] && !hids_[i].IsConnected())
                {
                    DriverLog("%s Joy-Con disconnected", i == 0 ? "left" : "right");
                    handRt_[i].source = HandRuntime::Source::None;
                }
                wasConnected[i] = hids_[i].IsConnected();
            }

#ifdef _WIN32
            bool gotUdp[2] = {false, false};
            PollUdp(raw, gotUdp);
#endif
            for (int i = 0; i < 2; ++i)
            {
                HandRuntime& h = handRt_[i];
                if (h.source == HandRuntime::Source::Switch2 &&
                    (!h.udpConnected || now - h.lastUdp > std::chrono::seconds(1)))
                {
                    DriverLog("%s Joy-Con 2 disconnected", i == 0 ? "left" : "right");
                    h.source = HandRuntime::Source::None;
                    raw[i] = JoyconRawState();
                }
                if (trackers_[i].NeedsAutoRecenter())
                {
                    trackers_[i].Recenter();
                }
            }

            CheckConfigReload();
            HandleButtons(raw, dt);
            UpdateHmdPose();

            // Controller poses
            const Quat bodyYaw = Quat::AxisAngle({0, 1, 0}, headYaw_);
            const Vec3 head{0.0, rt_.config.hmd.headHeight, 0.0};
            {
                std::lock_guard<std::mutex> lock(rt_.mutex);
                rt_.snap.lookMode = lookMode_ != rt_.config.look.alwaysOn;
                for (int i = 0; i < 2; ++i)
                {
                    HandSnapshot& h = rt_.snap.hands[i];
                    h.raw = raw[i];
                    h.connected = handRt_[i].source != HandRuntime::Source::None;

                    vr::DriverPose_t pose = BasePose();
                    pose.deviceIsConnected = h.connected;
                    pose.poseIsValid = h.connected && trackers_[i].HasData();
                    pose.result = pose.poseIsValid ? vr::TrackingResult_Running_OK : vr::TrackingResult_Running_OutOfRange;
                    const Vec3 pos = head + bodyYaw.Rotate(trackers_[i].HandOffsetFromHead(handRt_[i].extend));
                    pose.qRotation = ToVr(bodyYaw * trackers_[i].ControllerOrientation());
                    pose.vecPosition[0] = pos.x;
                    pose.vecPosition[1] = pos.y;
                    pose.vecPosition[2] = pos.z;
                    h.pose = pose;
                }
            }

            if (now - lastPoseSubmit >= std::chrono::milliseconds(4))
            {
                lastPoseSubmit = now;
                SubmitPoses();
            }

            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }

        for (auto& h : hids_) h.Disconnect();
#ifdef _WIN32
        CloseUdp();
#endif
    }

    void SubmitPoses()
    {
        auto* host = vr::VRServerDriverHost();
        if (hmd_ && hmd_->ObjectId() != vr::k_unTrackedDeviceIndexInvalid)
        {
            const vr::DriverPose_t p = hmd_->CurrentPose();
            host->TrackedDevicePoseUpdated(hmd_->ObjectId(), p, sizeof(p));
        }
        for (auto& c : controllers_)
        {
            if (c && c->ObjectId() != vr::k_unTrackedDeviceIndexInvalid)
            {
                const vr::DriverPose_t p = c->CurrentPose();
                host->TrackedDevicePoseUpdated(c->ObjectId(), p, sizeof(p));
            }
        }
    }

    Runtime rt_;
    std::string root_;
    AnalogCalibration leftCal_, rightCal_;
    JoyconHid hids_[2] = {JoyconHid(JoyconHand::Left), JoyconHid(JoyconHand::Right)};
    MotionTracker trackers_[2];
    HandRuntime handRt_[2];
    std::unique_ptr<VirtualHmd> hmd_;
    std::unique_ptr<JoyconController> controllers_[2];
    std::thread thread_;
    std::chrono::steady_clock::time_point lastConfigCheck_{};
    std::filesystem::file_time_type configStamp_{};
    bool haveConfigStamp_ = false;
    std::atomic<bool> running_{false};

    // Only touched by the tracking thread.
    double headYaw_ = 0.0;
    double headPitch_ = 0.0;
    bool lookMode_ = false;
#ifdef _WIN32
    bool mouseHeld_ = false;
    POINT lastMouse_{};
    SOCKET udp_ = INVALID_SOCKET;
#endif
};

Provider g_provider;
} // namespace

#if defined(_WIN32)
#define HMD_DLL_EXPORT extern "C" __declspec(dllexport)
#else
#define HMD_DLL_EXPORT extern "C" __attribute__((visibility("default")))
#endif

HMD_DLL_EXPORT void* HmdDriverFactory(const char* pInterfaceName, int* pReturnCode)
{
    if (0 == std::strcmp(vr::IServerTrackedDeviceProvider_Version, pInterfaceName))
    {
        if (pReturnCode) *pReturnCode = vr::VRInitError_None;
        return &g_provider;
    }
    if (pReturnCode) *pReturnCode = vr::VRInitError_Init_InterfaceNotFound;
    return nullptr;
}
