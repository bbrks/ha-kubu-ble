# Kubu BLE protocol

Verified against a Kubu Retro Pro (`KubuRetroPro-XX-XX` running firmware 04.46)

State is broadcast with no active connection required. The state value is encrypted with a per-sensor key that appears to be set at manufacturing time (it is never written to the sensor during pairing).

## Advertising

Legacy advertising, connectable and scannable.

| Packet | Content |
|---|---|
| ADV_IND | Flags `06`, complete local name `KubuRetroPro-XX-XX`, sometimes 16-bit service UUID `0xFC5E`. The app names other sensors `KubuDoorSensor-XX-XX` (see [Other sensors](#other-sensors-unverified)) |
| SCAN_RSP | Manufacturer specific data: company ID **3535** (`0x0DCF`, little endian `CF 0D`) + 16 byte payload. Raw AD structure: `13 FF CF 0D` + 16 bytes |

The state is **only in the scan response**, so the scanner must scan actively (send `SCAN_REQ`).

Timing:
- **Idle:** 2-4 adverts about every 30 s. The payload changes to a new heartbeat frame about every 60-90 s
- **State change:** a burst of adverts every 0.1-0.6 s for 3-6 s

The scan response repeats the same payload until the next frame (within a
burst, and between heartbeats). Treat a frame as new only when the payload
changes; bytes 4-5 are a frame counter. A payload is never sent again
later, so with more than one receiver (where one may hand back an older
payload) remember the recent payloads, not just the last one.

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

### Setup mode advert (not encrypted)

In setup (pairing) mode the sensor sends its manufacturer data **in the clear**.
The Kubu app (8.0.8, setup and firmware update screens) checks byte 0 of the
raw data before it decrypts anything:

| Byte | Meaning |
|---|---|
| 0 | `0F` = setup mode |
| 1 | Class identifier (the Retro Pro setup screen wants `01`) |
| 2-3 | Firmware, major and minor |
| 10-15 | The sensor's MAC address |

Never seen from a sensor that is already set up. If it is sent, it does not
decrypt, so it must not count as a sign of a wrong key. The integration skips
a raw payload that starts with `0F` and does not decode.

### Quirks

1. Some event frames send a bad battery voltage (e.g. `0x0119` = 281 mV, `0x0110` = 272 mV). Suggest ignoring <1000 mV. The Kubu app does not, and briefly shows "Critical"
2. Battery levels in the Kubu app: low below 2181 mV, critical at 2134 mV or less. Once low or critical, it only goes back to good above 2600 mV (readings vary by ~150 mV between frames)
3. Knocks and hits produced no frame at all on fw 04.46, even with sensitivity set to *demo*; byte 10 stayed `00`

### Status Flags

How to read bytes 8-9 depends on the sensor's class and logic type, which the Kubu cloud reports per sensor.
The Retro Pro is class `retroPro`, type **8**. Take the low 6 bits, `status & 0x3F`:

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

### Other sensors (unverified)

Everything below is copied from the Kubu Android app 8.0.8
(`BLEScannerService.evaluateSensorState()` and `evaluateDoorSensorState()`).
None of it has been checked against a real sensor. Reports welcome.

The app picks the decoder like this:

1. If the class is `door`, use the [door sensor table](#class-door-door-sensor), whatever the type.
2. Otherwise, use the logic type. For any type it does not know, the app
   shows "open". This integration refuses to set those up instead.

The class comes from the start of the sensor's serial number (on the QR
code) when it is set up in the app:

| Serial starts with | Class | Bluetooth name |
|---|---|---|
| `AVR` (Retro Pro / Anywhere Pro hardware) | `retroPro` | `KubuRetroPro-XX-XX` |
| `AVM` (Kubu Door Sensor) | `door` | `KubuDoorSensor-XX-XX` |
| anything else | `window` | `KubuDoorSensor-XX-XX` |

So an Anywhere Pro on a window still has class `retroPro`; only the type
tells it apart from a Retro Pro. The type is set during setup, and some
setup screens change it after a calibration step (for example 4 to 5, or 2
to 40/41).

| Type | Product in the app (`productType`) | States |
|---|---|---|
| 2 | Surface mounted / open-closed setup (`gen-w`, `gen-d`); also the fallback when a window calibration fails | open, closed |
| 3 | Vertical sliding window, one sensor per sash (`vs-w`) | open, closed |
| 4, 5 | "Works with Kubu" window (`sas-w`). 5 is the mirror of 4 | open, closed, secure, tampered |
| 11, 12 | Same decoding as 4 and 5. No setup screen in the app picks them | open, closed, secure, tampered |
| 8 | Retro Pro (`af-d`). **Verified**, see above | open, closed, locked, tampered |
| 9, 10 | "Works with Kubu" tilt & turn window (`tt-w`). 10 is roughly the mirror of 9 | open, tilt, secure, tampered |
| 40, 41 | "Works with Kubu" window, set by a newer calibration screen | open, closed, secure, unknown |

"Secure" is what the app shows for a closed and locked window. This
integration treats it the same as "locked". "Tilt" is shown as "Vent
mode".

#### Class `door` (Door Sensor)

The Kubu Door Sensor fits into a slot in a lift-lever door lock. Whole
16-bit status, exact match:

| Status | State |
|---|---|
| `FF01` | closed, not locked (the app shows "Unlocked, Closed") |
| `FF10` | locked |
| `FF11` | open |
| `FFFF` | tampered |
| anything else | unknown |

#### Types 2 and 3

Low 6 bits: 55, 59 or 63 is open, anything else is closed.

#### Types 4, 5, 11, 12

Read the low 6 bits as three 2-bit fields: A = bits 1-0, B = bits 3-2,
C = bits 5-4. A field is *set* when both its bits are 1.

1. 42 or 21: tampered
2. 63: open
3. Type 4 and 11: C set and A not set is secure. Anything else is closed.
4. Type 5 and 12: A set and C not set is secure. Anything else is closed.

#### Types 9 and 10 (tilt & turn)

Only byte 9 (`status & 0xFF`) is used. The app checks these sets in order:

| Set | Type 9 values | Type 10 values |
|---|---|---|
| secure | 253 189 245 181 213 149 229 165 173 | 235 171 234 170 233 169 239 175 237 173 191 |
| tilt (handle can move) | 215 151 223 159 246 182 230 166 214 150 222 158 254 190 218 154 250 186 | same as type 9 |
| tilt | 247 183 231 167 219 155 217 153 251 187 249 185 | 183 231 167 219 155 217 153 251 187 249 185 |
| open | 235 171 234 170 233 169 239 175 237 191 255 | 253 189 245 181 213 149 229 165 247 255 |
| anything else | tampered | tampered |

The decoder has memory. A value in the "tilt (handle can move)" set sets a
flag; "secure" and plain "tilt" clear it. While the flag is set, 255 reads
as tilt instead of open. The flag is per sensor.

#### Types 40 and 41

Only byte 9: 1 open, 2 closed, 3 secure, anything else unknown.

## Vibration sensitivity (GATT write)

The only setting, changed by the Kubu app over a GATT connection. The app
first asks the user to press the sensor's button until its LED turns green,
and afterwards to turn the key. Not needed to read state.

- Service `b16a326d-26e3-488f-8bc6-086b9bf17e51`,
  characteristic `23268b5d-2fca-4447-9f66-9e768580c89d`
- Value: `AES-128-ECB-encrypt(key = provKey, data = cmd FF FF … FF)`
  (16 bytes). The app's options and the value it saves as `vibSetting`:

  | App option | `vibSetting` | `cmd` |
  |---|---|---|
  | Off | `off` | `40` |
  | Demo | `demo` | `30` |
  | Low | `low` | `10` |
  | Default | `high` | `20` |

- `provKey` is a second per-sensor key from the same API as `dpKey`
- In testing, the app's first connection attempt failed (Android GATT
  error 133) and a retry worked

The setting is not reported in the broadcast frames.

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

#### Tokens and refresh

Measured from the app (8.0.8) on 2026-11-25:

- `idToken` and `accessToken` are RS256 JWTs valid for **5 minutes** (`expiresIn: 300`).
  The `idToken`'s `sub` claim is the account's user ID.
- `refreshToken` is a Cognito JWE (`RSA-OAEP` / `A256GCM`). It is encrypted, so its expiry can't be read.
- The app never keeps the password. On every launch it refreshes instead:

```http
POST /oauth HTTP/1.1
Host: api-green.kubusmart.com
Content-Type: application/json; charset=UTF-8

{"refreshToken":"eyJjdHki…"}
```

```http
HTTP/1.1 202 Accepted
Content-Type: application/json

{"idToken": "eyJraWQi…", "accessToken": "eyJraWQi…", "tokenType": "Bearer", "expiresIn": 300}
```

- The answer has **no new refresh token**: the same one is used again (no rotation).
  It has no `homes` either, so use `GET /users?type=all` after a refresh.
- The refresh token lasted at least 9 days (the ID token's `auth_time`, the last password
  sign-in, was 9 days before the refresh). The upper limit is not known yet; Cognito's
  default is 30 days. `dev/kubu_cloud_check.py --save-token` / `--token` can test it.

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
      "vibSetting": "high",
      "type": 8,
      "class": "retroPro",
      "fwVer": "04.46"
    }
  ]
}
```

Match a sensor by `sNo`, its Bluetooth address without colons.
Use `type` and `class` to pick the status decoding (see [Status Flags](#status-flags)).
