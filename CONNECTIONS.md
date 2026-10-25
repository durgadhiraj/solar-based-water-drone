# Solar Based Water Boat: Wiring Guide

This guide covers every wire in the boat. Read it fully before you connect anything.

> **Safety first**
> - A LiFePO4 pack can deliver hundreds of amps into a short circuit. Fit the **fuse first**, and keep the battery disconnected until the end.
> - Do the first power-up with the **motors disconnected** (see README, "Bench test").
> - Double-check polarity before you switch on. Red is +, black is −.

---

## 1. Parts list (including the small parts you need to add)

| Qty | Part | Notes |
|---|---|---|
| 1 | 12 V 10 W polycrystalline solar panel | Vmp ≈ 18 V, Imp ≈ 0.55 A |
| 1 | 30 A LCD solar charge controller | Must have a **lithium (LI) or USER** mode (see §3) |
| 1 | Pro-Range IFR 32140FS 12.8 V 15 Ah 4S1P LiFePO4 pack | Check that it has a built-in BMS/PCM |
| 1 | UBEC 5 V / 3 A | 12 V in, 5 V out |
| 1 | ESP32 DevKit (ESP-WROOM-32) | |
| 3 | FC65 12 V 30 A optocoupler relay module | Left motor, right motor, conveyor |
| 2 | Rhino 12 V 100 RPM IG32 planetary geared motor | Left and right propulsion |
| 1 | Conveyor motor, **assumed 12 V DC geared motor** | Check that yours is 12 V DC |
| 4 | HC-SR04 ultrasonic sensor | Or JSN-SR04T waterproof version (§10) |
| 1 | 15 A blade fuse + inline fuse holder | On the battery + wire, close to the battery |
| 1 | ON/OFF switch, rated ≥ 15 A DC | Main power switch (§4) |
| 3 | Flyback diode: 1N5408, or Schottky SB560 / SR560 / MBR1045 | One across each motor |
| 3 | 0.1 µF (100 nF) ceramic capacitor, ≥ 50 V | Optional, one across each motor |
| 3 | 10 kΩ resistor | Pull-down on each relay IN |
| 4 | 1 kΩ resistor | ECHO voltage dividers |
| 4 | 2 kΩ resistor | ECHO voltage dividers (do **not** use 2.2 kΩ, see §8) |
| 1 | 2-pin connector or jumper in the UBEC 5 V output | Lets you unplug the 5 V supply for USB programming (§7) |
| 1 | Ground bus bar / terminal block | One common ground point (§9) |
| — | Optional fallback: 3 × 2N2222 or BC547 NPN transistor + 3 × 1 kΩ | Only if the FC65 does not trigger reliably from 3.3 V (§6.4) |

### Wire colours and gauges used in this guide

| Wire | Colour | Gauge |
|---|---|---|
| Battery and main 12 V line (battery → fuse → controller, LOAD → switch → 12 V bus) | Red (+) / Black (−) | 14 AWG (2.5 mm²) |
| Solar panel → controller | Red (+) / Black (−) | 18 AWG (0.75–1.0 mm²) |
| 12 V bus → relay COM, relay NO → motor, motor → GND | Red (+) / Black (−) | 16–18 AWG (1.0–1.5 mm²) |
| 12 V bus → relay module DC+/DC−, UBEC input | Red / Black | 20–22 AWG |
| 5 V (UBEC → ESP32, sensors) | Orange | 22 AWG |
| Relay signals (GPIO → IN) | Yellow | 22–24 AWG |
| Sensor TRIG | Green | 22–24 AWG |
| Sensor ECHO | Blue | 22–24 AWG |
| Logic ground (ESP32, sensors, relay modules) | Black | 22 AWG |

---

## 2. Block diagrams

### 2.1 Power path

```
                          +-------------------------------+
  [ SOLAR PANEL 12V 10W ] |   30 A SOLAR CHARGE CONTROLLER |
     (+) red ------------>| PV+                           |
     (-) black ---------->| PV-                           |
                          |                               |
  [ LiFePO4 12.8V 15Ah ]  |                               |
     (+) --[15 A FUSE]--->| BAT+                    LOAD+ |----[ ON/OFF SWITCH ]----+---> 12 V BUS (+)
     (-) ---------------->| BAT-                    LOAD- |-------------------------+---> GND BUS (-)
       fuse within 15 cm  +-------------------------------+                         |
       of battery +                                                                 |
                                                                                    |
     12 V BUS (+) / GND BUS (-) feed:                                               |
        +--> FC65 #1 DC+/DC-  and  COM  --> NO --> LEFT MOTOR  --> GND BUS          |
        +--> FC65 #2 DC+/DC-  and  COM  --> NO --> RIGHT MOTOR --> GND BUS          |
        +--> FC65 #3 DC+/DC-  and  COM  --> NO --> CONVEYOR    --> GND BUS          |
        +--> UBEC IN+ / IN-                                                         |
                 |                                                                  |
                 +--> UBEC 5V OUT --[unpluggable jumper]--+--> ESP32 VIN (5V pin)   |
                                                          +--> HC-SR04 VCC x4       |
                 UBEC GND OUT -----> GND BUS  <---- ESP32 GND, HC-SR04 GND x4 ------+
```

### 2.2 Signal path

```
                 ESP32 DevKit (3.3 V logic)
              +---------------------------+
              |                    GPIO26 |--yellow--+--> FC65 #1 IN  (LEFT motor)
              |                           |          +--[10k]--> GND
              |                    GPIO27 |--yellow--+--> FC65 #2 IN  (RIGHT motor)
              |                           |          +--[10k]--> GND
              |                    GPIO25 |--yellow--+--> FC65 #3 IN  (CONVEYOR)
              |                           |          +--[10k]--> GND
              |                           |
              |                    GPIO16 |--green------------------> FRONT-LEFT  TRIG
              |                    GPIO34 |<--blue--[divider]-------- FRONT-LEFT  ECHO
              |                    GPIO17 |--green------------------> FRONT-RIGHT TRIG
              |                    GPIO35 |<--blue--[divider]-------- FRONT-RIGHT ECHO
              |                    GPIO18 |--green------------------> BACK-LEFT   TRIG
              |               GPIO36 (VP) |<--blue--[divider]-------- BACK-LEFT   ECHO
              |                    GPIO19 |--green------------------> BACK-RIGHT  TRIG
              |               GPIO39 (VN) |<--blue--[divider]-------- BACK-RIGHT  ECHO
              |                           |
              |     GPIO2 = onboard LED (no wiring)                  [divider] = 1k / 2k, see §8
              |   VIN (5V) <-- UBEC 5V      GND <-- GND BUS
              +---------------------------+
```

### 2.3 Final pin map

| Function | ESP32 pin | Goes to |
|---|---|---|
| Left motor relay | **GPIO 26** | FC65 #1 IN (+ 10 kΩ to GND) |
| Right motor relay | **GPIO 27** | FC65 #2 IN (+ 10 kΩ to GND) |
| Conveyor relay | **GPIO 25** | FC65 #3 IN (+ 10 kΩ to GND) |
| Front-Left TRIG / ECHO | **GPIO 16 / GPIO 34** | HC-SR04 #1 |
| Front-Right TRIG / ECHO | **GPIO 17 / GPIO 35** | HC-SR04 #2 |
| Back-Left TRIG / ECHO | **GPIO 18 / GPIO 36 (VP)** | HC-SR04 #3 |
| Back-Right TRIG / ECHO | **GPIO 19 / GPIO 39 (VN)** | HC-SR04 #4 |
| Status LED | **GPIO 2** | Onboard LED |
| 5 V in | **VIN** (sometimes labelled 5V) | UBEC 5 V out |
| Ground | **GND** | Ground bus |

Why these pins: GPIO 25, 26 and 27 stay LOW and do not pulse during boot, so the relays cannot click on while the ESP32 starts. GPIO 34–39 are input-only, which suits ECHO signals. No strapping pins (0, 2, 5, 12, 15) are used for relays. The pin map from the spec is unchanged.

---

## 3. Solar charge controller

### 3.1 Terminals

| From | To | Wire |
|---|---|---|
| Battery + | 15 A fuse (§4) → controller **BAT+** | Red, 14 AWG |
| Battery − | Controller **BAT−** | Black, 14 AWG |
| Solar panel + | Controller **PV+** (solar/panel symbol) | Red, 18 AWG |
| Solar panel − | Controller **PV−** | Black, 18 AWG |
| Controller **LOAD+** | Main ON/OFF switch → 12 V bus | Red, 14 AWG |
| Controller **LOAD−** | Ground bus | Black, 14 AWG |

**All loads (relays, motors, UBEC) connect to the LOAD terminals only, never directly to the battery.** The LOAD output has the low-voltage disconnect that protects the LiFePO4 pack from over-discharge.

### 3.2 Connection order

1. **Connect the battery to BAT+/BAT− first.** The controller powers up from the battery and detects a 12 V system.
2. **Then connect the solar panel** to PV+/PV−. Cover the panel or turn it face-down while you do this.
3. Connect the loads to LOAD+/LOAD−.

**To disconnect, go in reverse:** loads, then the panel, then the battery. Never leave the panel connected to the controller without a battery. Most PWM controllers can be damaged by this, or will output an unregulated panel voltage (up to about 22 V) on LOAD.

### 3.3 Settings for LiFePO4

> ⚠ **Check that your controller has a lithium mode** (often shown as "LI", "LFP", "USE", "USER" or "b4/b5"). Do **not** use the lead-acid defaults ("SLD", "GEL", "FLD"). Lead-acid profiles include equalisation and boost voltages up to 14.8 V, and some disconnect too late. Both damage LiFePO4 cells. If your controller has no lithium or user mode, replace it.

| Setting | Value |
|---|---|
| Battery type | LI / USER |
| System voltage | 12 V |
| Charge (boost/bulk) voltage | **14.4 V** |
| Float voltage | **13.6 V** |
| Equalisation | **OFF** (or set equal to 14.4 V if it cannot be disabled) |
| Low-voltage disconnect (LVD) | **12.0 V** |
| Low-voltage reconnect (LVR) | **12.8 V** |
| Temperature compensation | **OFF / 0 mV/°C** (lead-acid feature) |
| LOAD output mode | **Always ON**. Usually "24H" or "Normal". See below. |

**LOAD mode:** many controllers default to "light control" (dusk to dawn) or a timer mode, which would switch the boat off during the day. Set the load mode to **always ON** (often the "24H" setting; the exact name varies by model, so check the controller's manual). After setting it, confirm that LOAD+ to LOAD− reads battery voltage in daylight.

---

## 4. Main fuse and ON/OFF switch

| From | To | Wire |
|---|---|---|
| Battery + terminal | Inline 15 A blade fuse holder (as close to the battery as possible, under 15 cm) | Red, 14 AWG |
| Fuse holder output | Controller BAT+ | Red, 14 AWG |
| Controller LOAD+ | ON/OFF switch, terminal 1 | Red, 14 AWG |
| ON/OFF switch, terminal 2 | 12 V bus (terminal block) | Red, 14 AWG |

**Change from the spec: why the switch is on the LOAD line instead of the battery line.** The fuse belongs right at the battery, because it protects the wire along its whole length. If the ON/OFF switch were also in the battery line, switching OFF in sunlight would leave the panel connected to the controller without a battery. §3.2 says to avoid exactly that. With the switch after LOAD+, OFF cuts every load (motors, relays, ESP32) and the battery can still charge.

If you also want to isolate the battery (for storage or transport), add a second switch next to the fuse. Only open it **after unplugging the panel**.

---

## 5. Battery

| From | To | Wire |
|---|---|---|
| Battery + | 15 A fuse → controller BAT+ | Red, 14 AWG |
| Battery − | Controller BAT− | Black, 14 AWG |

- The pack is rated 2C (30 A max). The 15 A fuse protects the wiring, and it blows well below the pack's limit.
- Check that the pack has a BMS/PCM (cell protection board). If it doesn't, add a 4S LiFePO4 BMS rated ≥ 20 A.
- Do not connect anything else to the battery terminals. Everything goes through the controller.

---

## 6. Relay modules (FC65) and motors

There are three identical circuits:

| Relay | ESP32 pin | Load |
|---|---|---|
| FC65 #1 | GPIO 26 | LEFT propulsion motor |
| FC65 #2 | GPIO 27 | RIGHT propulsion motor |
| FC65 #3 | GPIO 25 | CONVEYOR motor |

### 6.1 Relay module power and trigger side (per module)

| From | To | Wire |
|---|---|---|
| 12 V bus (+) | FC65 **DC+** | Red, 20–22 AWG |
| Ground bus | FC65 **DC−** | Black, 20–22 AWG |
| ESP32 GPIO (26 / 27 / 25) | FC65 **IN** | Yellow, 22–24 AWG |
| FC65 **IN** | 10 kΩ resistor → ground bus | Pull-down |

**Trigger jumper:** set it to **H (HIGH-level trigger)**. Then GPIO HIGH = relay ON, and GPIO LOW or no signal = relay OFF. The firmware assumes this (`RELAY_ON_LEVEL HIGH`).

**10 kΩ pull-down:** fit one from each IN terminal to GND. During ESP32 boot, reset or flashing, or if the yellow wire falls off, IN is held LOW and the relay stays OFF. Solder the resistor at the relay module's IN/DC− terminals, so it still works if the signal wire breaks.

> ⚠ **Before connecting any IN wire to the ESP32:** power the module from 12 V with the jumper on H and nothing connected to IN. Measure IN to DC− with a multimeter. It must read about **0 V**. If it reads 5–12 V, the jumper is on L or the module has a pull-up to 12 V. That would destroy the ESP32 pin. Do not connect it until you have fixed this.

### 6.2 Load side and motor (per module)

```
                      FC65 relay
 12 V BUS (+) --red--> COM      NO --red--+-------------+-----------> MOTOR (+)
                       (NC unused)        |             |
                                         -+-  band      |
                          flyback diode  /_\  (cathode) ===  0.1 uF ceramic
                          1N5408/SB560    |  anode      |   (optional)
                                          |             |
 GND BUS (-) <-------------black----------+-------------+------------ MOTOR (-)

   Diode band (cathode) -> motor + / NO side.   Plain end (anode) -> motor - / GND.
   Solder the diode and the capacitor directly on the motor terminals.
```

| From | To | Wire |
|---|---|---|
| 12 V bus (+) | FC65 **COM** | Red, 16–18 AWG |
| FC65 **NO** (normally open) | Motor **+** | Red, 16–18 AWG |
| Motor **−** | Ground bus | Black, 16–18 AWG |
| FC65 **NC** | Not connected | — |
| Flyback diode | Across the motor terminals: **band (cathode) on motor +** (the NO wire), plain end (anode) on motor − (GND) | Solder at the motor |
| 0.1 µF ceramic (optional) | Across the motor terminals (no polarity) | Solder at the motor, short leads |

- **Why NO:** with no signal, power or ESP32, the NO contact is open and the motor is **OFF**. Never use NC.
- **Why the flyback diode:** when the relay opens, the motor's inductance produces a voltage spike of hundreds of volts. That spike arcs the relay contacts and can reset the ESP32. The diode gives the spike a safe path. Fitted the other way round, it shorts the 12 V supply the moment the relay closes and blows the fuse.
- **Diode choice:** the 1N5408 (3 A, 1000 V) works. A Schottky diode such as the SB560 or SR560 (5 A, 60 V) or MBR1045 reacts faster and is better.
- **Wrong spin direction?** If a propeller pushes backwards, swap that motor's two wires **at the motor**. Then move the diode so its band is still on the wire that comes from NO. The diode follows the wiring, not the "+" marking on the motor.
- **The 0.1 µF capacitor** reduces brush noise that can disturb Wi-Fi and the ultrasonic sensors. Fit it directly on the motor terminals.
- The conveyor relay is wired exactly the same. **Assumption:** the conveyor motor is a 12 V DC brushed geared motor. If yours is a different voltage or AC, it needs its own supply and must not use this circuit.

### 6.3 Check that the FC65 triggers reliably from 3.3 V

The FC65's input was designed for 5 V or 12 V logic. The ESP32 drives 3.3 V, which usually works but is close to the limit. Test each module **before** connecting it to the ESP32:

1. Power the module from the 12 V bus. Set the jumper to H and fit the 10 kΩ pull-down. Leave COM, NO and NC unconnected.
2. Take 3.3 V from the ESP32 3V3 pin (ESP32 powered from USB, grounds connected) and touch it to IN with a jumper wire.
3. The relay must **click** and its LED must light **every time**. Repeat 10 times.
4. Measure the current into IN with a multimeter in series (mA range). Above about 1 mA is good. Below 0.5 mA is marginal.
5. Repeat at the lowest battery voltage the boat will see (about **12.0 V**, the LVD point). Use a bench supply if you have one. The relay coil gets weaker as the voltage drops.
6. Remove the 3.3 V. The relay must release immediately.

If any step fails, or the relay chatters, use the transistor fallback below.

### 6.4 Transistor fallback (2N2222 or BC547)

The NPN transistor works as a switch that pulls IN to ground. Set **this module's jumper to L (LOW-level trigger)**. The logic stays the same, and **the firmware needs no change**: GPIO HIGH, then transistor ON, then IN pulled LOW, then relay ON.

```
 ESP32 GPIO (26/27/25) ---+---[ 1 kΩ ]---> B (base)
                          |                    2N2222 / BC547
                       [10 kΩ]          C (collector) ----> FC65 IN   (jumper on L)
                          |             E (emitter)   ----> GND BUS
 GND BUS -----------------+
```

| From | To |
|---|---|
| ESP32 GPIO | 1 kΩ → transistor **base** |
| ESP32 GPIO | 10 kΩ → GND (pull-down, keeps the transistor OFF at boot) |
| Transistor **collector** | FC65 **IN** |
| Transistor **emitter** | Ground bus |
| FC65 jumper | **L** |

Pinouts differ. 2N2222 (TO-92, flat side facing you, legs down): **E B C**. BC547: **C B E**. Check your datasheet.

With no GPIO signal, the transistor is OFF, IN floats, and the relay is OFF. The ESP32 pin only sees the base resistor, never 12 V.

---

## 7. 5 V power (UBEC → ESP32 and sensors)

| From | To | Wire |
|---|---|---|
| 12 V bus (+) | UBEC **input +** (red) | Red, 20–22 AWG |
| Ground bus | UBEC **input −** (black) | Black, 20–22 AWG |
| UBEC **5 V out** | 2-pin jumper/connector → ESP32 **VIN** (labelled VIN or 5V) | Orange, 22 AWG |
| UBEC **5 V out** (after the jumper) | VCC of all four HC-SR04 | Orange, 22 AWG |
| UBEC **GND out** | Ground bus, ESP32 GND, all HC-SR04 GND | Black, 22 AWG |

- Never feed 5 V into the ESP32 **3V3** pin. 5 V goes to **VIN/5V** only.
- Before connecting the ESP32, power the UBEC and measure its output: it must be 4.9–5.2 V.
- Current budget: ESP32 up to about 0.5 A peak, plus 4 × 15 mA for the sensors. The 3 A UBEC has plenty of headroom.

### Programming over USB safely

The DevKit's USB port also supplies 5 V. On many boards a diode separates USB 5 V from VIN, but some cheap clones do not have it. Two 5 V sources fighting each other can damage the laptop's USB port or the UBEC.

**Safe procedure:**
1. Switch the main ON/OFF switch **OFF**. The relays lose their 12 V, so the motors cannot run.
2. **Unplug the UBEC 5 V jumper** (that's why it is there).
3. Plug in the USB cable and upload. The ESP32 and sensors now run from USB.
4. Unplug USB, refit the 5 V jumper, then switch main power back ON.

For Serial Monitor debugging **with** motors powered: keep the UBEC jumper **unplugged** and power the ESP32 from USB only. The ground still connects through the GND wires. Keep USB cables short, and use a laptop running on battery when the boat is on water.

---

## 8. HC-SR04 ultrasonic sensors

There are four identical circuits:

| Sensor | TRIG → ESP32 | ECHO → divider → ESP32 |
|---|---|---|
| Front-Left | GPIO 16 | GPIO 34 |
| Front-Right | GPIO 17 | GPIO 35 |
| Back-Left | GPIO 18 | GPIO 36 (VP) |
| Back-Right | GPIO 19 | GPIO 39 (VN) |

| From | To | Wire |
|---|---|---|
| HC-SR04 **VCC** | UBEC 5 V | Orange |
| HC-SR04 **GND** | Ground bus | Black |
| HC-SR04 **TRIG** | ESP32 TRIG GPIO **directly** (3.3 V is enough for TRIG) | Green |
| HC-SR04 **ECHO** | **1 kΩ** → ESP32 ECHO GPIO | Blue |
| ESP32 ECHO GPIO | **2 kΩ** → GND | — |

### ECHO voltage divider (one per sensor)

The HC-SR04 ECHO pin outputs **5 V**. ESP32 pins tolerate **3.3 V only**. Without the divider, the input pins will be damaged over time.

```
   HC-SR04 ECHO (0 / 5 V)
          |
        [1 kΩ]
          |
          +---------------> ESP32 GPIO 34 / 35 / 36 / 39   (reads 0 / 3.33 V)
          |
        [2 kΩ]
          |
         GND

   Vout = 5 V × 2k / (1k + 2k) = 3.33 V
```

- Use **exactly 1 kΩ and 2 kΩ**. Two 1 kΩ in series make 2 kΩ. With 2.2 kΩ you get 3.44 V, which is above 3.3 V.
- Solder the dividers on a small perfboard next to the ESP32, so the long wires carry 5 V and the short ones 3.3 V.
- The 2 kΩ also acts as the pull-down that GPIO 34–39 need, because these pins have no internal pull resistors.
- The firmware reads the sensors **one at a time** (one every 60 ms, each with a 30 ms echo timeout), so they never hear each other's pings.

---

## 9. Ground: one common ground

Everything below must share **one ground**. Use one bus bar or terminal block (the "GND bus"), wired to **controller LOAD−**:

- Controller **LOAD−** (and through the controller, the battery −)
- UBEC input − and output GND
- ESP32 GND
- All three FC65 **DC−**
- All three motors' − wires
- All four HC-SR04 GND
- The 10 kΩ pull-downs and the divider 2 kΩ resistors

> ⚠ **Do not add a separate wire from the battery − terminal to the GND bus.** Many PWM controllers switch the **negative** side internally ("common positive" design). On those, LOAD− is *not* the same node as BAT− while the load is OFF. An extra battery-to-ground wire bypasses the low-voltage disconnect and can damage the controller. The battery − reaches the common ground **through the controller**. Use a star layout: heavy motor return wires go straight to the bus bar, not daisy-chained through the ESP32's ground.

---

## 10. Practical notes

### Waterproofing
- Put the controller, ESP32, UBEC, relays and fuse in an **IP65 or better enclosure** with cable glands. Add a silica-gel pack and a small breather/vent membrane to prevent condensation.
- Mount the enclosure high in the hull, away from bilge water. Use heat-shrink on every joint and dielectric grease on screw terminals.
- **HC-SR04 sensors are not waterproof.** Mount them under a small hood or lip so splashes and rain cannot hit the transducers, at least 10–15 cm above the waterline. Seal the back of the PCB with conformal coating or hot glue (keep the transducer faces clear).
- **Waterproof alternative: JSN-SR04T.** Same 5 V supply and same TRIG/ECHO interface (mode 0, default). Keep the same voltage divider and the same firmware. It has a larger minimum range (about 20–25 cm) and a wider beam. The 60 ms reading slot in the firmware suits it.
- Run the motor shafts through proper stern tubes or seals. Do not rely on the motor body being waterproof.

### Sensor mounting angle
- Mount each sensor **level, or tilted up by 5–10°**. A sensor pointing down will "see" the water surface, especially with waves, and show false obstacles.
- Angle the front sensors **15–20° outward** (front-left to the left, front-right to the right) to cover the bow corners. Do the same for the back pair.
- The HC-SR04 beam is roughly a 15° cone. Keep hull edges, the conveyor and the propeller guards out of it.
- Test on the water: if a sensor shows a steady reading with nothing in front of it, tilt it up a little more.

### Power budget

> Motor current values are **estimates**. Measure them in water with a clamp meter or an inline ammeter, and compare with the motor datasheet.

| Load | Current @ 12.8 V | Power |
|---|---|---|
| 2 × IG32 propulsion motor, in water with propeller | **~1.5 A each (estimate)**, 3.0 A total | ~38 W |
| Conveyor motor | **~1.0 A (estimate)** | ~13 W |
| 3 × FC65 relay coil when ON | ~0.04 A each | ~1.5 W |
| ESP32 with Wi-Fi + 4 × HC-SR04 (via UBEC, ~85% efficiency) | ~0.12 A | ~1.5 W |
| Charge controller self-consumption | ~0.01–0.02 A | ~0.2 W |

Battery: 12.8 V × 15 Ah = **192 Wh**. About **170 Wh** is usable before the 12.0 V low-voltage disconnect.

| Situation | Total current | Runtime (approx.) |
|---|---|---|
| Driving forward + conveyor ON | ~4.3 A (~55 W) | **~3 h** |
| Driving forward, conveyor OFF | ~3.2 A (~41 W) | **~4 h** |
| Parked, electronics only (armed, motors OFF) | ~0.13 A (~1.7 W) | **~4 days** |

Motor stall current (propeller jammed by weed) can be several times the running current (**estimate 5–8 A per motor; check the datasheet**). Stalling both motors can reach the 15 A fuse.

**Solar recharge:** the 10 W panel delivers about 0.55 A at best, roughly 7 W into the battery through a PWM controller. With about 4–5 peak-sun hours per day, that is **~2.5 Ah (~32 Wh) per sunny day**.
- Full recharge from empty (15 Ah): about **27 peak-sun hours, or 6 sunny days**, with the boat switched OFF.
- One hour of driving (~3–4 Ah) takes **about 1.5 sunny days** to put back.
- Even idle, the electronics use about 3 Ah per day. That is **more than the panel makes even on a sunny day** (~2.5 Ah). **Switch the main switch OFF when the boat is not in use**, otherwise the battery slowly drains even in sunshine.
- The 10 W panel is a range extender, not a full power source for this boat. A 30–50 W panel would make it close to self-sufficient for short daily runs.
