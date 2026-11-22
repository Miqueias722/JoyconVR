#pragma once

#include <cmath>

// Small self-contained quaternion / vector helpers (doubles, right-handed).
struct Vec3
{
    double x = 0, y = 0, z = 0;
    Vec3() = default;
    Vec3(double x_, double y_, double z_) : x(x_), y(y_), z(z_) {}
    Vec3 operator+(const Vec3& o) const { return {x + o.x, y + o.y, z + o.z}; }
    Vec3 operator-(const Vec3& o) const { return {x - o.x, y - o.y, z - o.z}; }
    Vec3 operator*(double s) const { return {x * s, y * s, z * s}; }
    Vec3 operator-() const { return {-x, -y, -z}; }
    double Dot(const Vec3& o) const { return x * o.x + y * o.y + z * o.z; }
    Vec3 Cross(const Vec3& o) const { return {y * o.z - z * o.y, z * o.x - x * o.z, x * o.y - y * o.x}; }
    double Length() const { return std::sqrt(Dot(*this)); }
    Vec3 Normalized() const
    {
        const double l = Length();
        return l > 1e-12 ? *this * (1.0 / l) : Vec3{0, 0, 0};
    }
};

struct Quat
{
    double w = 1, x = 0, y = 0, z = 0;
    Quat() = default;
    Quat(double w_, double x_, double y_, double z_) : w(w_), x(x_), y(y_), z(z_) {}

    static Quat AxisAngle(const Vec3& axis, double angle)
    {
        const Vec3 a = axis.Normalized();
        const double s = std::sin(angle * 0.5);
        return {std::cos(angle * 0.5), a.x * s, a.y * s, a.z * s};
    }

    Quat operator*(const Quat& b) const
    {
        return {
            w * b.w - x * b.x - y * b.y - z * b.z,
            w * b.x + x * b.w + y * b.z - z * b.y,
            w * b.y - x * b.z + y * b.w + z * b.x,
            w * b.z + x * b.y - y * b.x + z * b.w,
        };
    }

    Quat Conjugate() const { return {w, -x, -y, -z}; }

    Quat Normalized() const
    {
        const double l = std::sqrt(w * w + x * x + y * y + z * z);
        return l > 1e-12 ? Quat{w / l, x / l, y / l, z / l} : Quat{};
    }

    Vec3 Rotate(const Vec3& v) const
    {
        const Quat p{0, v.x, v.y, v.z};
        const Quat r = (*this) * p * Conjugate();
        return {r.x, r.y, r.z};
    }

    // Rotation whose columns are the given orthonormal basis vectors (maps local -> parent).
    static Quat FromBasis(const Vec3& cx, const Vec3& cy, const Vec3& cz)
    {
        const double m00 = cx.x, m01 = cy.x, m02 = cz.x;
        const double m10 = cx.y, m11 = cy.y, m12 = cz.y;
        const double m20 = cx.z, m21 = cy.z, m22 = cz.z;
        const double tr = m00 + m11 + m22;
        Quat q;
        if (tr > 0)
        {
            const double s = std::sqrt(tr + 1.0) * 2;
            q = {0.25 * s, (m21 - m12) / s, (m02 - m20) / s, (m10 - m01) / s};
        }
        else if (m00 > m11 && m00 > m22)
        {
            const double s = std::sqrt(1.0 + m00 - m11 - m22) * 2;
            q = {(m21 - m12) / s, 0.25 * s, (m01 + m10) / s, (m02 + m20) / s};
        }
        else if (m11 > m22)
        {
            const double s = std::sqrt(1.0 + m11 - m00 - m22) * 2;
            q = {(m02 - m20) / s, (m01 + m10) / s, 0.25 * s, (m12 + m21) / s};
        }
        else
        {
            const double s = std::sqrt(1.0 + m22 - m00 - m11) * 2;
            q = {(m10 - m01) / s, (m02 + m20) / s, (m12 + m21) / s, 0.25 * s};
        }
        return q.Normalized();
    }

    // Shortest rotation taking unit vector a onto unit vector b.
    static Quat FromTo(const Vec3& a, const Vec3& b)
    {
        const Vec3 na = a.Normalized(), nb = b.Normalized();
        const double d = na.Dot(nb);
        if (d < -0.999999)
        {
            Vec3 axis = Vec3{1, 0, 0}.Cross(na);
            if (axis.Length() < 1e-6) axis = Vec3{0, 1, 0}.Cross(na);
            return AxisAngle(axis, M_PI);
        }
        const Vec3 c = na.Cross(nb);
        return Quat{1.0 + d, c.x, c.y, c.z}.Normalized();
    }

    static Quat Slerp(const Quat& a, Quat b, double t)
    {
        double cosHalf = a.w * b.w + a.x * b.x + a.y * b.y + a.z * b.z;
        if (cosHalf < 0)
        {
            b = {-b.w, -b.x, -b.y, -b.z};
            cosHalf = -cosHalf;
        }
        if (cosHalf > 0.9995)
        {
            return Quat{a.w + (b.w - a.w) * t, a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, a.z + (b.z - a.z) * t}.Normalized();
        }
        const double half = std::acos(cosHalf);
        const double sinHalf = std::sqrt(1.0 - cosHalf * cosHalf);
        const double ra = std::sin((1 - t) * half) / sinHalf;
        const double rb = std::sin(t * half) / sinHalf;
        return {a.w * ra + b.w * rb, a.x * ra + b.x * rb, a.y * ra + b.y * rb, a.z * ra + b.z * rb};
    }
};

// Yaw (about +Y, SteamVR up) of a direction, 0 when it points at -Z (SteamVR forward).
inline double YawOf(const Vec3& dir)
{
    return std::atan2(-dir.x, -dir.z);
}
