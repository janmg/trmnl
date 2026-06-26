# Trmnl

Terminal (TRMNL) is an ePaper project started by Ryan Kulp. An epaper does not emit light and does not consume electricity until you refresh the image. It is a calm way to display an image throughout the day.

TRMNL sells ready made devices that can show you a playlist of screens. You can also build your own device and for a one-time fee get access to the server. If you want to host your own server, they offer terminus, an BYOS bring your own server.

After reading this article from Helsingin Sanomat, it was time to get my own epaper
https://www.hs.fi/suomi/art-2000011938981.html

I wanted to use DYI from Seeed Studio, put it in an IKEA photoframe and run my own very simple python application to show data I care about. When the device is unconfigured, it has an WIFI endpoint named TRMNL-ABCDEF and when you connect to it, there is a sign-in page where you can choose your own wifi. And also specify a server. My simple application runs on https://trmnl.janmg.com. Once the device resets, it should show a QR code, where you can register a new device to my server. This is useful later when you want to serve different images to different devices. When registered, the MAC address of the device is stored in the sqlite database and for now a mock schedule is displayed. That is my starting point to vibe code the panels with real data.

## Seeed Studio

TRMNL 7.5" (OG) DIY Kit

- [https://www.seeedstudio.com/TRMNL-7-5-Inch-OG-DIY-Kit-p-6481.html](https://www.seeedstudio.com/TRMNL-7-5-Inch-OG-DIY-Kit-p-6481.html)
- [https://wiki.seeedstudio.com/trmnl_7inch5_diy_kit_main_page/](https://wiki.seeedstudio.com/trmnl_7inch5_diy_kit_main_page/)

The TRMNL 7.5" (OG) DIY Kit, co-developed by Seeed Studio and TRMNL, is an e-ink development solution. It includes a:

- 7.5-inch 800x480, 4-level grayscale monochrome e-ink display (2-bit)
- XIAO ESP32-S3 PLUS driver board
- optional 2000 mAh rechargeable battery (can be directly usb powered)
- optional 10cm 24PIN FPC extension cable as SPI0 (the smaller one, you don't need the bigger connector)

The device can show PNG images in 4 greyscales or it can display simple HTML pages.

## Connect

- Optionally connect the white extension cable, pull out the black tab and push the screen into the extension cable.
- Lift the black lever on the shorter 24-pin flatcable connector upwards.
- Stick the golden connectors from the screen, halfway into the socket.
- Close the black lever.
- Remove the protecting film from the green tab (or at least the big warning sticker).
- Optionally connect the battery into the white plug to the side.
- Connect a USB-C cable.

## Flash firmware

[https://trmnl.com/flash](https://trmnl.com/flash)

With an USB-C connection to a computer, you can use select Seed Studio (TRMNL 7.5" OG DIY Kit).

The device comes with the TRMNL firmware installed. It's best to update to version 1.6.10 (or higher).

- Press KEY3 button for 5 seconds to get the device into FLASH MODE.
- Click Connect and through the browser the ESP Web Tools you can choose USB JTAG/serial debug unit to install TRMNL Firmware.

[https://www.youtube.com/watch?v=3xehPW-PCOM](https://www.youtube.com/watch?v=3xehPW-PCOM)

## Getting Started

For the setup, TRMNL runs a temporary WIFI access point. Use a phone/tablet/computer to connect to WIFI SSID "TRMNL".

On the sign-in page, you can choose your own WIFI SSID and enter the password.

You can choose your own local TRMNL API endpoint (See BYOS), to use your own server.

## TRMNL
```
pip install -r requirements.txt
python app.py
curl -H "ID: 00:11:22:33:44:55" http://localhost:5000/api/display
```
```
https://trmnl.janmg.com/register?mac_address=44:1b:f6:81:9b:fc
https://trmnl.janmg.com/register?mac_address=e0:72:a1:d8:2e:7c
```

## Ikea frame 13x18 cm
https://www.ikea.com/fi/en/cat/picture-photo-frames-18746/?filters=f-typed-reference-measurement%3A13x18-frames
RÖDALM, LOMVIKEN, HÄCKHAGTORN Must fit the 11 x 17cm screen

Frame height: 20 cm
Frame width: 15 cm
Mount opening, height: 14 cm
Mount opening, width: 9 cm
Picture with mount, height: 15 cm
Picture with mount, width: 10 cm
Picture without mount, height: 18 cm
Picture without mount, width: 13 cm
Frame, depth: 3 cm

## TRMNL BYOS

Build your own server. TRMNL has a paid subscription (one time 45 euros) or you can run your own server.

- [https://docs.trmnl.com/go/diy/byos](https://docs.trmnl.com/go/diy/byos)
- [https://github.com/usetrmnl/terminus](https://github.com/usetrmnl/terminus)

The most complete TRMNL experience is with terminus, which uses Docker Compose to run 4 Docker images.

If you have docker compose, this script will install the 4 docker containers and open the API on TCP port 2300:

```bash
curl https://raw.githubusercontent.com/usetrmnl/terminus/refs/heads/main/scripts/docker/quick.sh | bash
```

- [https://alchemists.io/projects/terminus](https://alchemists.io/projects/terminus)

```text
CONTAINER ID   IMAGE                               COMMAND                  CREATED          STATUS                    PORTS                                            NAMES
728b93bd1dd1   ghcr.io/usetrmnl/terminus:latest   "scripts/docker/entr…"   15 minutes ago   Up 14 minutes (healthy)   2300/tcp                                         terminus-worker-1
fc39ae239f28   ghcr.io/usetrmnl/terminus:latest   "scripts/docker/entr…"   15 minutes ago   Up 14 minutes (healthy)   0.0.0.0:2300->2300/tcp, [::]:2300->2300/tcp   terminus-web-1
6e5940e9bc98   postgres:18.4-alpine               "docker-entrypoint.s…"   15 minutes ago   Up 15 minutes (healthy)   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp   terminus-database-1
b9542a75e109   valkey/valkey:9-alpine             "docker-entrypoint.s…"   15 minutes ago   Up 15 minutes (healthy)   0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp   terminus-keyvalue-1
```

## TRMNL API

The minimum to get things displayed on the device is to implement an HTTP webserver that serves a json on `/api/display` with this content:

```json
{ "status": 200, "image_url": "http://10.0.0.5/output.png", "filename": "my-face", "refresh_rate": 900 }
```

If the device pulls that JSON, it is instructed to download the `output.png` ... The format is a [FloydSteinberg](https://en.wikipedia.org/wiki/Floyd%E2%80%93Steinberg_dithering) dithered picture with 4 colors. Dithering creates in illusion of more colors by spreading the dots further or closer apart.

```bash
magick input.jpeg -resize "800x480^" -gravity center -extent 800x480 -colorspace gray -dither FloydSteinberg -posterize 4 -define png:bit-depth=2 -define png:color-type=0 output.png
```

## TRMNL Cloud

If you don't want to run your own BYOS, you can buy access to their service for 45 Euro.

- [https://shop.trmnl.com/products/byod](https://shop.trmnl.com/products/byod)

To use it, the instructions are here:

- [https://docs.trmnl.com/go/diy/byod#build-from-scratch-advanced](https://docs.trmnl.com/go/diy/byod#build-from-scratch-advanced)

Connect to your own local TRMNL BYOS server ([# Build your own server](#build-your-own-server)), or buy access to the cloud service MAC is not registered. To register the MAC address in TRMNL in your account:

- BYOD device and click on the gear icon,
- click Developer Perks
- enter your MAC address for your device and save.

The Cloud works with plugins you can install, for instance the GMail Calendar or the Weather app and decide how often to rotate the screen.

## TRMNL Firmware

Uses platformio, can also be done directly in ESP-IDF, but Claude helped me make the modifications. In the end my 1.8.5 firmware was broken. Apparently 1.8.6 is better.

local-cmake branch contains my changes to use ESP-IDF directly, skipping the need for platformio.

```text
CMake: Select a kit ESP-IDF 6.0.1 GCC 15
```

- [https://github.com/usetrmnl/trmnl-firmware](https://github.com/usetrmnl/trmnl-firmware)

```text
. C:\esp\v6.0.1\esp-idf\export.ps1
.\scripts\setup_idf_components.ps1
idf.py set-target esp32s3
idf.py -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.XIAO_EPAPER_DISPLAY" build
idf.py flash
```

COM8 is only available very shortly, use JTAG.

## Private plugin

- [https://sensecraft.seeed.cc/hmi](https://sensecraft.seeed.cc/hmi)

Rendering: You write HTML/CSS markup directly in the TRMNL dashboard using Liquid templates. The TRMNL server renders this HTML into the specialized PNG format the device needs and serves it automatically.

- `src\bl.cpp`
- `src\display.cpp`

## Private "Webhook" Plugin

TRMNL supports Private Plugins that can receive data via Webhooks.

How it works: You create a Private Plugin on the TRMNL Dashboard and select the Webhook strategy.

Pushing data: You POST your todo list JSON to a unique URL provided by TRMNL.

Rendering: You write HTML/CSS markup directly in the TRMNL dashboard using Liquid templates. The TRMNL server renders this HTML into the specialized PNG format the device needs and serves it automatically.

## API Endpoints

- [http://10.0.0.5/output.png](http://10.0.0.5/output.png)
- [http://10.0.0.5/api/display](http://10.0.0.5/api/display)

### Setup

```bash
curl "https://byos.local/api/setup" \
-H 'ID: <device_mac_address>' \
-H 'Content-Type: application/json'
```

### Display

```bash
curl "http://byos.local/api/display" \
-H 'ID: <device_mac_address>' \
-H 'Content-Type: application/json'
```

### Logs

```bash
curl "http://byos.local/api/log" \
-H 'ID: <device_mac_address>' \
-H 'Content-Type: application/json'
```

## Hardware details

The e-paper screen is an GDEY075T7.

- [https://files.seeedstudio.com/wiki/XIAO_Gadget/TRMNL_Kit_Pic/XIAO_ePaper_driver_board_sch.pdf](https://files.seeedstudio.com/wiki/XIAO_Gadget/TRMNL_Kit_Pic/XIAO_ePaper_driver_board_sch.pdf)

2025/11/21, Upgrade the PMIC from ETA6003 to SY6974B.

### Developer Edition

- [https://wiki.seeedstudio.com/reterminal_e10xx_trmnl/](https://wiki.seeedstudio.com/reterminal_e10xx_trmnl/)
- [https://shop.usetrmnl.com/products/byod](https://shop.usetrmnl.com/products/byod)
- [https://trmnl.com/claim-a-device](https://trmnl.com/claim-a-device)
- [https://trmnl.com/dashboard](https://trmnl.com/dashboard)

Firmware v1.6.0 or later to change the palette.
