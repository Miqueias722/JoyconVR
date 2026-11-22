#pragma once

#include "JoyconTypes.h"

#include <string>

class JoyconConfig
{
public:
    static DriverConfig LoadFromFile(const std::string& configPath, std::string* error = nullptr);
    static DriverConfig Default();
};
