# Kubu BLE protocol

Verified against a Kubu Retro Pro (`KubuRetroPro-XX-XX` running firmware 04.46)

State is broadcast with no active connection required. The state value is encrypted with a per-sensor key that appears to be set at manufacturing time (it is never written to the sensor during pairing).

## Advertising

Legacy advertising, connectable and scannable.

| Packet | Content |
|---|---|
| ADV_IND | Flags `06`, complete local name `KubuRetroPro-XX-XX`, sometimes 16-bit service UUID `0xFC5E` |
| SCAN_RSP | Manufacturer specific data: company ID **3535** (`0x0DCF`, little endian `CF 0D`) + 16 byte payload. Raw AD structure: `13 FF CF 0D` + 16 bytes |

The state is **only in the scan response**, so the scanner must scan actively (send `SCAN_REQ`).

Timing:
- **Idle:** 2-4 adverts about every 30 s. The payload changes to a new heartbeat frame about every 60-90 s
- **State change:** a burst of adverts every 0.1-0.6 s for 3-6 s

The scan response repeats the same payload until the next frame (within a
burst, and between heartbeats). Treat a frame as new only when the payload
changes; bytes 4-5 are a frame counter.

A passive scanner observes the burst, so it can tell something changed, and briefly switch to active scanning to read the encrypted contents.

## Payload

```
plaintext = AES-128-ECB-decrypt(key = dpKey, data = payload)   # no padding
```

| Byte | Meaning | Example |
|---|---|---|
| 0 | Frame type: `0x10` event (state changed), `0x20` heartbeat. No other values seen | `10` |
| 1 | Class identifier (`01` on Retro Pro) | `01` |
| 2-3 | Battery voltage, mV, big endian | `0B29` = 2857 mV |
| 4-5 | Counter, +1 per new frame | `01B1` |
| 6-7 | Random | `2CA7` |
| 8-9 | Status (see below). Byte 8 has always been `FF` | `FFEA` |
| 10 | Vibration alert: `00` none, `10` "vibration activity", `20` "excessive force" (from the app; never seen non-zero on fw 04.46, see below) | `00` |
| 11-13 | Unknown, always `00 10 FF` so far. Byte 12 is *not* the vibration sensitivity | `00 10 FF` |
| 14-15 | Firmware, major and minor as decimal numbers | `04 2E` = 04.46 |

Example: `10 01 0B29 01B1 2CA7 FFEA 00 00 10 FF 04 2E` = event, 2.857 V, locked, firmware 04.46.

AES-ECB has no integrity check: a wrong key gives random bytes. A cheap
sanity check is byte 0 (high nibble set, low nibble clear) and bytes 14-15
(each below 100). Treat a key as wrong only if *every* frame fails.

### Quirks

1. Some event frames send a bad battery voltage (e.g. `0x0119` = 281 mV, `0x0110` = 272 mV). Suggest ignoring <1000 mV. The Kubu app does not, and briefly shows "Critical"
2. Battery levels in the Kubu app: low below 2181 mV, critical at 2134 mV or less. Once low or critical, it only goes back to good above 2600 mV (readings vary by ~150 mV between frames)
3. Knocks and hits produced no frame at all on fw 04.46, even with sensitivity set to *demo*; byte 10 stayed `00`

### Status Flags

How to read bytes 8-9 depends on the sensor's logic type, which the Kubu cloud reports per sensor.
The Retro Pro is type **8**. Take the low 6 bits, `status & 0x3F`:

| Low 6 bits | State |
|---|---|
| 42, 43, 46 | locked |
| 58, 59, 62 | closed, not locked (the door opens with the handle) |
| 63 | open |
| anything else | tampered |

Only 42 (`FFEA`), 58 (`FFFA`) and 63 (`FFFF`) have been seen, including
with part-turned thumbturns and a door left ajar. Read as three 2-bit
fields (bits 5-4, 3-2, 1-0), these are `10 10 10`, `11 10 10` and
`11 11 11`: the first field looks like the lock and the other two like door
contacts. That is an inference; the app gives the other values no meaning
beyond the table.

## Decryption Key

Each sensor has a hardcoded 16 byte `dpKey` (32 hex characters) set at the factory.
It is not in the QR code, which means there is no way to decrypt without connecting the sensor to Kubu first and retrieving the key.

Use the official Kubu app to register the sensor, and then the following API to pull the `dpKey` for offline decryption.
Or alternatively, look at the official Android app's logs (`adb logcat`): it logs full API responses, including `dpKey`, and every decrypted frame.

### 1. Sign in

```http
POST /oauth/login HTTP/1.1
Host: api-green.kubusmart.com
Content-Type: application/json; charset=UTF-8

{"email":"you@example.com","password":"your-password"}
```

Treat any 2xx as success (the exact status has not been confirmed; the
related token refresh call, `POST /oauth`, returns 202).

```http
HTTP/1.1 200 OK
Content-Type: application/json

{
  "idToken": "eyJraWQi…",
  "accessToken": "eyJraWQi…",
  "refreshToken": "eyJjdHki…",
  "homes": [{"id": "0123456789abcdef0123456789abcdef", "role": "sAdmin"}]
}
```

Errors are HTTP 401 or 403 with a JSON `code`:
* `1005`/`1008` wrong email or password
* `1105` too many attempts (locked for 5 minutes)
* `1141` email not verified

If `homes` is empty, `GET /users?type=all` (same bearer token) lists them
as `records[].homeId`.

### 2. Get the sensor keys

Send the **ID token** (`idToken`) as the bearer token.

```http
GET /view/rooms?homeId=0123456789abcdef0123456789abcdef HTTP/1.1
Host: api-green.kubusmart.com
Authorization: Bearer <idToken>
```

```http
HTTP/1.1 200 OK
Content-Type: application/json

{
  "homeId": "0123456789abcdef0123456789abcdef",
  "bleNodes": [
    {
      "sNo": "94A081123456",
      "devAlias": "KubuRetroPro-34-56",
      "dpKey": "00112233445566778899AABBCCDDEEFF",
      "provKey": "FFEEDDCCBBAA99887766554433221100",
      "type": 8,
      "class": "retroPro",
      "fwVer": "04.46"
    }
  ]
}
```

Match a sensor by `sNo`, its Bluetooth address without colons.
Use `type` to pick the status decoding (see [Status Flags](#status-flags)).
