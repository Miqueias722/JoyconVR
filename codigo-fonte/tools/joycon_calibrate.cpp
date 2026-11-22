// joycon_calibrate.cpp
// Standalone Joy-Con analog stick calibration tool.
// Connects to left and/or right Joy-Con via hidapi, walks through center/range/deadzone
// calibration and writes config/joyconcal.json consumed by the driver.
// Based on the JoyConCal process by jdanders: https://github.com/jdanders/JoyConCal

#include <hidapi.h>
#include <nlohmann/json.hpp>

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <string>
#ifdef _WIN32
#include <windows.h>
#endif

using json = nlohmann::json;

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------
constexpr unsigned short kNintendoVendor = 0x057e;
constexpr unsigned short kJoyConLPid     = 0x2006; // Joy-Con (L)
constexpr unsigned short kJoyConRPid     = 0x2007; // Joy-Con (R)
constexpr size_t         kPacketSize     = 49;

// Scale measured max/min towards center so real play stays within range.
// JoyConCal default; user unlikely to push as hard during play as during cal.
constexpr float kScale = 0.80f;

// ---------------------------------------------------------------------------
// Internal state
// ---------------------------------------------------------------------------
static uint8_t s_counter = 0;

// ---------------------------------------------------------------------------
// HID helpers
// ---------------------------------------------------------------------------
static void SendSubcommand(
    hid_device* dev, uint8_t cmd,
    const uint8_t* data, size_t len,
    bool waitReply)
{
    uint8_t out[kPacketSize] = {};
    out[0]  = 0x01;     // output report ID for subcommands
    out[1]  = s_counter;
    s_counter = static_cast<uint8_t>((s_counter + 1) & 0x0F);
    // Rumble data (neutral)
    out[2] = 0x00; out[3] = 0x01; out[4] = 0x40; out[5] = 0x40;
    out[6] = 0x00; out[7] = 0x01; out[8] = 0x40; out[9] = 0x40;
    out[10] = cmd;
    if (data && len > 0)
        std::memcpy(&out[11], data, len);

    hid_write(dev, out, kPacketSize);

    if (waitReply)
    {
        uint8_t in[kPacketSize] = {};
        hid_read_timeout(dev, in, kPacketSize, 100);
    }
}

static void SetFullMode(hid_device* dev)
{
    uint8_t mode = 0x30; // Full input report @ 60 Hz
    SendSubcommand(dev, 0x03, &mode, 1, true);
}

static void SetLed(hid_device* dev, uint8_t leds)
{
    SendSubcommand(dev, 0x30, &leds, 1, false);
}

static void Cleanup(hid_device* dev)
{
    uint8_t buf = 0x01; // normal mode
    SendSubcommand(dev, 0x03, &buf, 1, false);
    buf = 0x00;
    SendSubcommand(dev, 0x06, &buf, 1, false); // Disconnect
}

// Return true if any non-stick button is pressed in a 0x30 report
static bool IsButtonPressed(const uint8_t* data)
{
    if (data[0] == 0x30 || data[0] == 0x21)
        return (data[3] | (data[4] & 0xF3) | data[5]) != 0;
    return false;
}

// Block until a button is pressed then released
static void WaitButton(hid_device* dev, const std::string& prompt)
{
    std::cout << prompt << std::endl;
    uint8_t buf[kPacketSize] = {};
    while (true)
    {
        if (hid_read(dev, buf, kPacketSize) > 0 && IsButtonPressed(buf))
            break;
    }
    while (true)
    {
        if (hid_read(dev, buf, kPacketSize) > 0 && !IsButtonPressed(buf))
            break;
    }
}

// Decode Joy-Con 12-bit stick bytes into raw X/Y
static void DecodeStick(const uint8_t* report, bool isLeft, uint16_t& outX, uint16_t& outY)
{
    uint8_t a, b, c;
    if (isLeft)  { a = report[6];  b = report[7];  c = report[8]; }
    else          { a = report[9];  b = report[10]; c = report[11]; }

    outX = static_cast<uint16_t>(a | ((b & 0x0F) << 8));
    outY = static_cast<uint16_t>((b >> 4) | (c << 4));
}

// ---------------------------------------------------------------------------
// Calibration data accumulators
// ---------------------------------------------------------------------------
struct CalAccum
{
    // Center point
    uint16_t centerX = 0;
    uint16_t centerY = 0;
    bool     hasCenter = false;

    // Full range
    uint16_t rangeXMin = std::numeric_limits<uint16_t>::max();
    uint16_t rangeXMax = 0;
    uint16_t rangeYMin = std::numeric_limits<uint16_t>::max();
    uint16_t rangeYMax = 0;

    // Deadzone: extents of stick at center-ish (soft press)
    uint16_t dzXMin = std::numeric_limits<uint16_t>::max();
    uint16_t dzXMax = 0;
    uint16_t dzYMin = std::numeric_limits<uint16_t>::max();
    uint16_t dzYMax = 0;

    void UpdateRange(uint16_t x, uint16_t y)
    {
        rangeXMin = std::min(rangeXMin, x);
        rangeXMax = std::max(rangeXMax, x);
        rangeYMin = std::min(rangeYMin, y);
        rangeYMax = std::max(rangeYMax, y);
    }

    void UpdateDeadzone(uint16_t x, uint16_t y)
    {
        dzXMin = std::min(dzXMin, x);
        dzXMax = std::max(dzXMax, x);
        dzYMin = std::min(dzYMin, y);
        dzYMax = std::max(dzYMax, y);
    }
};

// ---------------------------------------------------------------------------
// Calibration steps
// ---------------------------------------------------------------------------
static CalAccum RunCalibration(hid_device* dev, bool isLeft, const std::string& handName)
{
    CalAccum a;
    uint8_t  buf[kPacketSize] = {};

    // ---- Step 1: center ------------------------------------------------
    WaitButton(dev, "\n[" + handName + "] CENTER: Leave the stick centered and press any button.");

    // Drain buffer to get the latest report right after button release
    for (int i = 0; i < 10; ++i)
        hid_read_timeout(dev, buf, kPacketSize, 5);

    // Read a fresh 0x30 report for center
    for (int tries = 0; tries < 200; ++tries)
    {
        if (hid_read_timeout(dev, buf, kPacketSize, 8) > 0 && buf[0] == 0x30)
        {
            uint16_t cx, cy;
            DecodeStick(buf, isLeft, cx, cy);
            a.centerX   = cx;
            a.centerY   = cy;
            a.hasCenter = true;
            // Seed deadzone with center
            a.dzXMin = a.dzXMax = cx;
            a.dzYMin = a.dzYMax = cy;
            // Seed range with center
            a.rangeXMin = a.rangeXMax = cx;
            a.rangeYMin = a.rangeYMax = cy;
            std::cout << "  Recorded center: X=" << cx << " Y=" << cy << std::endl;
            break;
        }
    }

    if (!a.hasCenter)
    {
        std::cerr << "  WARNING: could not read center. Using default 2048." << std::endl;
        a.centerX = a.centerY = 2048;
        a.hasCenter = true;
        a.dzXMin = a.dzXMax = a.rangeXMin = a.rangeXMax = 2048;
        a.dzYMin = a.dzYMax = a.rangeYMin = a.rangeYMax = 2048;
    }

    // ---- Step 2: full range --------------------------------------------
    std::cout << "\n[" << handName << "] RANGE: Push the stick to every extreme (full circles, push hard)." << std::endl;
    std::cout << "  Watch the range values below. Press any button when the numbers stop changing." << std::endl;

    while (true)
    {
        if (hid_read_timeout(dev, buf, kPacketSize, 8) > 0 && buf[0] == 0x30)
        {
            uint16_t x, y;
            DecodeStick(buf, isLeft, x, y);
            a.UpdateRange(x, y);

            std::cout << "\r  X:[" << a.rangeXMin << " | " << a.centerX << " | " << a.rangeXMax << "]  "
                      << "Y:[" << a.rangeYMin << " | " << a.centerY << " | " << a.rangeYMax << "]    "
                      << std::flush;

            if (IsButtonPressed(buf))
            {
                // Wait for release
                while (true)
                {
                    if (hid_read(dev, buf, kPacketSize) > 0 && !IsButtonPressed(buf))
                        break;
                }
                std::cout << std::endl;
                break;
            }
        }
    }

    // ---- Step 3: deadzone (4 soft direction presses) -------------------
    const std::array<std::string, 4> kDirs = {"UP", "DOWN", "LEFT", "RIGHT"};
    for (const auto& dir : kDirs)
    {
        std::cout << "\n[" << handName << "] DEADZONE " << dir
                  << ": Lightly press the stick " << dir
                  << " until you just feel resistance, then release it. Press any button." << std::endl;

        WaitButton(dev, "  (press button when done)");

        // Drain buffer
        for (int i = 0; i < 10; ++i)
            hid_read_timeout(dev, buf, kPacketSize, 5);

        // Sample position after gentle press
        for (int tries = 0; tries < 200; ++tries)
        {
            if (hid_read_timeout(dev, buf, kPacketSize, 8) > 0 && buf[0] == 0x30)
            {
                uint16_t x, y;
                DecodeStick(buf, isLeft, x, y);
                a.UpdateDeadzone(x, y);
                std::cout << "  Sampled: X=" << x << " Y=" << y << std::endl;
                break;
            }
        }
    }

    return a;
}

// ---------------------------------------------------------------------------
// Build final calibration values
// ---------------------------------------------------------------------------
struct FinalCal
{
    uint16_t xMin, xCenter, xMax;
    uint16_t yMin, yCenter, yMax;
    uint16_t deadzone;
};

static FinalCal BuildFinal(const CalAccum& a)
{
    FinalCal f{};

    // Center = midpoint of soft-press deadzone measurements
    f.xCenter = static_cast<uint16_t>((static_cast<uint32_t>(a.dzXMin) + a.dzXMax) / 2);
    f.yCenter = static_cast<uint16_t>((static_cast<uint32_t>(a.dzYMin) + a.dzYMax) / 2);

    // Max extent scaled by kScale so full range is never actually hit during play
    auto scaledMax = [](uint16_t center, uint16_t raw) -> uint16_t {
        if (raw <= center) return center;
        return static_cast<uint16_t>(center + static_cast<float>(raw - center) * kScale);
    };
    auto scaledMin = [](uint16_t center, uint16_t raw) -> uint16_t {
        if (raw >= center) return center;
        return static_cast<uint16_t>(center - static_cast<float>(center - raw) * kScale);
    };

    f.xMax = scaledMax(f.xCenter, a.rangeXMax);
    f.yMax = scaledMax(f.yCenter, a.rangeYMax);
    f.xMin = scaledMin(f.xCenter, a.rangeXMin);
    f.yMin = scaledMin(f.yCenter, a.rangeYMin);

    // Deadzone = larger of X or Y soft-press deviation
    uint16_t dzX = a.dzXMax > a.dzXMin ? static_cast<uint16_t>(a.dzXMax - a.dzXMin) : 0;
    uint16_t dzY = a.dzYMax > a.dzYMin ? static_cast<uint16_t>(a.dzYMax - a.dzYMin) : 0;
    f.deadzone = std::max(dzX, dzY);
    if (f.deadzone < 20)
        f.deadzone = 20; // minimum sanity value

    return f;
}

// ---------------------------------------------------------------------------
// JSON output
// ---------------------------------------------------------------------------
static json HandToJson(const FinalCal& c)
{
    return json{
        {"x_min",    c.xMin},
        {"x_center", c.xCenter},
        {"x_max",    c.xMax},
        {"y_min",    c.yMin},
        {"y_center", c.yCenter},
        {"y_max",    c.yMax},
        {"deadzone", c.deadzone},
    };
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------
int main(int argc, char* argv[])
{
    std::string outputPath = "../../config/joyconcal.json";
#ifdef _WIN32
    {
        char exePath[MAX_PATH] = {};
        if (GetModuleFileNameA(nullptr, exePath, MAX_PATH) > 0)
        {
            outputPath = (std::filesystem::path(exePath).parent_path() / ".." / ".." / "config" / "joyconcal.json").lexically_normal().string();
        }
    }
#endif
    if (argc >= 2)
        outputPath = argv[1];

    std::cout << "=== JoyCon Stick Calibration ===" << std::endl;
    std::cout << "Output will be written to: " << outputPath << std::endl;
    std::cout << "Make sure your Joy-Con(s) are paired and connected via Bluetooth." << std::endl;
    std::cout << "Press Enter to start..." << std::endl;
    std::cin.get();

    if (hid_init() < 0)
    {
        std::cerr << "Failed to initialise hidapi." << std::endl;
        return 1;
    }

    json root;
    bool calDone = false;

    // Calibrate Left -------------------------------------------------------
    {
        struct hid_device_info* list = hid_enumerate(kNintendoVendor, kJoyConLPid);
        if (list)
        {
            std::cout << "\nFound Left Joy-Con. Connecting..." << std::endl;
            hid_device* dev = hid_open_path(list->path);
            hid_free_enumeration(list);

            if (dev)
            {
                hid_set_nonblocking(dev, 0); // blocking reads for calibration
                s_counter = 0;
                SetFullMode(dev);
                SetLed(dev, 0x01);

                std::cout << "Connected." << std::endl;
                CalAccum acc = RunCalibration(dev, true, "LEFT");
                FinalCal cal = BuildFinal(acc);

                std::cout << "\n  Left final:  X[" << cal.xMin << " " << cal.xCenter << " " << cal.xMax << "]"
                          << "  Y[" << cal.yMin << " " << cal.yCenter << " " << cal.yMax << "]"
                          << "  DZ=" << cal.deadzone << std::endl;

                root["left"] = HandToJson(cal);
                calDone = true;

                Cleanup(dev);
                hid_close(dev);
            }
            else
            {
                std::cerr << "Could not open Left Joy-Con." << std::endl;
            }
        }
        else
        {
            std::cout << "No Left Joy-Con found, skipping." << std::endl;
        }
    }

    // Calibrate Right ------------------------------------------------------
    {
        struct hid_device_info* list = hid_enumerate(kNintendoVendor, kJoyConRPid);
        if (list)
        {
            std::cout << "\nFound Right Joy-Con. Connecting..." << std::endl;
            hid_device* dev = hid_open_path(list->path);
            hid_free_enumeration(list);

            if (dev)
            {
                hid_set_nonblocking(dev, 0);
                s_counter = 0;
                SetFullMode(dev);
                SetLed(dev, 0x02);

                std::cout << "Connected." << std::endl;
                CalAccum acc = RunCalibration(dev, false, "RIGHT");
                FinalCal cal = BuildFinal(acc);

                std::cout << "\n  Right final: X[" << cal.xMin << " " << cal.xCenter << " " << cal.xMax << "]"
                          << "  Y[" << cal.yMin << " " << cal.yCenter << " " << cal.yMax << "]"
                          << "  DZ=" << cal.deadzone << std::endl;

                root["right"] = HandToJson(cal);
                calDone = true;

                Cleanup(dev);
                hid_close(dev);
            }
            else
            {
                std::cerr << "Could not open Right Joy-Con." << std::endl;
            }
        }
        else
        {
            std::cout << "No Right Joy-Con found, skipping." << std::endl;
        }
    }

    hid_exit();

    if (!calDone)
    {
        std::cerr << "\nNo Joy-Con calibrated. Make sure controllers are connected and try again." << std::endl;
        std::cerr << "Press Enter to exit..." << std::endl;
        std::cin.get();
        return 1;
    }

    // Create output directory if needed
    const std::filesystem::path outPath(outputPath);
    if (outPath.has_parent_path())
    {
        std::error_code ec;
        std::filesystem::create_directories(outPath.parent_path(), ec);
    }

    std::ofstream out(outputPath);
    if (!out.good())
    {
        std::cerr << "Failed to write " << outputPath << std::endl;
        return 1;
    }
    out << root.dump(2) << std::endl;
    out.close();

    std::cout << "\nCalibration saved to " << outputPath << std::endl;
    std::cout << "Press Enter to exit..." << std::endl;
    std::cin.get();
    return 0;
}
