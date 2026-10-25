# Solar Based Water Boat

You control a solar-charged boat from your phone. The ESP32 on the boat switches three relays: left propulsion motor, right propulsion motor and conveyor. It also reads four ultrasonic obstacle sensors. The phone and the ESP32 talk through **Firebase Realtime Database**, and both sign in with **Firebase Email/Password Authentication**.

| File | What it is |
|---|---|
| `index.html` | The whole control website in one file (HTML + CSS + JS, Firebase SDK from the official CDN) |
| `solar_boat_esp32/solar_boat_esp32.ino` | The complete ESP32 firmware. All settings are in the config section at the top. |
| `CONNECTIONS.md` | The wiring guide for every component |
| `README.md` | This setup guide |

> **Hardware limitation the whole design is built around:** the motors are switched by relays, so each motor is only **ON or OFF**. There is no speed control and no reverse. Turning works by running one motor only. "Back / Brake" switches both motors off and lets the boat coast to a stop.

---

## Contents

1. [How it works](#1-how-it-works)
2. [Values you need to fill in](#2-values-you-need-to-fill-in)
3. [Firebase setup](#3-firebase-setup)
4. [Database structure (final)](#4-database-structure-final)
5. [Security rules](#5-security-rules)
6. [Arduino IDE: board and library](#6-arduino-ide-board-and-library)
7. [Configure and upload the firmware](#7-configure-and-upload-the-firmware)
8. [Configure, open or host the website](#8-configure-open-or-host-the-website)
9. [Safe bench-test procedure](#9-safe-bench-test-procedure)
10. [Test checklist](#10-test-checklist)
11. [Failsafe reference](#11-failsafe-reference)
12. [Settings that must match across files](#12-settings-that-must-match-across-files)
13. [Troubleshooting](#13-troubleshooting)
14. [Assumptions and limitations](#14-assumptions-and-limitations)

---

## 1. How it works

```
  Phone (index.html)                 Firebase Realtime Database                ESP32 on the boat
  ------------------                 ---------------------------               -----------------
  START / D-pad / conveyor  ---->    boat/control   (started, command,  ----->  stream (instant)
  heartbeat +1 every 1 s    ---->                    conveyor, webHeartbeat)    -> relays
                                                                               
  sensor cards + banner     <----    boat/sensors   (4 distances, cm)   <-----  every 0.5 s
  online dot, button states <----    boat/status    (lastSeen, relays)  <-----  every 2 s + on change
```

| Command | Left motor | Right motor | Boat |
|---|---|---|---|
| `forward` | ON | ON | Goes forward |
| `left` | OFF | ON | Turns left |
| `right` | ON | OFF | Turns right |
| `stop` (BACK / BRAKE) | OFF | OFF | Coasts to a stop (stays armed) |
| `started = false` | OFF | OFF | Locked: motors never run until START |

The conveyor relay simply follows `control/conveyor`, independently of START.

---

## 2. Values you need to fill in

| Where | Placeholder | What to put |
|---|---|---|
| `solar_boat_esp32/secrets.h` | `YOUR_WIFI_SSID` | Name of the 2.4 GHz Wi-Fi network or phone hotspot the boat uses |
| `solar_boat_esp32/secrets.h` | `YOUR_WIFI_PASSWORD` | Its password |
| `solar_boat_esp32.ino` | `YOUR_FIREBASE_API_KEY` | Firebase **Web API key** (step 3.2) |
| `solar_boat_esp32.ino` | `YOUR_FIREBASE_DATABASE_URL` | Realtime Database URL (step 3.3), e.g. `https://my-boat-default-rtdb.firebaseio.com` |
| `solar_boat_esp32/secrets.h` | `YOUR_ESP32_USER_EMAIL` | Email of the ESP32's Firebase user (step 3.4) |
| `solar_boat_esp32/secrets.h` | `YOUR_ESP32_USER_PASSWORD` | Password of the ESP32's Firebase user |
| `index.html` | `YOUR_FIREBASE_API_KEY` | Same Web API key |
| `index.html` | `YOUR_PROJECT_ID.firebaseapp.com` | `authDomain` from the web app config |
| `index.html` | `YOUR_FIREBASE_DATABASE_URL` | Same database URL |
| `index.html` | `YOUR_PROJECT_ID` | `projectId` from the web app config |
| Database rules (optional, recommended) | `YOUR_WEB_USER_UID`, `YOUR_ESP32_USER_UID` | The two user UIDs (step 3.4) |

The Firebase web API key is not a secret: it only identifies the project, and the security rules protect the data. The Wi-Fi password and the ESP32 user password **are** secrets. They live only in `solar_boat_esp32/secrets.h`, which `.gitignore` keeps out of GitHub. Never upload `secrets.h` anywhere.

---

## 3. Firebase setup

### 3.1 Create the project
1. Go to <https://console.firebase.google.com> and click **Create a project** (or **Add project**).
2. Name it, e.g. `solar-water-boat`. Google Analytics is not needed, so you can switch it off.
3. Wait for the project to be created and click **Continue**.

### 3.2 Register a web app (gets you the config)
1. On the project overview page, click the **Web** icon `</>`.
2. App nickname: `boat-website`. Leave "Firebase Hosting" unticked for now (you can add it later).
3. Click **Register app**. Firebase shows a `firebaseConfig` object. Copy `apiKey`, `authDomain`, `databaseURL` (it may only appear after step 3.3) and `projectId`.
4. You can find this again later under **Project settings** (gear icon) > **General** > **Your apps**.

### 3.3 Create the Realtime Database
1. In the left menu: **Build > Realtime Database > Create Database**.
2. Choose a location close to you. Note it: the URL depends on it.
3. Choose **Start in locked mode**. You will paste the real rules in step 5.
4. Copy the database URL shown at the top of the **Data** tab, e.g.
   `https://solar-water-boat-default-rtdb.firebaseio.com` or
   `https://solar-water-boat-default-rtdb.asia-southeast1.firebasedatabase.app`.
   Use it **without** a trailing slash.

> Use **Realtime Database**, not Firestore.

### 3.4 Authentication and the two users
1. **Build > Authentication > Get started**.
2. **Sign-in method** tab > **Email/Password** > **Enable** (the first switch only) > **Save**.
3. **Users** tab > **Add user**. Create:

   | User | Example email | Used by |
   |---|---|---|
   | Website user | `captain@example.com` | You, on the phone login card |
   | ESP32 user | `esp32-boat@example.com` | The firmware (`ESP32_USER_EMAIL` / `ESP32_USER_PASSWORD`) |

   The emails don't need to be real inboxes. Use strong passwords (at least 8 characters).
4. Copy each user's **User UID** from the Users table. You need them for the recommended rules.
5. Recommended: under **Authentication > Settings > User actions**, untick **Enable create (sign-up)** if your project shows that option. This stops strangers creating accounts with your public API key. The UID-locked rules in step 5.2 protect you even if you cannot disable it.

### 3.5 Initial data (optional)
You don't need to create any data by hand. On boot, the ESP32 writes `boat/control` (started=false, command="stop", conveyor=false) and `boat/status`, and the website creates `webHeartbeat`.

---

## 4. Database structure (final)

```
boat/
  control/
    started:      true | false        website: START sets true, STOP SYSTEM sets false
    command:      "forward" | "left" | "right" | "stop"
    conveyor:     true | false
    webHeartbeat: number              website: +1 every 1 s (server-side increment)
  sensors/
    frontLeft:    number (cm)         integer cm, -1 = no reading
    frontRight:   number (cm)
    backLeft:     number (cm)
    backRight:    number (cm)
  status/
    lastSeen:     server timestamp    ESP32: every 2 s, only while it accepts commands
    relays/
      left:       true | false        ESP32: ACTUAL relay output states
      right:      true | false
      conveyor:   true | false
```

Refinements compared with the original spec (the structure itself is unchanged):
- **`status` is also written immediately when a relay changes**, not only every 2 s. The website therefore shows "Sending..." for well under a second instead of up to 2 s.
- **The ESP32's control reset is one atomic multi-path update** at `boat/`, which sets `control/started=false`, `control/command="stop"`, `control/conveyor=false` **and** `status/relays/*=false` together.
- **`lastSeen` is only written while the ESP32 is fully linked** (control reset done and stream open). So "ESP32 Online" on the website means the boat really accepts commands.
- **Sensor values are integer centimetres.** A sensor reports `-1` only after 3 missed echoes in a row, so a single dropped ping does not make the banner flicker.

---

## 5. Security rules

Open **Realtime Database > Rules**, replace everything with ONE of the two rule sets below, and click **Publish**.

### 5.1 Basic: any signed-in user (what the spec asks for)

```json
{
  "rules": {
    ".read": false,
    ".write": false,
    "boat": {
      ".read": "auth != null",
      ".write": "auth != null",
      "control": {
        "started":      { ".validate": "newData.isBoolean()" },
        "command":      { ".validate": "newData.isString() && (newData.val() === 'forward' || newData.val() === 'left' || newData.val() === 'right' || newData.val() === 'stop')" },
        "conveyor":     { ".validate": "newData.isBoolean()" },
        "webHeartbeat": { ".validate": "newData.isNumber()" }
      },
      "sensors": {
        "$sensor": { ".validate": "newData.isNumber()" }
      },
      "status": {
        "lastSeen": { ".validate": "newData.isNumber()" },
        "relays": {
          "$relay": { ".validate": "newData.isBoolean()" }
        }
      }
    }
  }
}
```

### 5.2 Recommended: only your two users

Firebase's Email/Password sign-up can be called by anyone who has your (public) API key. If sign-up is not disabled (step 3.4, item 5), a stranger could create an account and count as "authenticated". These rules allow only your two UIDs:

```json
{
  "rules": {
    ".read": false,
    ".write": false,
    "boat": {
      ".read":  "auth != null && (auth.uid === 'YOUR_WEB_USER_UID' || auth.uid === 'YOUR_ESP32_USER_UID')",
      ".write": "auth != null && (auth.uid === 'YOUR_WEB_USER_UID' || auth.uid === 'YOUR_ESP32_USER_UID')",
      "control": {
        "started":      { ".validate": "newData.isBoolean()" },
        "command":      { ".validate": "newData.isString() && (newData.val() === 'forward' || newData.val() === 'left' || newData.val() === 'right' || newData.val() === 'stop')" },
        "conveyor":     { ".validate": "newData.isBoolean()" },
        "webHeartbeat": { ".validate": "newData.isNumber()" }
      },
      "sensors": {
        "$sensor": { ".validate": "newData.isNumber()" }
      },
      "status": {
        "lastSeen": { ".validate": "newData.isNumber()" },
        "relays": {
          "$relay": { ".validate": "newData.isBoolean()" }
        }
      }
    }
  }
}
```

Replace `YOUR_WEB_USER_UID` and `YOUR_ESP32_USER_UID` with the UIDs from step 3.4. To add a second phone user, add another `|| auth.uid === '...'`.

`.info/connected` and `.info/serverTimeOffset`, which the website reads, are always readable and need no rule.

---

## 6. Arduino IDE: board and library

Tested combination (the firmware was compiled with exactly these versions):

| Item | Version |
|---|---|
| Arduino IDE | 2.x (arduino-cli 1.5.1 inside the IDE) |
| Board package **esp32 by Espressif Systems** | **3.3.12** (any 3.x should work; 2.x will **not** compile because the watchdog API changed) |
| Library **FirebaseClient** by **Mobizt** | **2.2.13** |
| Library **ESP_SSLClient** by Mobizt (installed automatically as a dependency) | 3.1.3 |

### 6.1 Install the ESP32 board package
1. **File > Preferences > Additional boards manager URLs**, add:
   `https://espressif.github.io/arduino-esp32/package_esp32_index.json`
2. **Tools > Board > Boards Manager**, search **esp32**, install **esp32 by Espressif Systems** (3.3.12 or newer 3.x).
3. **Tools > Board > esp32 > ESP32 Dev Module**.

### 6.2 Install the Firebase library
1. **Sketch > Include Library > Manage Libraries**.
2. Search **FirebaseClient**. Install **FirebaseClient by Mobizt, version 2.2.13**. When asked, click **Install all** to include ESP_SSLClient.

> Do **not** use the older "Firebase Arduino Client Library for ESP8266 and ESP32" (Firebase-ESP-Client). It is deprecated and has a different API. You can leave it installed (it does not conflict, checked), but this sketch uses **FirebaseClient**.

### 6.3 Board settings (Tools menu)

| Setting | Value |
|---|---|
| Board | ESP32 Dev Module |
| Upload Speed | 921600 (use 115200 if uploads fail) |
| CPU Frequency | 240 MHz (WiFi/BT) |
| Flash Size | 4MB (32Mb) |
| Partition Scheme | **Default 4MB with spiffs (1.2MB APP/1.5MB SPIFFS)**. The sketch uses about 1.05 MB (79%). |
| Port | The COM port of your ESP32 |

If no COM port appears, install the USB driver for your board's USB chip: **CP210x** (Silicon Labs) or **CH340** (WCH).

---

## 7. Configure and upload the firmware

1. Open `solar_boat_esp32/solar_boat_esp32.ino` in the Arduino IDE. The folder name must stay `solar_boat_esp32`.
2. In the `solar_boat_esp32` folder, copy `secrets.example.h` to **`secrets.h`** and fill in your Wi-Fi name and password and the ESP32 user's email and password. `secrets.h` is listed in `.gitignore`, so these passwords are never uploaded to GitHub. Then check `FIREBASE_API_KEY` and `FIREBASE_DATABASE_URL` in the **USER CONFIGURATION** section at the top of the `.ino` (table in section 2). The rest of that section (pins, timeouts) already matches `CONNECTIONS.md`. Only change it if you change the wiring.
3. **Safe connection for upload** (see `CONNECTIONS.md` §7): main switch **OFF**, **UBEC 5 V jumper unplugged**, then plug in USB.
4. Click **Upload**. If it stops at `Connecting........`, hold the **BOOT** button on the ESP32 until the upload starts.
5. Open **Tools > Serial Monitor** at **115200 baud**. A good start looks like this:

```
==============================================
 Solar Based Water Boat  firmware v1.0.0
 FirebaseClient v2.2.13
 Relays: LEFT=GPIO26 RIGHT=GPIO27 CONVEYOR=GPIO25
==============================================
[BOOT] All relays OFF. Waiting for link + control reset.
[WIFI] Connecting to "MyWiFi"...
[WIFI] Connected. IP 192.168.1.50  RSSI -58 dBm
[FIREBASE] Signed in and ready
[LINK] DOWN -> RESETTING CONTROL
[LINK] Writing started=false, command="stop", conveyor=false ...
[LINK] Control state reset confirmed by Firebase
[LINK] Opening stream on /boat/control ...
[LINK] RESETTING CONTROL -> STARTING STREAM
[STREAM] Connected, first snapshot received
[LINK] STARTING STREAM -> ONLINE
[LINK] ONLINE. Waiting for START from the website.
[CMD] started=false command=stop conveyor=false
```

When you press buttons on the website you'll see lines like:

```
[CMD] started=true command=forward conveyor=false
[RELAY] LEFT=ON  RIGHT=ON  CONVEYOR=off
```

and a failsafe looks like:

```
[FAILSAFE] Website heartbeat unchanged for 5 s (page closed or phone offline) -> ALL RELAYS OFF (motors were running)
[FAILSAFE] Event #1 handled. Boat stays stopped until START is pressed again.
[LINK] Dropping link: Website heartbeat unchanged for 5 s (page closed or phone offline)
```

**Onboard blue LED (GPIO 2):** solid = online and accepting commands; fast blink = Wi-Fi OK, connecting to Firebase; slow blink = Wi-Fi disconnected.

---

## 8. Configure, open or host the website

### 8.1 Configure
Open `index.html` in a text editor. At the top of the `<script>`, fill in `firebaseConfig`:

```js
const firebaseConfig = {
  apiKey:      "AIza...",
  authDomain:  "solar-water-boat.firebaseapp.com",
  databaseURL: "https://solar-water-boat-default-rtdb.firebaseio.com",
  projectId:   "solar-water-boat",
};
```

Until these are filled in, the page shows a yellow "Firebase is not configured yet" card and a disabled login form. That is expected.

### 8.2 Try it on your computer
Firebase's ES modules do not load reliably from `file://`, so serve the folder over HTTP:

```bash
python -m http.server 8000
```

Then open <http://localhost:8000> in a browser. This is fine for testing, but the phone needs a hosted copy (8.3).

### 8.3 Host it for the phone (Firebase Hosting, free, HTTPS)
HTTPS is needed so the phone can keep the screen awake while the boat is armed.

> ⚠ Deploy **only `index.html`**. Do **not** deploy the project folder: `solar_boat_esp32/secrets.h` contains your Wi-Fi password and the ESP32 user's password.

1. Install Node.js from <https://nodejs.org>, then install the Firebase CLI:
   ```bash
   npm install -g firebase-tools
   ```
2. Make a new empty folder, e.g. `boat-site`, and copy `index.html` into `boat-site/public/`.
3. In `boat-site`:
   ```bash
   firebase login
   ```
   ```bash
   firebase init hosting
   ```
   Pick **Use an existing project** and select your boat project. Public directory: `public`. Single-page app: **No**. GitHub deploys: **No**. Do **not** overwrite `public/index.html`.
4. Deploy:
   ```bash
   firebase deploy --only hosting
   ```
5. Open the printed URL (`https://<project-id>.web.app`) on your phone. Use the browser menu's **Add to Home screen** to get an app-like icon.

Any other static HTTPS host (GitHub Pages, Netlify drop) also works. It only needs to serve the single `index.html`.

### 8.4 Using it
1. Log in with the **website user** (not the ESP32 user).
2. Wait for the green **ESP32 Online** dot.
3. **START** arms the system and drives forward. The button becomes **STOP SYSTEM**.
4. The D-pad is latching: one tap holds until you tap another. The yellow button and "Current: ..." show what the relays are **actually** doing. A pulsing button labelled "Sending..." means the ESP32 has not confirmed yet.
5. **CONVEYOR** toggles the conveyor relay. It works without START, but needs the ESP32 to be online.
6. **Keep the page open and in the foreground** while the boat moves. If the phone locks, the page is closed or the phone loses internet, the heartbeat stops and the boat switches everything OFF within 5 s. The page asks the browser to keep the screen on while armed.
7. **Log out** sends STOP before signing out.

---

## 9. Safe bench-test procedure

Do this on a table, with the boat out of the water.

### Step 1: Motors and propellers disconnected
1. **Remove the propellers.** Disconnect the motor wires from the relay **NO** terminals, or disconnect the motors themselves.
2. Optional but helpful: connect a 12 V test lamp, or an LED with a 1 kΩ resistor, from each relay's NO terminal to GND. Each relay's own LED and click also show its state.
3. Do the FC65 3.3 V trigger test from `CONNECTIONS.md` §6.3 for all three modules.
4. Measure the UBEC output (4.9–5.2 V) before connecting the ESP32.
5. Switch on. The Serial Monitor should reach `ONLINE` (see step 7 of this guide) and the website should show **ESP32 Online**. **No relay may click during power-up or reset.** Press the ESP32's EN/RESET button a few times and listen.

### Step 2: Test each button

| Action | Expected relays (L / R / Conveyor) | Expected website |
|---|---|---|
| Before START: tap D-pad | Buttons are greyed out, nothing happens | "Motors are locked until you press START." |
| START | ON / ON / – | Button turns red "STOP SYSTEM", Current: FORWARD |
| LEFT | **OFF / ON** / – | LEFT highlighted, Current: LEFT |
| RIGHT | **ON / OFF** / – | RIGHT highlighted, Current: RIGHT |
| BACK / BRAKE | OFF / OFF / – | BRAKE highlighted, still armed |
| FORWARD (after brake) | ON / ON / – | FORWARD works again without START |
| CONVEYOR | – / – / ON | "CONVEYOR: ON" in yellow |
| CONVEYOR again | – / – / OFF | "CONVEYOR: OFF" |
| CONVEYOR without START | – / – / ON | Works while disarmed |
| STOP SYSTEM | OFF / OFF / OFF | Back to green START, D-pad greyed out |

Each change should take about 1 second or less. Watch for "Sending..." disappearing.

### Step 3: Test each failsafe
Before each test: press START (and switch the conveyor ON) so relays are running.

| # | Test | Expected result |
|---|---|---|
| 1 | **Switch off the Wi-Fi router** (or the phone hotspot the boat uses) | All relays OFF **within ~1 s** (Wi-Fi loss), or within 3 s at most. Serial: `[FAILSAFE] Wi-Fi disconnected`. LED blinks slowly. Website shows **ESP32 Offline** after ~6 s and writes STOP. |
| 1b | Switch the router back on | ESP32 reconnects (Serial: `[WIFI] Connected`, `[LINK] ... ONLINE`). **Relays stay OFF.** The website shows START again. Press START to continue. |
| 2 | **Close the website** (or lock the phone) | All relays OFF **within 5 s**. Serial: `Website heartbeat unchanged for 5 s`. Reopen the page: START is shown and you must press it again. |
| 3 | **Phone in airplane mode** | Same as test 2 (relays OFF within 5 s). |
| 4 | **Unplug the ESP32** (pull the UBEC 5 V jumper) | Relays drop OFF immediately (no drive signal, and the 10 kΩ pull-downs hold IN low). Website shows Offline after ~6 s and writes STOP. Plug back in: **no relay clicks during boot**, and the boat stays stopped until START. |
| 5 | Unplug the internet connection from the router (Wi-Fi stays up) | Relays OFF within **3 s** (`No successful Firebase communication for 3 s`). |
| 6 | Pull one yellow relay signal wire while that relay is ON | That relay switches OFF (pull-down). Refit the wire, then press STOP SYSTEM and START. |
| 7 | Press the ESP32 **EN/RESET** button while driving | Relays OFF immediately and stay OFF after reboot until START. |

Only after **every** test passes:
- Reconnect the motors (still **without propellers**) and repeat steps 2 and 3. Check that each motor spins the right way. If one doesn't, swap its wires at the motor and move the flyback diode (`CONNECTIONS.md` §6.2).
- Fit the propellers. Do the first water test with the boat on a **tether line**, in calm water, within Wi-Fi range.

---

## 10. Test checklist

Tick each item.

**Firebase**
- [ ] Realtime Database created (not Firestore); URL copied without a trailing slash
- [ ] Email/Password sign-in enabled
- [ ] Website user and ESP32 user created; UIDs copied
- [ ] Rules published (5.1, or better 5.2 with your UIDs)

**Wiring** (see `CONNECTIONS.md`)
- [ ] Controller set to LI/USER: 14.4 V charge, 13.6 V float, 12.0 V LVD, 12.8 V LVR, no equalisation
- [ ] LOAD output mode = always ON; LOAD terminals show battery voltage in daylight
- [ ] 15 A fuse within 15 cm of battery +
- [ ] Battery connected to the controller before the panel
- [ ] FC65 jumpers on H; IN reads ~0 V when nothing is connected; 10 kΩ pull-downs fitted
- [ ] FC65s trigger reliably from 3.3 V, also at ~12.0 V supply
- [ ] Motors on NO (not NC); flyback diodes with the band on motor + (NO side)
- [ ] UBEC output 4.9–5.2 V, going to ESP32 VIN (not 3V3)
- [ ] Every ECHO line has a 1 kΩ / 2 kΩ divider (ECHO pin reads ≤ 3.4 V at the GPIO side)
- [ ] One common ground through controller LOAD−; no extra battery−-to-ground wire

**Firmware**
- [ ] Board package esp32 3.x, FirebaseClient 2.2.13 installed
- [ ] `secrets.h` created from `secrets.example.h` and filled in; API key and database URL set in the `.ino`
- [ ] Uploads; Serial Monitor reaches `ONLINE`; LED solid
- [ ] Sensors show plausible distances (hold a hand at 30 cm, 1 m, 2 m)

**Website**
- [ ] `firebaseConfig` filled in; page hosted over HTTPS (only `index.html` deployed)
- [ ] Login works; wrong password shows an error
- [ ] Green "ESP32 Online" dot; turns red within ~6 s when the ESP32 is unplugged
- [ ] Obstacle banner appears at ≤ 2 m with the sensor name and distance; disappears when clear
- [ ] Sensor card turns red at ≤ 2 m; "--" when no reading; greyed out when offline
- [ ] All button tests and failsafe tests in section 9 pass

---

## 11. Failsafe reference

| Trigger | Detected by | Reaction time | What happens |
|---|---|---|---|
| Wi-Fi disconnects | ESP32 safety task (checks every 50 ms) and main loop | < 0.1 s after the ESP32 notices (usually ~1 s) | All relays OFF |
| Firebase stream error / cancel / auth revoked | Stream callback | Immediate | All relays OFF |
| No successful Firebase traffic (writes or stream) for 3 s | ESP32 safety task | 3 s | All relays OFF |
| `webHeartbeat` unchanged for 5 s while any relay is ON | ESP32 safety task | 5 s | All relays OFF |
| ESP32 program freezes | Safety task (no Firebase traffic → OFF in 3 s), then task watchdog | 3 s OFF, 15 s reboot | All relays OFF, reboot |
| ESP32 loses power / resets | Hardware: relays need a HIGH signal; 10 kΩ pull-downs | Immediate | All relays OFF |
| ESP32 offline as seen by the website (lastSeen > 6 s old) | Website | 6 s | Website writes started=false, command="stop", conveyor=false |

**Recovery rule:** after **any** failsafe, and after every boot, the ESP32:
1. keeps all relays OFF and ignores commands,
2. closes the stream,
3. waits until Wi-Fi and Firebase sign-in are OK,
4. writes `started=false, command="stop", conveyor=false` (and relays all false) and **waits for Firebase to confirm it**,
5. re-opens the stream and only then obeys commands again.

So the boat never resumes on its own. Someone must press **START** again.

The safety checks run in a **separate FreeRTOS task** (priority 3, core 0). It can switch the relays off even while the main loop is blocked in a slow network call. The task watchdog (15 s) reboots the board if either task stops running.

---

## 12. Settings that must match across files

| Setting | Value | Firmware (`.ino`) | Website (`index.html`) | Docs |
|---|---|---|---|---|
| Control path | `boat/control` | `PATH_CONTROL` | `PATHS.control` | §4 |
| Sensors path | `boat/sensors` | `PATH_SENSORS` | `PATHS.sensors` | §4 |
| Status path | `boat/status` | `PATH_STATUS` | `PATHS.status` | §4 |
| Sensor keys | `frontLeft`, `frontRight`, `backLeft`, `backRight` | `sensors[]` | `SENSORS` | §4 |
| Commands | `forward`, `left`, `right`, `stop` | `applyControlToRelays()` | `data-cmd` | §1, §5 rules |
| Obstacle threshold | 200 cm (2 m) | (website only) | `OBSTACLE_CM` | §9 |
| Online window | 6 s | status every 2 s (`STATUS_INTERVAL_MS`) | `ONLINE_TIMEOUT_MS` | §11 |
| Heartbeat | every 1 s / timeout 5 s | `HEARTBEAT_TIMEOUT_MS` | `HEARTBEAT_MS` | §11 |
| Firebase silence | 3 s | `FIREBASE_SILENCE_TIMEOUT_MS` | – | §11 |
| Sensor upload | every 0.5 s | `SENSOR_UPLOAD_INTERVAL_MS` | – | §4 |
| Echo timeout | 30 ms | `ECHO_TIMEOUT_US` | – | CONNECTIONS §8 |
| Pins | see table | config section | – | CONNECTIONS §2.3 |

Final pin map: Left relay **GPIO 26**, Right relay **GPIO 27**, Conveyor relay **GPIO 25**, Front-Left **TRIG 16 / ECHO 34**, Front-Right **TRIG 17 / ECHO 35**, Back-Left **TRIG 18 / ECHO 36 (VP)**, Back-Right **TRIG 19 / ECHO 39 (VN)**, status LED **GPIO 2**.

---

## 13. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Serial shows `[AUTH] Error ... INVALID_LOGIN_CREDENTIALS` | Wrong `ESP32_USER_EMAIL` or password, or Email/Password sign-in not enabled. |
| `Write ... failed ... code 401` or `Permission denied` | Rules not published, or (rules 5.2) the UID in the rules is wrong. |
| `code -124` / invalid database URL | `FIREBASE_DATABASE_URL` must be the full `https://...` URL from the Data tab. |
| ESP32 never connects to Wi-Fi | 2.4 GHz only. Check the SSID/password spelling. Phone hotspots: enable "Maximise compatibility" on iPhone. |
| Website stuck on "ESP32 Checking..." | Not logged in yet, or rules deny reading `boat/status`. |
| Website says Offline but the ESP32 log says ONLINE | Rules deny the website user. Or the ESP32 writes to a different database URL than the website reads. Compare both URLs. |
| Website: "Could not load Firebase" | The phone has no internet, or the page was opened as `file://`. Use 8.2 or 8.3. |
| Relays click on during ESP32 boot | Jumper not on H, missing 10 kΩ pull-down, or IN wired to a different GPIO. |
| Relay sometimes does not switch | FC65 not happy at 3.3 V: use the transistor fallback (CONNECTIONS §6.4). |
| Sensor always `--` | ECHO divider wrong or missing ground. The sensor is too close to an obstacle (< 2 cm) or pointing at the open sky (> 4 m). |
| Sensor shows a short distance with nothing there | It sees the water surface or the hull. Tilt it up 5–10°. |
| ESP32 resets when a motor starts | Missing flyback diode / 0.1 µF capacitor, a weak UBEC ground, or motor ground daisy-chained through the ESP32 ground. |
| Boat stops every few minutes | The phone's screen locked or the page went to the background (heartbeat stops). Keep the page in front. |

---

## 14. Assumptions and limitations

- **Conveyor motor:** assumed to be a **12 V DC brushed geared motor**, wired exactly like the propulsion motors. Not confirmed by the spec.
- **FC65 relay module terminals:** assumed DC+ / DC− / IN on the input side, COM / NO / NC on the output side, and an H/L trigger jumper. Check your module's silkscreen.
- **Motor currents** in the power budget (CONNECTIONS §10) are **estimates** to verify against the datasheet and by measurement.
- **Main switch position:** the ON/OFF switch is placed on the **controller LOAD+ line** (not the battery line). The 15 A fuse stays close to the battery. Reason: switching the battery line off in sunlight would leave the panel connected without a battery (CONNECTIONS §4).
- **No automatic obstacle avoidance:** sensors drive the website warning only, as specified. The firmware does not stop the motors for obstacles.
- **TLS certificate checking is skipped** on the ESP32 (`setInsecure()`, as in the library's examples), because Google's certificates rotate. Authentication and database rules still apply.
- **Single operator:** the system is designed for one controlling phone at a time. Two open pages both send heartbeats and both can send commands.
- **Wi-Fi range:** the boat must stay within range of the router or hotspot. Out of range = failsafe stop (it will drift).
- **ESP32 Dev Module + Arduino-ESP32 core 3.x** only. Core 2.x has a different watchdog API.
