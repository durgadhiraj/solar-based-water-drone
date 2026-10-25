// secrets.example.h - template for your private settings.
// Copy this file to secrets.h (same folder) and fill in your values.
// secrets.h is listed in .gitignore so your passwords never reach GitHub.
#pragma once

// ---- Wi-Fi (2.4 GHz network; the ESP32 cannot use 5 GHz) ----
#define WIFI_SSID              "YOUR_WIFI_SSID"
#define WIFI_PASSWORD          "YOUR_WIFI_PASSWORD"

// ---- The ESP32's own Firebase Authentication user (Email/Password) ----
#define ESP32_USER_EMAIL       "YOUR_ESP32_USER_EMAIL"
#define ESP32_USER_PASSWORD    "YOUR_ESP32_USER_PASSWORD"
