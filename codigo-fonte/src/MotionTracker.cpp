#include "MotionTracker.h"

#include <algorithm>

namespace
{
constexpr double kDegToRad = M_PI / 180.0;

// Filter world is Z-up; SteamVR world is Y-up with -Z forward. Rotation of -90 deg about X maps
// world +Z (up) -> +Y and world +Y -> -Z.
const Quat kVrFromWorld = Quat::AxisAngle({1, 0, 0}, -M_PI / 2);

Vec3 AxisFromString(const std::string& s)
{
    if (s == "-x") return {-1, 0, 0};
    if (s == "+y" || s == "y") return {0, 1, 0};
    if (s == "-y") return {0, -1, 0};
    if (s == "+z" || s == "z") return {0, 0, 1};
    if (s == "-z") return {0, 0, -1};
    return {1, 0, 0};
}
}

void MotionTracker::Configure(JoyconHand hand, const HandConfig& handCfg, const ArmConfig& arm, const ImuConfig& imu)
{
    hand_ = hand;
    handCfg_ = handCfg;
    arm_ = arm;
    imu_ = imu;
}

Vec3 MotionTracker::ForwardAxis() const
{
    return AxisFromString(handCfg_.forwardAxis);
}

void MotionTracker::AddSample(const ImuSample& s, double dt)
{
    const Vec3 rawGyro{static_cast<double>(s.gyro[0]), static_cast<double>(s.gyro[1]), static_cast<double>(s.gyro[2])};
    const Vec3 accel = Vec3{static_cast<double>(s.accel[0]), static_cast<double>(s.accel[1]), static_cast<double>(s.accel[2])} * imu_.accelScaleG;
    const double accelNorm = accel.Length();

    ++samplesSinceStart_;

    if (!initialized_)
    {
        if (accelNorm < 0.3)
        {
            return; // no usable data yet
        }
        // Align sensor "up" (accelerometer reads +1 g upwards at rest) with world +Z.
        q_ = Quat::FromTo(accel, {0, 0, 1});
        gyroMean_ = rawGyro;
        initialized_ = true;
        return;
    }

    // --- Gyro bias: when the controller is held still, slowly learn the resting gyro value.
    gyroMean_ = gyroMean_ + (rawGyro - gyroMean_) * 0.05;
    const double deviationDps = (rawGyro - gyroMean_).Length() * imu_.gyroScaleDps;
    const bool accelSteady = std::abs(accelNorm - 1.0) < 0.08;
    // A steady turn also has a low deviation, so additionally require the rate itself to be close
    // to the current bias estimate (real Joy-Con biases are only a few deg/s).
    const double rateDps = (gyroMean_ - bias_).Length() * imu_.gyroScaleDps;
    const double maxRateDps = biasLocked_ ? 4.0 : 12.0;
    if (deviationDps < 2.0 && accelSteady && rateDps < maxRateDps)
    {
        ++stillCount_;
    }
    else
    {
        stillCount_ = 0;
    }
    if (stillCount_ > 60) // ~0.3 s of stillness
    {
        const double rate = stillCount_ > 400 ? 0.01 : 0.04;
        bias_ = bias_ + (gyroMean_ - bias_) * rate;
        if (stillCount_ > 200) biasLocked_ = true;
    }

    Vec3 omega = (rawGyro - bias_) * (imu_.gyroScaleDps * kDegToRad); // rad/s, sensor frame

    // --- Gravity correction (Mahony proportional term). Trust the accelerometer less when moving.
    if (accelNorm > 0.5 && accelNorm < 1.5)
    {
        const Vec3 measuredUp = accel * (1.0 / accelNorm);
        const Vec3 predictedUp = q_.Conjugate().Rotate({0, 0, 1});
        const double trust = std::clamp(1.0 - std::abs(accelNorm - 1.0) * 4.0, 0.0, 1.0);
        omega = omega + measuredUp.Cross(predictedUp) * (imu_.tiltCorrection * trust);
    }

    // --- Integrate body rates.
    const double angle = omega.Length() * dt;
    if (angle > 1e-12)
    {
        const Quat next = (q_ * Quat::AxisAngle(omega, angle)).Normalized();
        // never keep a broken (NaN) orientation: drop the sample instead
        if (std::isfinite(next.w) && std::isfinite(next.x) && std::isfinite(next.y) && std::isfinite(next.z))
        {
            q_ = next;
        }
    }
}

void MotionTracker::Recenter()
{
    if (!initialized_)
    {
        return;
    }

    // "Up" of the controller = the sensor axis closest to gravity right now, excluding the
    // forward axis. Snapping keeps the result clean even if the user holds it a bit crooked.
    const Vec3 forward = ForwardAxis();
    const Vec3 upNow = q_.Conjugate().Rotate({0, 0, 1});

    const Vec3 candidates[] = {{1, 0, 0}, {0, 1, 0}, {0, 0, 1}};
    Vec3 up;
    double best = -1;
    for (const Vec3& c : candidates)
    {
        if (std::abs(c.Dot(forward)) > 0.5)
        {
            continue;
        }
        const double d = c.Dot(upNow);
        if (std::abs(d) > best)
        {
            best = std::abs(d);
            up = d >= 0 ? c : -c;
        }
    }
    if (handCfg_.flipUp)
    {
        up = -up;
    }

    // Controller axes (SteamVR convention): -Z points forward, +Y up, +X right.
    const Vec3 cz = -forward;
    const Vec3 cy = up;
    const Vec3 cx = cy.Cross(cz);
    sensorFromController_ = Quat::FromBasis(cx, cy, cz);

    const Quat vr = kVrFromWorld * q_ * sensorFromController_;
    const Vec3 pointing = vr.Rotate({0, 0, -1});
    yawOffset_ = Quat::AxisAngle({0, 1, 0}, -YawOf(pointing));
    centered_ = true;
}

Quat MotionTracker::ControllerOrientation() const
{
    return (yawOffset_ * kVrFromWorld * q_ * sensorFromController_).Normalized();
}

Vec3 MotionTracker::HandOffsetFromHead(double extend) const
{
    const double side = hand_ == JoyconHand::Left ? -1.0 : 1.0;
    const Vec3 shoulder{arm_.shoulderX * side, arm_.shoulderY, arm_.shoulderZ};

    const Quat r = ControllerOrientation();
    const Quat elbowRot = Quat::Slerp(Quat{}, r, std::clamp<double>(arm_.elbowFollow, 0.0, 1.0));

    const Vec3 elbow = shoulder + elbowRot.Rotate({0, -arm_.upperArm, 0});
    const Vec3 hand = elbow + r.Rotate({0, 0, -(arm_.forearm + extend)});
    return hand;
}
