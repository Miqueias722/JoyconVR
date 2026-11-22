#pragma once

#include "JoyconTypes.h"
#include "MotionMath.h"

// Turns raw Joy-Con IMU frames into a SteamVR controller pose:
//  * Mahony-style filter: gyro integration + gravity correction for pitch/roll
//  * automatic gyro bias estimation whenever the controller is held still
//  * "recenter": the current pose becomes "pointing at the screen"
//  * arm model: hand position derived from orientation (shoulder -> elbow -> hand)
class MotionTracker
{
public:
    void Configure(JoyconHand hand, const HandConfig& handCfg, const ArmConfig& arm, const ImuConfig& imu);

    void AddSample(const ImuSample& sample, double dt);

    // The controller is assumed to be held pointing at the screen with the face buttons up.
    void Recenter();
    bool NeedsAutoRecenter() const { return !centered_ && samplesSinceStart_ > 150; }
    bool HasData() const { return initialized_; }

    // Orientation in SteamVR "body" space (yaw relative to the body, -Z forward, +Y up).
    Quat ControllerOrientation() const;

    // Position relative to the eyes in body space (metres).
    Vec3 HandOffsetFromHead(double extend) const;

    // For tests/diagnostics.
    Quat SensorToWorld() const { return q_; }
    Vec3 GyroBias() const { return bias_; }

private:
    Vec3 ForwardAxis() const;

    JoyconHand hand_ = JoyconHand::Left;
    HandConfig handCfg_;
    ArmConfig arm_;
    ImuConfig imu_;

    Quat q_;                    // sensor -> filter world (Z up)
    bool initialized_ = false;
    Vec3 bias_;                 // raw gyro bias (LSB)
    Vec3 gyroMean_;             // running mean of raw gyro (LSB)
    int stillCount_ = 0;
    bool biasLocked_ = false;   // a first good bias estimate exists
    long samplesSinceStart_ = 0;

    bool centered_ = false;
    Quat sensorFromController_; // controller axes -> sensor axes
    Quat yawOffset_;            // makes "forward at recenter" == -Z
};
