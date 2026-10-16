# Holix Play for Home Assistant

[![Validate](https://github.com/HolixPlay/holix-homeassistant/actions/workflows/validate.yml/badge.svg)](https://github.com/HolixPlay/holix-homeassistant/actions/workflows/validate.yml)
[![Tests](https://github.com/HolixPlay/holix-homeassistant/actions/workflows/tests.yml/badge.svg)](https://github.com/HolixPlay/holix-homeassistant/actions/workflows/tests.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)

Home Assistant integration for [Holix Play](https://holixplay.com) (HACS custom integration,
`custom_components/holix`), plus the specification of the local control protocol the app exposes on your
network (HLCP v1, [`docs/protocol.md`](docs/protocol.md)).

**How it works:** while Holix Play (Apple TV, iPhone/iPad, Mac, Android/Android TV …) is in the foreground,
it opens a local WebSocket endpoint and advertises it as `_holixplay._tcp`. Home Assistant discovers it via
zeroconf, pairs once using a 6-digit PIN shown on screen, and from then on state and events are **pushed**
(`iot_class: local_push`). No cloud, and no Home Assistant URL or token has to be entered in the app.

Requires Home Assistant 2025.2 or newer.

## Installation

### HACS (recommended)

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/HolixPlay/holix-homeassistant`,
   category **Integration**.
2. Search for **Holix Play** in HACS and download it.
3. Restart Home Assistant.

### Manual

1. Copy `custom_components/holix` into the `custom_components` folder of your Home Assistant
   configuration directory (so you have `<config>/custom_components/holix/manifest.json`).
2. Restart Home Assistant.

## Pairing

1. Open Holix Play on the device and keep it in the foreground.
2. Home Assistant shows a **Discovered** notification (Settings → Devices & services). Click **Add**.
   Without discovery: Settings → Devices & services → **Add integration** → **Holix Play**, then enter the
   device's IP address (default port `47861`).
3. Enter the 6-digit code shown on the TV/device screen.

Pairing is per device; each Holix Play device becomes its own config entry. If the pairing is removed in the
app (Settings → Connected home systems), Home Assistant asks you to pair again.

## Entities

Entity IDs below assume a device named "Living Room Apple TV".

| Entity | Example | What it does |
|---|---|---|
| `media_player` | `media_player.living_room_apple_tv` | State (`off` while the app is closed or in the background), title/series/season/episode/artwork/position; play/pause/stop/seek/next/previous; `play_media` (Jellyfin item ID); volume only if the device supports it |
| `sensor` | `…_profile`, `…_media_type`, `…_dynamic_range`, `…_segment`, `…_video_resolution`, `…_audio_format` | Active profile, content type, dynamic range (SDR/HDR10/HDR10+/Dolby Vision/HLG), media segment (intro/outro/…), resolution, audio format |
| `binary_sensor` | `…_hdr`, `…_screensaver` | HDR active, in-app screensaver |
| `select` | `select.living_room_apple_tv_profile` | Switch profile (PIN-protected profiles cannot be selected) |
| `light` | `light.living_room_apple_tv_ambilight` | Holix ambilight on/off + brightness |
| `event` | `event.living_room_apple_tv_playback` | Playback started/paused/resumed/stopped, segment started/ended, profile changed, screensaver started/stopped |
| `remote` | `remote.living_room_apple_tv_navigation` | In-app navigation keys: `up` `down` `left` `right` `select` `back` `home` `menu` `play_pause` |

Also provided:

- **Device triggers** for every event type above, and a `holix_event` bus event
  (`device_id`, `holix_id`, `type`, plus the event payload).
- **Service** `holix.show_notification` — shows a toast on screen (e.g. doorbell) without interrupting playback.
- **Options → Use Home Assistant lights as ambilight:** selected HA lights follow a screen zone
  (whole screen, left/right/top/bottom edge, center) at 1–10 updates per second (2 recommended for
  Hue/Zigbee). This is a soft ambient effect; for real-time ambilight use WLED/Holix Light directly in the app.

## Automation examples

Dim the lights when a movie starts, bring them back for the credits:

```yaml
automation:
  - alias: Movie starts — dim lights
    trigger:
      - platform: state
        entity_id: event.living_room_apple_tv_playback
    condition:
      - condition: template
        value_template: >
          {{ trigger.to_state.attributes.event_type == 'playback_started'
             and states('sensor.living_room_apple_tv_media_type') == 'movie' }}
    action:
      - service: light.turn_on
        target: {entity_id: light.living_room}
        data: {brightness_pct: 10, transition: 3}

  - alias: Credits — lights up
    trigger:
      - platform: event
        event_type: holix_event
        event_data: {type: segment_started}
    condition:
      - condition: template
        value_template: "{{ trigger.event.data.segment == 'outro' }}"
    action:
      - service: light.turn_on
        target: {entity_id: light.living_room}
        data: {brightness_pct: 70, transition: 5}
```

Doorbell notification on the TV:

```yaml
action:
  - service: holix.show_notification
    target: {entity_id: media_player.living_room_apple_tv}
    data:
      title: Doorbell
      message: Someone is at the door
      duration: 8
```

Device triggers ("Holix Play → Playback started" etc.) are also available in the automation editor.

## Troubleshooting

- **Not discovered / cannot connect:** Holix Play must be open in the foreground (apps are suspended in
  the background) and on the same network/VLAN as Home Assistant; mDNS (`_holixplay._tcp`) must not be
  blocked. Try adding it manually by IP and port `47861`.
- **Media player shows `off`:** expected while the app is closed or in the background; the integration
  reconnects automatically when the app comes back.
- **"Pair again" repair:** the pairing was removed in the app. Open Holix Play and follow the prompt; a new
  code appears on screen.
- **"The app is newer than this integration":** update the integration via HACS.
- **Volume controls missing:** on tvOS the app does not control volume (use your TV/AVR integration).
- **Debug logs:**
  ```yaml
  logger:
    logs:
      custom_components.holix: debug
  ```

## Repository layout

- `custom_components/holix/` — the integration. `api.py` is a protocol client independent of Home Assistant.
- `docs/protocol.md` — **normative protocol** (discovery TXT, pairing, HMAC auth, state, commands, events,
  color stream); Turkish version: `docs/protokol.tr.md`.
- `docs/protocol.schema.json` — JSON Schema for all messages.
- `tests/` — pytest (`pytest-homeassistant-custom-component`); `tests/fake_device.py` is the reference
  device, `tests/fixtures/*.json` are sample messages.
- `tools/holix_probe.py` — conformance tool that checks a real app against the protocol.

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/python -m pytest -q
```

Try it with the fake device: `.venv/bin/python -I tests/fake_device.py --port 47861 --pin 123456`;
check a real app: `.venv/bin/python tools/holix_probe.py <ip> --commands --colors 4 --listen 30`.

## License

Apache-2.0 — see [LICENSE](LICENSE).
