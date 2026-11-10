# BME688 Scent Classifier (ESP32 → MQTT)

Real-time scent classification on the **Bosch BME688 Development Kit** running on an
**Adafruit ESP32 Feather**. The board runs a BME AI-Studio–trained selectivity model
(BSEC 2.6.1.0) across 8 gas sensors, takes a **majority vote**, and publishes the
result over WiFi to **HiveMQ Cloud (MQTT/TLS)**. A small Python script subscribes and
prints the live label.

Trained classes: **air / woody / citrus / floral** (plus **unknown** when the sensors
don't agree or confidence is low).

## Hardware
- Bosch **BME688 Development Kit** (8× BME688 over SPI + I²C mux)
- **Adafruit ESP32 Feather** (classic, 2.4 GHz WiFi only)
- A 2.4 GHz WiFi hotspot + a free **HiveMQ Cloud** cluster
- Optional: LiPo battery on the Feather's JST connector for untethered use

## Repository layout
```
firmware/bme688_sel_esp32/   Arduino sketch (BSEC integration + WiFi/MQTT)
  bme688_sel_esp32.ino         main sketch
  bsec_selectivity.c/.h        AI-Studio trained model config (yours)
  bme68x.*, bsec_*, commMux.*  Bosch BSEC/BME68x integration files
  secrets.h.example            template for credentials (copy to secrets.h)
lib/esp32/libalgobsec.a        BSEC 2.6.1.0 algorithm library (esp32)
receiver/mqtt_receiver.py      Python MQTT subscriber
```

## 1. Configure credentials
```bash
cd firmware/bme688_sel_esp32
cp secrets.h.example secrets.h      # then edit secrets.h with your values
```
`secrets.h` holds your WiFi SSID/password and HiveMQ broker/user/password. It is
**git-ignored**, so your secrets are never committed.

## 2. Prerequisites (arduino-cli)
```bash
arduino-cli core install esp32:esp32          # ESP32 core (tested on 2.0.17)
arduino-cli lib install "PubSubClient"        # MQTT client
```
`WiFi` and `WiFiClientSecure` ship with the ESP32 core.

## 3. Build & upload
This project links the precompiled **`libalgobsec.a`**, so it must be passed to the
linker via `build.extra_libs`. From the repo root:

```bash
arduino-cli compile \
  --fqbn esp32:esp32:featheresp32 \
  --build-property "build.extra_libs=$PWD/lib/esp32/libalgobsec.a" \
  firmware/bme688_sel_esp32

arduino-cli upload -p <PORT> --fqbn esp32:esp32:featheresp32 firmware/bme688_sel_esp32
```
Replace `<PORT>` with your serial port (e.g. `COM5` on Windows, `/dev/ttyUSB0` on Linux).

> On Windows, `$PWD` → use the full path, e.g.
> `--build-property "build.extra_libs=C:\path\to\repo\lib\esp32\libalgobsec.a"`

## 4. Receive the data
On any machine with internet (e.g. a Jetson):
```bash
pip3 install "paho-mqtt==1.6.1"
MQTT_BROKER=your-cluster.s1.eu.hivemq.cloud \
MQTT_USER=youruser MQTT_PASS=yourpass \
python3 receiver/mqtt_receiver.py
```
You'll see one label roughly every 2 seconds:
```
[2026-08-30 13:45:16] air
[2026-08-30 13:45:18] floral
```

## How it works
- **Model**: BME AI-Studio selectivity config in `bsec_selectivity.c` (BSEC 2.6.1.0).
- **8-sensor majority vote**: each sensor classifies; the published label is the class
  most sensors agree on. Robust against a single noisy sensor.
- **Unknown detection**: if too few sensors agree, or their raw confidence is low, the
  label becomes `unknown` (tunable via `CONF_MIN` / `AGREE_MIN` in the sketch).
- **Non-blocking networking**: MQTT/TLS runs in its own FreeRTOS task on core 0, so a
  slow or dropped hotspot can never freeze the sensor loop on core 1.
- **Self-healing**: a task watchdog reboots the board if the sensor loop ever hangs.
- **Low power (battery-friendly)**: CPU at 80 MHz, reduced WiFi TX power, ~2 s publish
  interval.

### Tunables (top of `bme688_sel_esp32.ino` / `bsec_integration.h`)
| What | Where | Default |
|------|-------|---------|
| Number of sensors | `NUM_OF_SENS` (bsec_integration.h) | 8 |
| Publish interval | `vTaskDelay(...)` in `mqtt_task` | 2000 ms |
| Unknown thresholds | `CONF_MIN`, `AGREE_MIN` | 0.6, 50 |
| WiFi TX power | `WiFi.setTxPower(...)` | 2 dBm |
| MQTT topic | `MQTT_TOPIC` | `sensor/air_quality` |

## Retraining for your own scents
Collect data in **BME AI-Studio** (ideally each scent at several concentrations to
avoid the classifier collapsing to one class at high concentration), export the
selectivity config, and replace `bsec_selectivity.c/.h`. Keep the array name
`bsec_config_selectivity` and update the size in the header. Also update the
`class_labels[]` order in the sketch to match your training.

## Credits & license
- BSEC / BME68x integration files and `libalgobsec.a` are **© Bosch Sensortec GmbH**,
  redistributed here for convenience — see the license headers in each file and the
  official package: <https://www.bosch-sensortec.com/software-tools/software/bsec/>.
  These Bosch components remain under Bosch's license terms.
- The sketch modifications, receiver script, and documentation in this repo are the
  author's own work.
