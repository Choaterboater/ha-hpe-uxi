# HPE UXI for Home Assistant (unofficial)

> Not affiliated with or endorsed by Hewlett Packard Enterprise (HPE). "HPE UXI"
> is used only to name the service this integration talks to (HPE Aruba Networking
> User Experience Insight). No HPE or Aruba logos are included; the icon is
> original artwork.

Brings an HPE Aruba Networking UXI workspace into Home Assistant, so a UXI sensor
going offline or a new UXI issue can drive an automation. It is a sibling of the
**HPE Central** and **HPE Mist** integrations. It only reads: nothing in UXI is
changed. One config entry per UXI workspace (GreenLake workspace) gives you:

- **A workspace device** with:
  - *Sensors online* and *Sensors offline*
  - *Active issues*: issues that are not resolved, across all sensors. Attribute
    `issues` (sensor, code, severity, network, service test, interface, time),
    newest first.
  - *Agents* (attribute `agents`: name, serial, model, group)
  - *Service tests enabled* (attributes `total` and `tests`: name, category,
    target, enabled)
  - *Networks* (attribute `networks`: name, wireless / wired, SSID, security, VLAN)
  - an **Events** entity (see below)
- **A device per UXI sensor**, under the workspace: *Online*, *Testing*, *Issue*
  (on while the sensor has an issue that is not resolved), *Issues* (count;
  attribute `issues`) and, as a diagnostic, *Group*.
- **A device per UXI agent** (UXI running as software on a laptop, phone or VM,
  sometimes called a virtual sensor), under the workspace: name, model, serial,
  MACs and, as a diagnostic, *Group*. UXI's API gives no online or issue status
  for agents, so they have no Online / Issue entities.

If the API client may not read agents (or another list), the log says so once
(`UXI agents not read: UXI refused the API client`). The diagnostics download
(Settings → Devices & services → HPE UXI → ⋮ → Download diagnostics) lists what
UXI returned, with credentials, serials, MACs and notes removed.

List attributes are capped at 200 rows and kept out of the recorder.

### Events

The workspace's **Events** entity fires:

| Event type | Data |
|---|---|
| `sensor_offline`, `sensor_online` | sensor, sensor_id, serial |
| `issue_confirmed`, `issue_resolved` | sensor, sensor_id, issue_id, code, severity, network, service_test, timestamp |

Nothing fires on the first check after start, so a restart does not replay old issues.

## Install

1. HACS → ⋮ → Custom repositories → `https://github.com/Choaterboater/ha-hpe-uxi`, type *Integration*.
2. Download **HPE UXI**, restart Home Assistant.
3. Settings → Devices & services → Add integration → **HPE UXI**.

## Set up

In HPE GreenLake: Manage Workspace → Personal API clients → Create personal API
client, and pick your UXI service. Enter its **client ID** and **client secret**.
Leave the UXI API host at its default (`https://api.capenetworks.com`) unless HPE
gave you another one. The secret is stored only in the config entry and is never
logged.

Options: *Check UXI every (seconds)*, 60 by default (30 to 600).

## How it reads UXI

- Tokens come from GreenLake (`sso.common.cloud.hpe.com`, client credentials) and
  are reused until a minute before they expire (2 hours), and renewed once on a 401.
- Every check reads each sensor's status (`/networking-uxi/v1alpha1/sensors/{id}/status`):
  one request per sensor. The sensor, agent, service test and network lists are
  read every 5 minutes. Requests are spaced at least 0.35 s apart.
- A list the API client may not read (403) or that does not exist (404) is skipped.
  If UXI refuses the status of one sensor, that sensor keeps its last status.
- On 429 or 5xx the checks back off (up to 5 minutes). Through short trouble the
  last state is kept; after 5 minutes without a good check the entities become
  unavailable.

## Limits

- UXI's public API (`v1alpha1`) gives each sensor's online / testing state and its
  issues. Test results and metrics are not in this API: UXI sends those to its data
  push destinations (S3, Elasticsearch, BigQuery, HTTP).
- Removing a sensor in UXI removes its device here at the next restart or reload.

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements_test.txt
.venv/bin/pytest -q
```

The tests use a fake GreenLake and UXI with sample data, not from a real network.
