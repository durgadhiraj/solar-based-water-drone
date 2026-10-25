/*
 * ============================================================================
 *  Solar Based Water Boat - ESP32 firmware
 * ============================================================================
 *  Board   : ESP32 Dev Module (ESP-WROOM-32 DevKit), Arduino-ESP32 core 3.x
 *  Library : FirebaseClient by Mobizt v2.2.13 (+ its dependency ESP_SSLClient)
 *
 *  What it does
 *   - Signs in to Firebase with Email/Password (the ESP32's own account).
 *   - Streams boat/control from the Realtime Database and switches 3 relays:
 *       left propulsion motor, right propulsion motor, conveyor motor.
 *     Motors are relay-switched: ON or OFF only. No speed control, no reverse.
 *   - Reads 4 HC-SR04 ultrasonic sensors one at a time and uploads them to
 *     boat/sensors about twice per second.
 *   - Writes boat/status (lastSeen server timestamp + actual relay states)
 *     every 2 s and immediately whenever a relay changes.
 *
 *  Safety design (see README "Failsafe" for the full description)
 *   - A separate high-priority FreeRTOS "safety" task watches Wi-Fi, Firebase
 *     communication and the website heartbeat every 50 ms and switches ALL
 *     relays OFF on its own, even if the main loop is stuck inside a network
 *     call.
 *   - After ANY failsafe (and after every boot) the ESP32 stops the stream,
 *     writes started=false / command="stop" / conveyor=false to Firebase,
 *     waits for Firebase to confirm, and only then re-opens the stream.
 *     So the boat never resumes on its own: the user must press START again.
 *   - The ESP32 task watchdog reboots the board if the program freezes.
 *
 *  Command table (only when started == true; otherwise both motors OFF)
 *     command   | LEFT motor | RIGHT motor | boat
 *     ----------+------------+-------------+-----------------
 *     "forward" |     ON     |     ON      | goes forward
 *     "left"    |     OFF    |     ON      | turns left
 *     "right"   |     ON     |     OFF     | turns right
 *     "stop"    |     OFF    |     OFF     | brake (drifts to a stop)
 *   Conveyor relay = control/conveyor (independent of started).
 * ============================================================================
 */

#include <Arduino.h>   // needed before the config section (uint8_t, HIGH/LOW)

// ============================================================================
// =========================  USER CONFIGURATION  =============================
// ============================================================================
// Fill in every value marked YOUR_... before uploading. See README.md.
// Passwords live in secrets.h (see below), not in this file.

// ---- Wi-Fi + ESP32 login: kept in secrets.h (not uploaded to GitHub) ----
// Copy secrets.example.h to secrets.h and fill in WIFI_SSID, WIFI_PASSWORD,
// ESP32_USER_EMAIL and ESP32_USER_PASSWORD there.
#if __has_include("secrets.h")
#include "secrets.h"
#else
#error "secrets.h is missing: copy secrets.example.h to secrets.h and fill in your Wi-Fi and Firebase user."
#endif

// ---- Firebase project (Project settings > General > Web API key) ----
#define FIREBASE_API_KEY       "AIzaSyCs4DNdxRw4tvyjWTZWiihyrOCHdoMY0-k"
// Realtime Database URL, e.g. "https://my-boat-default-rtdb.firebaseio.com"
// or "https://my-boat-default-rtdb.asia-southeast1.firebasedatabase.app"
#define FIREBASE_DATABASE_URL  "https://solar-water-boat-default-rtdb.firebaseio.com"

// ---- Database paths (must match index.html and README) ----
#define PATH_CONTROL           "/boat/control"
#define PATH_SENSORS           "/boat/sensors"
#define PATH_STATUS            "/boat/status"
#define PATH_BOAT              "/boat"

// ---- Pin map (must match CONNECTIONS.md) ----
// Relays: GPIO 25/26/27 stay LOW and do not glitch during boot.
const uint8_t PIN_RELAY_LEFT     = 26;   // FC65 #1 IN  -> left propulsion motor
const uint8_t PIN_RELAY_RIGHT    = 27;   // FC65 #2 IN  -> right propulsion motor
const uint8_t PIN_RELAY_CONVEYOR = 25;   // FC65 #3 IN  -> conveyor motor
const uint8_t PIN_STATUS_LED     = 2;    // onboard blue LED

// Ultrasonic sensors. ECHO pins are input-only GPIOs and each ECHO line goes
// through a 1k/2k voltage divider (HC-SR04 ECHO is 5 V, ESP32 is 3.3 V).
const uint8_t PIN_FL_TRIG = 16, PIN_FL_ECHO = 34;   // Front-Left
const uint8_t PIN_FR_TRIG = 17, PIN_FR_ECHO = 35;   // Front-Right
const uint8_t PIN_BL_TRIG = 18, PIN_BL_ECHO = 36;   // Back-Left  (VP)
const uint8_t PIN_BR_TRIG = 19, PIN_BR_ECHO = 39;   // Back-Right (VN)

// FC65 jumper set to HIGH-level trigger: GPIO HIGH = relay ON.
#define RELAY_ON_LEVEL   HIGH
#define RELAY_OFF_LEVEL  LOW

// ---- Failsafe timeouts ----
const uint32_t FIREBASE_SILENCE_TIMEOUT_MS = 3000;  // no successful Firebase traffic -> all OFF
const uint32_t HEARTBEAT_TIMEOUT_MS        = 5000;  // webHeartbeat unchanged while a relay is ON -> all OFF
const uint32_t SAFETY_CHECK_INTERVAL_MS    = 50;    // how often the safety task checks
const uint32_t WATCHDOG_TIMEOUT_S          = 15;    // task watchdog: frozen program -> reboot

// ---- Timing ----
const uint32_t STATUS_INTERVAL_MS          = 2000;  // status/lastSeen + relays
const uint32_t SENSOR_UPLOAD_INTERVAL_MS   = 500;   // boat/sensors (about 2x per second)
const uint32_t SENSOR_SLOT_MS              = 60;    // one sensor per 60 ms slot (full cycle 240 ms)
const uint32_t ECHO_TIMEOUT_US             = 30000; // pulseIn timeout (~5 m round trip)
const uint32_t WIFI_RETRY_INTERVAL_MS      = 20000; // backup reconnect attempt while down
const uint32_t RELINK_DELAY_MS             = 1000;  // wait before re-linking after a failsafe
const uint32_t RESET_ACK_TIMEOUT_MS        = 8000;  // control reset must be confirmed within this
const uint32_t STREAM_START_TIMEOUT_MS     = 10000; // stream must deliver first data within this
const uint32_t WRITE_STUCK_TIMEOUT_MS      = 8000;  // abort a write that never completes

// ---- Sensor limits ----
const int SENSOR_MIN_CM     = 2;    // HC-SR04 usable range
const int SENSOR_MAX_CM     = 400;
const int SENSOR_MISS_LIMIT = 3;    // report -1 only after this many misses in a row

// ---- Serial log ----
#define SERIAL_BAUD        115200
#define LOG_SENSORS_EVERY_MS 0      // e.g. 2000 to print sensor values; 0 = off

#define FIRMWARE_VERSION   "1.0.0"
// ============================================================================
// ======================  END OF USER CONFIGURATION  =========================
// ============================================================================

#define ENABLE_USER_AUTH
#define ENABLE_DATABASE

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <FirebaseClient.h>
#include <esp_task_wdt.h>

// ---------------------------------------------------------------------------
// Firebase objects
// ---------------------------------------------------------------------------
WiFiClientSecure sslClient;        // used for all writes
WiFiClientSecure streamSslClient;  // used only for the control stream

using AsyncClient = AsyncClientClass;
AsyncClient aClient(sslClient);
AsyncClient streamClient(streamSslClient);

UserAuth userAuth(FIREBASE_API_KEY, ESP32_USER_EMAIL, ESP32_USER_PASSWORD, 3000 /* token refresh period, s */);
FirebaseApp app;
RealtimeDatabase Database;

// ---------------------------------------------------------------------------
// Relay state (shared between the main loop and the safety task)
// ---------------------------------------------------------------------------
SemaphoreHandle_t relayMutex = nullptr;
volatile bool relayLeft = false, relayRight = false, relayConveyor = false;

// controlEnabled is true only while the link is healthy AND the control state
// has been reset after boot/failsafe. Relays can only turn ON while it is true.
volatile bool controlEnabled = false;

volatile bool failsafePending = false;          // set by triggerFailsafe(), handled in loop()
volatile const char *failsafeReason = "";
volatile uint32_t lastFirebaseOkMs = 0;         // last successful write ack or stream event
volatile uint32_t lastHeartbeatChangeMs = 0;    // last time control/webHeartbeat changed
volatile bool statusDirty = false;              // relay changed -> send status now

uint32_t failsafeCount = 0;

// ---------------------------------------------------------------------------
// Control mirror (what boat/control currently contains, from the stream)
// ---------------------------------------------------------------------------
struct ControlState {
  bool started = false;
  String command = "stop";
  bool conveyor = false;
  String heartbeat = "";   // raw value; we only care whether it changes
};
ControlState ctl;
String lastLoggedControl = "";

// ---------------------------------------------------------------------------
// Link state machine
// ---------------------------------------------------------------------------
enum LinkState { LINK_DOWN, LINK_RESETTING, LINK_STREAM_STARTING, LINK_ONLINE };
LinkState linkState = LINK_DOWN;
uint32_t linkStateSinceMs = 0;
volatile bool resetAcked = false, resetFailed = false;
volatile bool streamGotData = false, streamLost = false;
bool appWasReady = false;
// Each link attempt gets unique task IDs, so late callbacks from a cancelled
// reset write or stream (from a previous attempt) are ignored.
uint32_t linkGeneration = 0;
String resetUid = "", streamUid = "";

// Write-in-flight tracking (never queue more than one of each)
bool sensorWriteBusy = false, statusWriteBusy = false;
uint32_t sensorWriteStartMs = 0, statusWriteStartMs = 0;
uint32_t lastSensorUploadMs = 0, lastStatusMs = 0;

// Wi-Fi
bool wifiWasUp = false;
uint32_t lastWifiAttemptMs = 0;
volatile uint8_t wifiLastReason = 0;      // last disconnect reason from the driver
volatile bool wifiReasonPending = false;
uint32_t lastWifiReasonLogMs = 0;

// ---------------------------------------------------------------------------
// Ultrasonic sensors
// ---------------------------------------------------------------------------
struct UltraSensor {
  const char *key;     // database key
  const char *label;   // log label
  uint8_t trig, echo;
  int cm;              // last reported value, -1 = no reading
  int misses;
};
UltraSensor sensors[4] = {
  {"frontLeft",  "FRONT-LEFT",  PIN_FL_TRIG, PIN_FL_ECHO, -1, 0},
  {"frontRight", "FRONT-RIGHT", PIN_FR_TRIG, PIN_FR_ECHO, -1, 0},
  {"backLeft",   "BACK-LEFT",   PIN_BL_TRIG, PIN_BL_ECHO, -1, 0},
  {"backRight",  "BACK-RIGHT",  PIN_BR_TRIG, PIN_BR_ECHO, -1, 0},
};
uint8_t sensorIndex = 0;
uint32_t lastSensorSlotMs = 0, lastSensorLogMs = 0;

// ---------------------------------------------------------------------------
// Prototypes
// ---------------------------------------------------------------------------
void onAuthEvent(AsyncResult &r);
void onWriteResult(AsyncResult &r);
void onStreamEvent(AsyncResult &r);
void triggerFailsafe(const char *reason);
void applyControlToRelays();

// ===========================================================================
// Small helpers
// ===========================================================================
const char *linkStateName(LinkState s) {
  switch (s) {
    case LINK_DOWN:            return "DOWN";
    case LINK_RESETTING:       return "RESETTING CONTROL";
    case LINK_STREAM_STARTING: return "STARTING STREAM";
    case LINK_ONLINE:          return "ONLINE";
  }
  return "?";
}

void setLinkState(LinkState s) {
  if (s != linkState) Serial.printf("[LINK] %s -> %s\n", linkStateName(linkState), linkStateName(s));
  linkState = s;
  linkStateSinceMs = millis();
}

void markFirebaseOk() { lastFirebaseOkMs = millis(); }

bool anyRelayOn() { return relayLeft || relayRight || relayConveyor; }

// Elapsed time since a timestamp that another task may update at any moment.
// Read the timestamp first, then millis(), so the result can never underflow.
uint32_t elapsedSince(volatile uint32_t &stamp) {
  uint32_t t = stamp;
  uint32_t now = millis();
  return (now >= t) ? (now - t) : 0;
}

// ---- tiny JSON helpers for the flat boat/control object ----
// Returns the raw token for "key" (e.g. true, 12, "forward") or "" if absent.
String jsonRawValue(const String &json, const char *key) {
  String needle = String("\"") + key + "\"";
  int k = json.indexOf(needle);
  if (k < 0) return "";
  int colon = json.indexOf(':', k + needle.length());
  if (colon < 0) return "";
  int i = colon + 1;
  while (i < (int)json.length() && isspace((unsigned char)json[i])) i++;
  if (i >= (int)json.length()) return "";
  if (json[i] == '"') {
    int end = json.indexOf('"', i + 1);
    return end < 0 ? "" : json.substring(i, end + 1);
  }
  int end = i;
  while (end < (int)json.length() && json[end] != ',' && json[end] != '}') end++;
  String v = json.substring(i, end);
  v.trim();
  return v;
}

String unquote(String v) {
  v.trim();
  if (v.length() >= 2 && v[0] == '"' && v[v.length() - 1] == '"') return v.substring(1, v.length() - 1);
  return v;
}

bool rawToBool(const String &v) { return v == "true" || v == "1"; }

// ===========================================================================
// Relays
// ===========================================================================
// Only place that touches relay pins. Protected by a mutex so the safety task
// and the main loop can never interleave a check-then-write.
void writeRelays(bool left, bool right, bool conveyor, bool force = false) {
  bool changed;
  xSemaphoreTake(relayMutex, portMAX_DELAY);
  if (!controlEnabled && !force) { left = right = conveyor = false; }
  changed = (left != relayLeft) || (right != relayRight) || (conveyor != relayConveyor);
  digitalWrite(PIN_RELAY_LEFT,     left     ? RELAY_ON_LEVEL : RELAY_OFF_LEVEL);
  digitalWrite(PIN_RELAY_RIGHT,    right    ? RELAY_ON_LEVEL : RELAY_OFF_LEVEL);
  digitalWrite(PIN_RELAY_CONVEYOR, conveyor ? RELAY_ON_LEVEL : RELAY_OFF_LEVEL);
  relayLeft = left; relayRight = right; relayConveyor = conveyor;
  xSemaphoreGive(relayMutex);

  if (changed) {
    statusDirty = true;
    Serial.printf("[RELAY] LEFT=%s  RIGHT=%s  CONVEYOR=%s\n",
                  left ? "ON" : "off", right ? "ON" : "off", conveyor ? "ON" : "off");
  }
}

void allRelaysOff() { writeRelays(false, false, false, true); }

// Called from the safety task OR the main loop. Switches everything OFF at
// once and disables control until the link has been re-established and the
// control state has been reset in Firebase.
void triggerFailsafe(const char *reason) {
  bool wasEnabled;
  xSemaphoreTake(relayMutex, portMAX_DELAY);
  wasEnabled = controlEnabled;
  controlEnabled = false;
  digitalWrite(PIN_RELAY_LEFT,     RELAY_OFF_LEVEL);
  digitalWrite(PIN_RELAY_RIGHT,    RELAY_OFF_LEVEL);
  digitalWrite(PIN_RELAY_CONVEYOR, RELAY_OFF_LEVEL);
  bool wereOn = relayLeft || relayRight || relayConveyor;
  relayLeft = false; relayRight = false; relayConveyor = false;
  if (wasEnabled) {
    failsafeReason = reason;
    failsafePending = true;
  }
  xSemaphoreGive(relayMutex);

  if (wasEnabled) {
    Serial.printf("\n[FAILSAFE] %s -> ALL RELAYS OFF%s\n", reason, wereOn ? " (motors were running)" : "");
  }
}

// Turn the mirrored control state into relay outputs (command table).
void applyControlToRelays() {
  bool wantLeft = false, wantRight = false;
  if (ctl.started) {
    if (ctl.command == "forward")      { wantLeft = true;  wantRight = true;  }
    else if (ctl.command == "left")    { wantLeft = false; wantRight = true;  }
    else if (ctl.command == "right")   { wantLeft = true;  wantRight = false; }
    else                               { wantLeft = false; wantRight = false; } // "stop" or unknown
  }
  bool wantConveyor = ctl.conveyor;

  // Log every change of the requested control state.
  String summary = String("started=") + (ctl.started ? "true" : "false") +
                   " command=" + ctl.command + " conveyor=" + (ctl.conveyor ? "true" : "false");
  if (summary != lastLoggedControl) {
    lastLoggedControl = summary;
    Serial.printf("[CMD] %s%s\n", summary.c_str(), controlEnabled ? "" : "  (ignored: control not enabled)");
  }

  if (!controlEnabled) { allRelaysOff(); return; }

  // Never switch anything ON if the website heartbeat is already stale.
  if ((wantLeft || wantRight || wantConveyor) && elapsedSince(lastHeartbeatChangeMs) > HEARTBEAT_TIMEOUT_MS) {
    triggerFailsafe("Command received but website heartbeat is stale (>5 s)");
    return;
  }
  writeRelays(wantLeft, wantRight, wantConveyor);
}

// ===========================================================================
// Safety task: runs independently of loop() every 50 ms
// ===========================================================================
void safetyTask(void *) {
  esp_task_wdt_add(NULL);
  for (;;) {
    esp_task_wdt_reset();
    if (controlEnabled) {
      if (!WiFi.isConnected()) {
        triggerFailsafe("Wi-Fi disconnected");
      } else if (elapsedSince(lastFirebaseOkMs) > FIREBASE_SILENCE_TIMEOUT_MS) {
        triggerFailsafe("No successful Firebase communication for 3 s");
      } else if (anyRelayOn() && elapsedSince(lastHeartbeatChangeMs) > HEARTBEAT_TIMEOUT_MS) {
        triggerFailsafe("Website heartbeat unchanged for 5 s (page closed or phone offline)");
      }
    }
    vTaskDelay(pdMS_TO_TICKS(SAFETY_CHECK_INTERVAL_MS));
  }
}

// ===========================================================================
// Wi-Fi
// ===========================================================================
// Called by the Wi-Fi driver task; just record the reason, loop() prints it.
void onWifiDisconnected(WiFiEvent_t, WiFiEventInfo_t info) {
  wifiLastReason = info.wifi_sta_disconnected.reason;
  wifiReasonPending = true;
}

const char *wifiReasonHint(uint8_t reason) {
  switch (reason) {
    case WIFI_REASON_NO_AP_FOUND:
    case WIFI_REASON_NO_AP_FOUND_W_COMPATIBLE_SECURITY:
    case WIFI_REASON_NO_AP_FOUND_IN_AUTHMODE_THRESHOLD:
    case WIFI_REASON_NO_AP_FOUND_IN_RSSI_THRESHOLD:
      return "Network not found: check WIFI_SSID (capitals and spaces must match), "
             "that it is 2.4 GHz (not 5 GHz) and that it is in range";
    case WIFI_REASON_AUTH_FAIL:
    case WIFI_REASON_4WAY_HANDSHAKE_TIMEOUT:
    case WIFI_REASON_HANDSHAKE_TIMEOUT:
    case WIFI_REASON_AUTH_EXPIRE:
      return "Password rejected: check WIFI_PASSWORD. On a phone hotspot use WPA2 "
             "(not WPA3-only) and turn on 'Maximise compatibility' / 2.4 GHz";
    case WIFI_REASON_ASSOC_FAIL:
    case WIFI_REASON_CONNECTION_FAIL:
      return "Router refused the connection: too many devices, MAC filter, or weak signal";
    case WIFI_REASON_BEACON_TIMEOUT:
      return "Signal lost: move closer to the router/hotspot";
    default:
      return "See reason name above";
  }
}

void handleWiFi() {
  bool up = (WiFi.status() == WL_CONNECTED);
  if (up != wifiWasUp) {
    wifiWasUp = up;
    if (up) {
      Serial.printf("[WIFI] Connected. IP %s  RSSI %d dBm\n", WiFi.localIP().toString().c_str(), WiFi.RSSI());
    } else {
      Serial.println("[WIFI] Disconnected");
      triggerFailsafe("Wi-Fi disconnected");
    }
  }

  // Explain why the connection failed (printed at most every 5 s).
  if (wifiReasonPending && millis() - lastWifiReasonLogMs > 5000) {
    wifiReasonPending = false;
    lastWifiReasonLogMs = millis();
    uint8_t reason = wifiLastReason;
    Serial.printf("[WIFI] Not connected. Reason %u (%s)\n       -> %s\n", reason,
                  WiFi.STA.disconnectReasonName((wifi_err_reason_t)reason), wifiReasonHint(reason));
  }

  // Backup retry. The driver already auto-reconnects; never call WiFi.begin()
  // again here, because that interrupts an attempt that is still running.
  if (!up && millis() - lastWifiAttemptMs > WIFI_RETRY_INTERVAL_MS) {
    lastWifiAttemptMs = millis();
    Serial.printf("[WIFI] Still trying to connect to \"%s\"...\n", WIFI_SSID);
    WiFi.reconnect();
  }
}

// One-time scan at boot so the Serial Monitor shows whether the configured
// network is visible at all (the ESP32 only sees 2.4 GHz networks).
void scanForNetwork() {
  Serial.println("[WIFI] Scanning for 2.4 GHz networks...");
  int n = WiFi.scanNetworks();
  bool found = false, foundOtherCase = false;
  for (int i = 0; i < n; i++) {
    String ssid = WiFi.SSID(i);
    bool match = (ssid == WIFI_SSID);
    found |= match;
    foundOtherCase |= (!match && ssid.equalsIgnoreCase(WIFI_SSID));
    if (i < 12) {
      Serial.printf("       %s \"%s\"  ch %d  %d dBm\n", match ? "->" : "  ", ssid.c_str(), (int)WiFi.channel(i), (int)WiFi.RSSI(i));
    }
  }
  if (n <= 0) Serial.println("       (no networks found)");
  if (found) {
    Serial.printf("[WIFI] \"%s\" found.\n", WIFI_SSID);
  } else if (foundOtherCase) {
    Serial.printf("[WIFI] \"%s\" NOT found, but a network with different capitals is. "
                  "WIFI_SSID must match exactly.\n", WIFI_SSID);
  } else {
    Serial.printf("[WIFI] \"%s\" NOT found. Check the name, that it is 2.4 GHz, and that the hotspot is on.\n", WIFI_SSID);
  }
  WiFi.scanDelete();
}

// ===========================================================================
// Firebase link handling
// ===========================================================================
void stopFirebaseTasks() {
  streamClient.stopAsync(true);
  aClient.stopAsync(true);
  sensorWriteBusy = statusWriteBusy = false;
}

// Go back to LINK_DOWN (relays are already off at this point).
void dropLink(const char *why) {
  Serial.printf("[LINK] Dropping link: %s\n", why);
  xSemaphoreTake(relayMutex, portMAX_DELAY);
  controlEnabled = false;
  xSemaphoreGive(relayMutex);
  allRelaysOff();
  stopFirebaseTasks();
  streamGotData = false; streamLost = false;
  resetAcked = false; resetFailed = false;
  resetUid = streamUid = "";
  setLinkState(LINK_DOWN);
}

// Reset boat/control in one atomic multi-path update, and report relays OFF.
void sendControlReset() {
  resetAcked = false; resetFailed = false;
  linkGeneration++;
  resetUid = String("reset#") + linkGeneration;
  String json = "{\"control/started\":false,\"control/command\":\"stop\",\"control/conveyor\":false,"
                "\"status/relays/left\":false,\"status/relays/right\":false,\"status/relays/conveyor\":false}";
  Serial.println("[LINK] Writing started=false, command=\"stop\", conveyor=false ...");
  Database.update<object_t>(aClient, PATH_BOAT, object_t(json), onWriteResult, resetUid);
}

void startControlStream() {
  streamGotData = false; streamLost = false;
  streamUid = String("stream#") + linkGeneration;
  streamClient.setSSEFilters("get,put,patch,keep-alive,cancel,auth_revoked");
  Serial.println("[LINK] Opening stream on " PATH_CONTROL " ...");
  Database.get(streamClient, PATH_CONTROL, onStreamEvent, true /* SSE stream */, streamUid);
}

void handleLink() {
  bool wifiUp = (WiFi.status() == WL_CONNECTED);
  bool ready = wifiUp && app.ready();

  if (ready != appWasReady) {
    appWasReady = ready;
    Serial.printf("[FIREBASE] %s\n", ready ? "Signed in and ready" : "Not ready");
  }

  // A failsafe fired (safety task or elsewhere): rebuild the link from scratch.
  if (failsafePending) {
    failsafePending = false;
    failsafeCount++;
    Serial.printf("[FAILSAFE] Event #%lu handled. Boat stays stopped until START is pressed again.\n",
                  (unsigned long)failsafeCount);
    dropLink((const char *)failsafeReason);
  }

  switch (linkState) {
    case LINK_DOWN:
      if (ready && millis() - linkStateSinceMs > RELINK_DELAY_MS) {
        sendControlReset();
        setLinkState(LINK_RESETTING);
      }
      break;

    case LINK_RESETTING:
      if (!wifiUp) { dropLink("Wi-Fi lost while resetting"); }
      else if (resetAcked) {
        Serial.println("[LINK] Control state reset confirmed by Firebase");
        startControlStream();
        setLinkState(LINK_STREAM_STARTING);
      } else if (resetFailed || millis() - linkStateSinceMs > RESET_ACK_TIMEOUT_MS) {
        dropLink(resetFailed ? "control reset write failed" : "control reset not confirmed in time");
      }
      break;

    case LINK_STREAM_STARTING:
      if (!wifiUp) { dropLink("Wi-Fi lost while starting stream"); }
      else if (streamLost) { dropLink("stream error while starting"); }
      else if (streamGotData) {
        // Link is healthy and control was reset: start obeying commands.
        uint32_t now = millis();
        lastHeartbeatChangeMs = now;
        lastFirebaseOkMs = now;
        xSemaphoreTake(relayMutex, portMAX_DELAY);
        controlEnabled = true;
        xSemaphoreGive(relayMutex);
        setLinkState(LINK_ONLINE);
        Serial.println("[LINK] ONLINE. Waiting for START from the website.");
        applyControlToRelays();
        statusDirty = true;
      } else if (millis() - linkStateSinceMs > STREAM_START_TIMEOUT_MS) {
        dropLink("stream did not deliver data in time");
      }
      break;

    case LINK_ONLINE:
      if (streamLost) {
        streamLost = false;
        triggerFailsafe("Firebase stream lost");
      }
      break;
  }
}

// ===========================================================================
// Firebase callbacks
// ===========================================================================
void onAuthEvent(AsyncResult &r) {
  if (r.isError()) {
    Serial.printf("[AUTH] Error: %s (code %d)\n", r.error().message().c_str(), r.error().code());
  } else if (r.isEvent()) {
    Serial.printf("[AUTH] %s\n", r.eventLog().message().c_str());
  }
}

void onWriteResult(AsyncResult &r) {
  if (!r.isResult()) return;
  String uid = r.uid();

  if (r.isError()) {
    Serial.printf("[FIREBASE] Write '%s' failed: %s (code %d)\n", uid.c_str(),
                  r.error().message().c_str(), r.error().code());
    if (uid == resetUid) resetFailed = true;
    else if (uid == "sensorTask") sensorWriteBusy = false;
    else if (uid == "statusTask") statusWriteBusy = false;
    return;
  }
  if (r.available()) {
    markFirebaseOk();
    if (uid == resetUid) resetAcked = true;
    else if (uid == "sensorTask") sensorWriteBusy = false;
    else if (uid == "statusTask") statusWriteBusy = false;
  }
}

// Apply one stream event to the control mirror.
void applyStreamData(const String &event, const String &path, const String &data) {
  if (path == "/") {
    if (event == "put") {
      // Whole node replaced (also the first event after the stream opens).
      ctl.started = false; ctl.command = "stop"; ctl.conveyor = false;
      if (data == "null") { ctl.heartbeat = ""; return; }
    }
    String v;
    if ((v = jsonRawValue(data, "started")).length())  ctl.started  = rawToBool(v);
    if ((v = jsonRawValue(data, "command")).length())  ctl.command  = unquote(v);
    if ((v = jsonRawValue(data, "conveyor")).length()) ctl.conveyor = rawToBool(v);
    if ((v = jsonRawValue(data, "webHeartbeat")).length() && v != ctl.heartbeat) {
      ctl.heartbeat = v;
      lastHeartbeatChangeMs = millis();
    }
  } else if (path == "/started") {
    ctl.started = rawToBool(data);
  } else if (path == "/command") {
    ctl.command = (data == "null") ? String("stop") : unquote(data);
  } else if (path == "/conveyor") {
    ctl.conveyor = rawToBool(data);
  } else if (path == "/webHeartbeat") {
    if (data != ctl.heartbeat) {
      ctl.heartbeat = data;
      lastHeartbeatChangeMs = millis();
    }
  }
}

void onStreamEvent(AsyncResult &r) {
  if (!r.isResult()) return;
  if (r.uid() != streamUid) return;   // late event from a stopped stream

  if (r.isError()) {
    Serial.printf("[STREAM] Error: %s (code %d)\n", r.error().message().c_str(), r.error().code());
    if (linkState == LINK_STREAM_STARTING || linkState == LINK_ONLINE) streamLost = true;
    return;
  }
  if (!r.available()) return;

  RealtimeDatabaseResult &s = r.to<RealtimeDatabaseResult>();
  if (!s.isStream()) return;

  markFirebaseOk();
  String event = s.event();

  if (event == "cancel" || event == "auth_revoked") {
    Serial.printf("[STREAM] Server sent '%s'\n", event.c_str());
    streamLost = true;
    return;
  }
  if (event != "put" && event != "patch") return;   // keep-alive etc.

  String path = s.dataPath();
  String data = s.data();
  data.trim();
  applyStreamData(event, path, data);

  if (!streamGotData) {
    streamGotData = true;   // first full snapshot after (re)opening the stream
    Serial.println("[STREAM] Connected, first snapshot received");
  }
  if (linkState == LINK_ONLINE) applyControlToRelays();
}

// ===========================================================================
// Uploads
// ===========================================================================
void handleUploads() {
  bool canWrite = (WiFi.status() == WL_CONNECTED) && app.ready();
  uint32_t now = millis();

  // Abort writes that never finish so the queue can't block forever.
  if ((sensorWriteBusy && now - sensorWriteStartMs > WRITE_STUCK_TIMEOUT_MS) ||
      (statusWriteBusy && now - statusWriteStartMs > WRITE_STUCK_TIMEOUT_MS)) {
    Serial.println("[FIREBASE] A write is stuck; aborting pending writes");
    aClient.stopAsync(true);
    sensorWriteBusy = statusWriteBusy = false;
    if (linkState == LINK_RESETTING) resetFailed = true;
  }
  if (!canWrite) return;

  // Sensors: about twice per second, whenever Firebase is reachable.
  if (!sensorWriteBusy && now - lastSensorUploadMs >= SENSOR_UPLOAD_INTERVAL_MS) {
    lastSensorUploadMs = now;
    String json = "{";
    for (uint8_t i = 0; i < 4; i++) {
      if (i) json += ",";
      json += "\""; json += sensors[i].key; json += "\":"; json += sensors[i].cm;
    }
    json += "}";
    sensorWriteBusy = true;
    sensorWriteStartMs = now;
    Database.set<object_t>(aClient, PATH_SENSORS, object_t(json), onWriteResult, "sensorTask");
  }

  // Status: every 2 s and immediately on relay change, only while ONLINE
  // (the website uses lastSeen to decide that the boat accepts commands).
  if (linkState == LINK_ONLINE && !statusWriteBusy &&
      (statusDirty || now - lastStatusMs >= STATUS_INTERVAL_MS)) {
    statusDirty = false;
    lastStatusMs = now;
    String json = String("{\"lastSeen\":{\".sv\":\"timestamp\"},\"relays\":{") +
                  "\"left\":" + (relayLeft ? "true" : "false") +
                  ",\"right\":" + (relayRight ? "true" : "false") +
                  ",\"conveyor\":" + (relayConveyor ? "true" : "false") + "}}";
    statusWriteBusy = true;
    statusWriteStartMs = now;
    Database.update<object_t>(aClient, PATH_STATUS, object_t(json), onWriteResult, "statusTask");
  }
}

// ===========================================================================
// Ultrasonic sensors: one sensor per slot, never two at the same time
// ===========================================================================
void handleSensors() {
  uint32_t now = millis();
  if (now - lastSensorSlotMs < SENSOR_SLOT_MS) return;
  lastSensorSlotMs = now;

  UltraSensor &s = sensors[sensorIndex];
  digitalWrite(s.trig, LOW);
  delayMicroseconds(2);
  digitalWrite(s.trig, HIGH);
  delayMicroseconds(10);
  digitalWrite(s.trig, LOW);
  unsigned long us = pulseIn(s.echo, HIGH, ECHO_TIMEOUT_US);   // blocks at most ~30 ms

  int cm = -1;
  if (us > 0) {
    int d = (int)(us / 58.0f + 0.5f);   // speed of sound ~343 m/s, round trip
    if (d >= SENSOR_MIN_CM && d <= SENSOR_MAX_CM) cm = d;
  }
  if (cm > 0) {
    s.cm = cm;
    s.misses = 0;
  } else if (++s.misses >= SENSOR_MISS_LIMIT) {
    s.cm = -1;   // several misses in a row: really no reading
    s.misses = SENSOR_MISS_LIMIT;
  }

  sensorIndex = (sensorIndex + 1) % 4;

#if LOG_SENSORS_EVERY_MS > 0
  if (now - lastSensorLogMs >= (uint32_t)LOG_SENSORS_EVERY_MS) {
    lastSensorLogMs = now;
    Serial.printf("[SENSORS] FL=%d FR=%d BL=%d BR=%d cm\n", sensors[0].cm, sensors[1].cm, sensors[2].cm, sensors[3].cm);
  }
#endif
}

// ===========================================================================
// Status LED: solid = online, fast blink = Wi-Fi OK but linking to Firebase,
//             slow blink = Wi-Fi disconnected
// ===========================================================================
void handleLed() {
  bool on;
  if (linkState == LINK_ONLINE) on = true;
  else if (WiFi.status() == WL_CONNECTED) on = (millis() / 125) % 2;
  else on = (millis() / 500) % 2;
  digitalWrite(PIN_STATUS_LED, on ? HIGH : LOW);
}

// ===========================================================================
// Setup / loop
// ===========================================================================
void setup() {
  // 1) Relays OFF before anything else.
  pinMode(PIN_RELAY_LEFT, OUTPUT);
  pinMode(PIN_RELAY_RIGHT, OUTPUT);
  pinMode(PIN_RELAY_CONVEYOR, OUTPUT);
  digitalWrite(PIN_RELAY_LEFT, RELAY_OFF_LEVEL);
  digitalWrite(PIN_RELAY_RIGHT, RELAY_OFF_LEVEL);
  digitalWrite(PIN_RELAY_CONVEYOR, RELAY_OFF_LEVEL);

  pinMode(PIN_STATUS_LED, OUTPUT);
  digitalWrite(PIN_STATUS_LED, LOW);
  for (uint8_t i = 0; i < 4; i++) {
    pinMode(sensors[i].trig, OUTPUT);
    digitalWrite(sensors[i].trig, LOW);
    pinMode(sensors[i].echo, INPUT);   // GPIO 34-39 have no internal pulls; divider pulls down
  }

  Serial.begin(SERIAL_BAUD);
  delay(200);   // let the USB serial settle (setup only)
  Serial.println();
  Serial.println("==============================================");
  Serial.printf(" Solar Based Water Boat  firmware v%s\n", FIRMWARE_VERSION);
  Serial.printf(" FirebaseClient v%s\n", FIREBASE_CLIENT_VERSION);
  Serial.println(" Relays: LEFT=GPIO26 RIGHT=GPIO27 CONVEYOR=GPIO25");
  Serial.println("==============================================");
  Serial.println("[BOOT] All relays OFF. Waiting for link + control reset.");

  relayMutex = xSemaphoreCreateMutex();

  // 2) Task watchdog: a frozen program reboots the board (relays drop OFF).
  esp_task_wdt_config_t wdtConfig = {
    .timeout_ms = WATCHDOG_TIMEOUT_S * 1000,
    .idle_core_mask = 0,
    .trigger_panic = true,
  };
  if (esp_task_wdt_reconfigure(&wdtConfig) != ESP_OK) esp_task_wdt_init(&wdtConfig);
  esp_task_wdt_add(NULL);   // watch loop()

  // 3) Independent safety task (higher priority than loop()).
  xTaskCreatePinnedToCore(safetyTask, "safety", 4096, nullptr, 3, nullptr, 0);

  // 4) Wi-Fi (non-blocking; handleWiFi() keeps retrying).
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.setSleep(false);   // lower latency for commands
  WiFi.onEvent(onWifiDisconnected, ARDUINO_EVENT_WIFI_STA_DISCONNECTED);
  scanForNetwork();       // ~2-3 s, relays are OFF
  esp_task_wdt_reset();
  Serial.printf("[WIFI] Connecting to \"%s\" (password: %u characters)...\n",
                WIFI_SSID, (unsigned)strlen(WIFI_PASSWORD));
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  lastWifiAttemptMs = millis();

  // 5) Firebase (async sign-in; app.loop() drives it).
  sslClient.setInsecure();
  streamSslClient.setInsecure();
  sslClient.setHandshakeTimeout(5);
  streamSslClient.setHandshakeTimeout(5);
  initializeApp(aClient, app, getAuth(userAuth), onAuthEvent, "authTask");
  app.getApp<RealtimeDatabase>(Database);
  Database.url(FIREBASE_DATABASE_URL);

  setLinkState(LINK_DOWN);
}

void loop() {
  esp_task_wdt_reset();

  handleWiFi();
  app.loop();          // authentication + async Firebase tasks
  handleLink();
  handleSensors();
  handleUploads();
  handleLed();
}
