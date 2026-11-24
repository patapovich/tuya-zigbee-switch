# ZDMS16-2 module firmware downloads

How often Zigbee2MQTT has downloaded the AVATTO ZDMS16-2 module firmware of this fork (it
carries [Open Dimmer](https://github.com/patapovich/open-dimmer) for the dimming MCU). Every
build is a [release](https://github.com/patapovich/tuya-zigbee-switch/releases) (tag `zdms16-2-<build>`) and the OTA index
points at its files, so these are GitHub's download counts of those files.

- **Updates**: the normal image, an update from an earlier build of this firmware.
- **Conversions**: the `-from_tuya` image, a module converted from the Tuya stock firmware.
- **Forced**: the `-forced` image of the force index.

| | Downloads |
|---|---:|
| Updates | 11 |
| Conversions | 5 |
| Forced | 5 |

Counts as of 2026-11-24 19:05 UTC (the last change; checked every hour by `.github/workflows/ota-downloads.yml` on `main`, GitHub's counts lag a few minutes). History:
[history.csv](history.csv).

## Per build

Newest first. Builds nobody downloaded are deleted beyond the 10 newest.

| Build | Released | Open Dimmer | Updates | Conversions | Forced |
|---|---|---|---:|---:|---:|
| [1.1.3-d903f5ea](https://github.com/patapovich/tuya-zigbee-switch/releases/tag/zdms16-2-1.1.3-d903f5ea) | 2026-11-24 | 2.2.8 | 3 | 1 | 1 |
| [1.1.3-d983b68a](https://github.com/patapovich/tuya-zigbee-switch/releases/tag/zdms16-2-1.1.3-d983b68a) | 2026-11-21 | 2.2.6 | 2 | 1 | 1 |
| [1.1.3-09a180fa](https://github.com/patapovich/tuya-zigbee-switch/releases/tag/zdms16-2-1.1.3-09a180fa) | 2026-11-20 | 2.2.6 | 2 | 1 | 1 |
| [1.1.3-8ea2f5f2](https://github.com/patapovich/tuya-zigbee-switch/releases/tag/zdms16-2-1.1.3-8ea2f5f2) | 2026-11-20 | 2.2.6 | 2 | 1 | 1 |
| [1.1.3-2b74dd50](https://github.com/patapovich/tuya-zigbee-switch/releases/tag/zdms16-2-1.1.3-2b74dd50) | 2026-11-20 | 2.2.5 | 2 | 1 | 1 |

## Notes

- 2026-11-20: the first downloads of 1.1.3-2b74dd50 (updates 2, conversions 1, forced 1) were one test and one fetch of every asset (a person or a crawler on the release page).
