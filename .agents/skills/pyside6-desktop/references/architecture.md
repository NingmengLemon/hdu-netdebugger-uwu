# Architecture Patterns

Structural decisions that proved out in production for GUDE Deploy.

## Rule 20: Wizard-style page flow with central orchestrator

```
QStackedWidget holds all pages (QWidget subclasses)
GudeDeployApp orchestrator wires signals between pages
Pages NEVER import or reference each other
All communication flows through the orchestrator via signals
```

Each page implements:
- `_setup_ui()` — build layout
- `load_*()` / `start_*()` — populate from data
- `reset()` — clear state for next cycle
- Custom `Signal()` definitions for events

## Rule 21: Pydantic v2 models for data, QWidget for UI

Don't mix QObject signals with Pydantic models. They serve different purposes.

```python
# Pydantic model — data only
class WorkListItem(BaseModel):
    hostname: str
    ip: str
    status: Literal["pending", "deployed", "failed"] = "pending"

# QWidget — UI only, communicates via Qt signals
class DiscoveryPage(QWidget):
    device_discovered = Signal(object)
```

Pydantic v2 gotcha: access `model_fields` from the **class**, not the instance:
```python
type(obj).model_fields  # RIGHT
obj.model_fields         # WRONG in Pydantic v2.11+
```

## Rule 22: Template + worklist override ordering

When applying config from multiple sources, order matters — last write wins:

1. Load device's current config from hardware
2. Apply template (sets standard config like SNMP, port names)
3. Apply worklist overrides (sets hostname, IP, netmask, gateway)

Worklist overrides MUST run after template to ensure network config is correct.

## Rule 23: `force_field()` for guaranteed deployment

Standard `update_field()` + `set_field_deploy()` checks equality — if the value matches the device's current value, `will_deploy` returns False (field is pristine). This silently skips deployment on re-deploys.

`force_field()` bypasses the equality check entirely:

```python
def force_field(self, section, field_name, value, port_number=None):
    fc = self.get_field(section, field_name, port_number)
    if fc:
        fc.current_value = value
        fc.field_status = FieldStatus.DIRTY      # unconditional
        fc.deploy_state = DeployState.MANUAL_ON   # unconditional
    if self.working_config:
        self._sync_field_to_config(section, field_name, value, port_number)
```

Use for worklist overrides where the value MUST deploy regardless of current device state.

## Rule 24: Subnet scanning limits

Don't refuse to scan subnets larger than /24. Production networks use /23 (510 hosts) or /22 (1022 hosts). Set `HTTP_SCAN_MAX_HOSTS = 1022` to support up to /22. With 50 parallel workers and 0.5s timeout, 510 hosts scans in ~5 seconds.

## Rule 25: HTTPS-only device discovery

Some devices have port 80 closed and only serve on port 443. Discovery must try HTTP first, then fall back to HTTPS:

```python
client = ApiClient(host=ip, port=80)
if not client.health_check():
    client.close()
    client = ApiClient(host=ip, port=443, use_ssl=True)
    if not client.health_check():
        return None  # device not responding on either
```
