# Aprilaire 8870 Thermostat Integration for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)

This custom integration allows you to control and monitor Aprilaire 8870 thermostats through Home Assistant. The integration supports both network-connected serial servers and direct serial port connections to the Aprilaire RS-485 network.

## Features

- Control and monitor Aprilaire 8870 thermostats
- Support for multiple thermostats on a single RS-485 network
- Real-time updates using Change of State (COS) functionality
- Full climate entity with temperature, mode, and fan control
- Additional sensors for temperature, humidity, and system status
- Customizable polling intervals

## Requirements

- Home Assistant **2024.1.0** or newer
- One of the following connection methods:
  - **Serial Server**: A network-connected serial server with telnet capability (e.g., Moxa NPort)
  - **Direct Serial Connection**: RS-485 to USB/Serial adapter connected to your Home Assistant host

## Connection Methods Explained

### Serial Server Connection

A serial server (also known as a serial-to-Ethernet converter) allows you to connect RS-485 devices to your network. This is ideal if your Aprilaire thermostats and Home Assistant system are not in the same physical location.

**Recommended Serial Servers:**
- **Moxa NPort** series (e.g., Moxa NPort 5110, 5130, 5150)
- **USR-TCP232** series
- **Digi One SP** or **Digi PortServer TS**

The serial server must be configured with the following settings:
- **Operation Mode**: TCP Server or RFC2217 mode
- **Port Settings**: 9600 baud (default), 8 data bits, no parity, 1 stop bit
- **Flow Control**: None

### Direct Serial Connection

If your Home Assistant host is physically close to your Aprilaire thermostat network, you can use a direct serial connection with an RS-485 adapter.

**Required Hardware:**
- RS-485 to USB adapter
- For multi-drop networks, ensure your adapter supports the proper RS-485 protocol

## Installation

### Option 1: HACS Installation (Recommended)

1. If you haven't already installed HACS, follow the [HACS installation guide](https://hacs.xyz/docs/installation/manual).
2. Navigate to HACS in your Home Assistant instance
3. Go to "Integrations"
4. Click the three-dot menu in the top right corner and select "Custom repositories"
5. Add `https://github.com/Limecooler/aprilaire_8870` as a custom repository with the category "Integration"
6. Click "ADD"
7. Search for "Aprilaire 8870" in the integrations tab
8. Click "DOWNLOAD"
9. Restart Home Assistant

### Option 2: Manual Installation

1. Download the **Source code (zip)** for the latest release from the [Releases page](https://github.com/Limecooler/aprilaire_8870/releases).
2. Extract the archive.
3. Copy the inner `custom_components/aprilaire_8870/` folder into your Home Assistant `config/custom_components/` directory so the final path is `config/custom_components/aprilaire_8870/manifest.json`.
4. Restart Home Assistant.

## Configuration

The integration is configured via the Home Assistant UI:

1. Go to **Configuration** → **Integrations**
2. Click the "+" button to add a new integration
3. Search for "Aprilaire 8870"
4. Follow the configuration flow, selecting either serial server or direct COM port connection

### Serial Server Configuration

- **IP Address**: The IP address of your serial server
- **Port**: The TCP port on your serial server (default: 23)

### Direct Serial Connection Configuration

- **Serial Port**: The path to your serial port
  - **Linux**: `/dev/ttyUSB0`, `/dev/ttyS0`, etc.
  - **Windows**: `COM1`, `COM2`, etc.
  - **macOS**: `/dev/tty.usbserial`, etc.
- **Baud Rate**: 9600 or 19200 (typically 9600)

## Wiring and Hardware Setup

### RS-485 Network Overview

Aprilaire thermostats use a 4-wire RS-485 network:
- A+ (positive data line)
- B- (negative data line)
- COM (common/ground)
- +VDC (power)

### Serial Server Connection Example

```
  [Aprilaire Thermostats] <---RS-485---> [Serial Server] <---Ethernet---> [Home Assistant]
         RS-485 Network         |
                                +--> Configure as TCP Server on port 23
                                     Set to 9600 baud, 8N1, no flow control
```

1. Connect the RS-485 network to the serial server's RS-485 terminals
2. Connect the serial server to your network via Ethernet
3. Configure the serial server according to manufacturer instructions
   - Typically set as TCP Server mode
   - Set baud rate to 9600
   - Set 8 data bits, no parity, 1 stop bit
   - Disable flow control

### Direct Serial Connection Example

```
  [Aprilaire Thermostats] <---RS-485---> [RS-485 to USB Adapter] <---USB---> [Home Assistant]
```

1. Connect the RS-485 network to the RS-485 adapter
2. Connect the USB adapter to your Home Assistant system
3. Ensure you have the correct permissions to access the serial port:
   ```bash
   # On Linux systems, add your user to the dialout group
   sudo usermod -a -G dialout YOUR_USER
   
   # For Docker-based installations, ensure the serial device is passed to the container
   # Example docker-compose.yml addition:
   devices:
     - /dev/ttyUSB0:/dev/ttyUSB0
   ```

## Entities

The integration will create the following entities:

- **Climate Entity**: Primary thermostat control (setpoints, mode, fan)
- **Sensors**:
  - Temperature (indoor)
  - Humidity (if the thermostat has a humidity sensor)
  - Outdoor Temperature (if connected to the thermostat)
  - Outdoor Humidity (if connected to the thermostat)
- **Binary Sensors**:
  - HVAC Status (any heating/cooling stage active)
  - Fan Status (fan currently running)
  - Filter Status (filter change due)
  - Error Status (any thermostat error active)
  - Network Override (HOLD active from the network side)
- **Switches**:
  - Fan Override (Auto/On)
  - Network Override (Hold)
  - Backlight

## Services

The integration provides the following services:

- `aprilaire_8870.set_text_message`: Display a message on the thermostat screen
- `aprilaire_8870.set_backlight`: Control thermostat backlight
- `aprilaire_8870.reset_filter`: Reset filter timer
- `aprilaire_8870.set_lockout`: Configure thermostat lockout features
- `aprilaire_8870.configure_cos`: Configure Change of State notifications

## Advanced Configuration

The integration can be further customized through the options flow:

1. Go to **Configuration** → **Integrations**
2. Find the Aprilaire 8870 integration and click "Configure"
3. Customize settings such as:
   - Temperature unit preference
   - Command retry count
   - Connection backoff maximum
   - Debug mode

## Troubleshooting

### Common Issues and Solutions

#### Connection Problems

- **Serial Server Connection Failed**: 
  - Verify the IP address and port are correct
  - Ensure the serial server is properly configured
  - Check that the serial server has connectivity to the RS-485 network

- **Direct Serial Connection Failed**:
  - Check if the serial port exists and is accessible
  - Verify the baud rate matches the thermostat configuration
  - Ensure proper permissions for the serial port

#### No Devices Found

- Verify RS-485 wiring is correct (A+, B-, COM)
- Check that termination resistors are properly placed (if required)
- Ensure the network doesn't exceed maximum device count or cable length specifications

#### Thermostat Not Responding

- Check if the thermostat has power
- Verify the network address setting on the thermostat
- Check for proper RS-485 communication parameters

### Logs and Debugging

To enable debug logs for troubleshooting:

1. Add the following to your `configuration.yaml`:
   ```yaml
   logger:
     default: info
     logs:
       custom_components.aprilaire_8870: debug
   ```
2. Restart Home Assistant
3. Check the logs for detailed information

## FAQ

### What's the difference between Serial Server and Direct Serial connections?

A serial server provides network access to the RS-485 bus, allowing you to place Home Assistant anywhere on your network. A direct serial connection requires your Home Assistant instance to be physically close to the RS-485 network.

### Can I use this integration with other Aprilaire thermostat models?

This integration is specifically designed for the Aprilaire 8870 model. Other models might work partially but are not officially supported.

### How many thermostats can I control with this integration?

The RS-485 network supports up to 64 thermostats. This integration supports discovering and controlling all thermostats on the network.

### Does this integration work with Home Assistant Supervised/Core/Container?

Yes, this integration works with all Home Assistant installation types. For container-based deployments, ensure you properly pass through the serial device if using direct serial connection.

### Why does the thermostat display "Override" while using Home Assistant?

When you control the thermostat through Home Assistant, it uses the network override feature of the Aprilaire thermostat. This is normal behavior.

## References

Manufacturer documentation — useful for wiring questions, network addressing, and the protocol commands this integration speaks:

- [Aprilaire Whole System Installation Guide (PDF)](https://www.homecontrols.com/homecontrols/products/pdfs/RP-AprilAire/Aprilaire_Whole_System_Installation.pdf) — RS-485 network topology, wiring, multi-thermostat addressing.
- [Aprilaire 8870 Thermostat Owner's Manual (PDF)](https://www.aprilaire.com/docs/default-source/product-owners-manuals/Thermostat/aprilaire-thermostat-model-8870-owners-manual-obs.pdf?sfvrsn=6) — thermostat configuration, on-device menus, and the network address setting referenced in the troubleshooting section above.

### Local docs

The [`docs/`](docs/) folder mirrors the manufacturer references the integration was developed against. These are the authoritative source for protocol behavior (TDMA timing, SN0 globals, COS flags, the TIME/DATE/PMES/lockout command set, etc.):

- [`aprilaire_programmers_manual_8870_system_dp10005756.doc`](docs/aprilaire_programmers_manual_8870_system_dp10005756.doc) — **8870-specific** programmer's manual (DP 10005756). Primary reference for the StatNet command set: TEMP/MODE/FAN/HVAC/HOLD/SH/SC essentials, COS flags (C1–C8), CR=NORMAL, TIME=HHMM, DATE=MMDDYY, PMES1–4 / TMPMES messaging, BLTON, FLTALM, lockouts (FANLK/MODELK/UPDNLK/NETLK/LKTIME/LKLIMIT), and the SN0 global broadcast address.
- [`Aprilaire 8800 Programmers Manual.pdf`](docs/Aprilaire%208800%20Programmers%20Manual.pdf) — older 8800-series programmer's manual. Same protocol family; useful for cross-checking commands that aren't documented in the 8870 manual.
- [`Aprilaire_Whole_System_Installation-1.pdf`](docs/Aprilaire_Whole_System_Installation-1.pdf) — full system installer guide: RS-485 wiring, termination, address dipswitches, and the 265ms × N (default 32) TDMA response window assumption the coordinator's bulk-poll timeout is sized for.
- [`aprilaire-8870-communicating-thermostat-owners-manual-B2202659.pdf`](docs/aprilaire-8870-communicating-thermostat-owners-manual-B2202659.pdf) / [`-B2202659B.pdf`](docs/aprilaire-8870-communicating-thermostat-owners-manual-B2202659B.pdf) — end-user owner's manuals (two revisions); on-device menu navigation, mode/fan/hold semantics as the user sees them.
- [`aprilaire-8870-communicating-thermostat-specification-sheet-4047.pdf`](docs/aprilaire-8870-communicating-thermostat-specification-sheet-4047.pdf) — specification sheet: supported HVAC equipment topologies (heat pump O/B reversing valve, two-stage heat/cool, etc.), sensor accuracy.
- [`aprilaire-8870-communicating-thermostat-submittal-sheet-4061.pdf`](docs/aprilaire-8870-communicating-thermostat-submittal-sheet-4061.pdf) — submittal sheet: dimensions, electrical specs, terminal designations.

## Support

If you need assistance:

1. Check the [issues section](https://github.com/Limecooler/aprilaire_8870/issues) on GitHub for similar problems and solutions
2. Open a new issue with detailed information if your problem is not already reported

## Contributing

Contributions to improve the integration are welcome:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## License

This integration is licensed under the MIT License - see the LICENSE file for details.

