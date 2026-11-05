#!/usr/bin/env python3
"""Boot the minimum Home Assistant and require HomeWizard Instant to set up.

The version is the ``homeassistant`` field in ``hacs.json``. The script pulls
the official Core image at that tag, starts it with this integration and the
P1 emulator, and exits non-zero unless the config entry reaches ``loaded``
and the power sensor follows a change made on the emulator.

Home Assistant Container:
https://www.home-assistant.io/installation/linux/#install-home-assistant-container
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any
import urllib.error
from urllib.parse import urlencode
import urllib.request

from p1_emulator import (
    AVERAGE_POWER_W,
    GAS_VALUE_M3,
    INITIAL_POWER_W,
    UPDATED_POWER_W,
)

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "ghcr.io/home-assistant/home-assistant"
CORE_NAME = "ha-min-check-core"
MOCK_NAME = "ha-min-check-mock"
MOCK_IMAGE = "python:3.12-alpine"
# Host networking: the library calls http://<ip>/ with no port, so the emulator
# must own port 80, and Core must be able to reach that address. A custom
# bridge is not used because some nested Docker daemons cannot route between
# containers. GitHub-hosted Linux runners support host networking.
MOCK_IP = "127.0.0.1"
HOST_PORT = 8123
USERNAME = "admin"
PASSWORD = "homewizard-dev"
BOOT_TIMEOUT_SECONDS = 180
UPDATE_TIMEOUT_SECONDS = 30


class CheckError(Exception):
    """The minimum-version boot did not produce a working config entry."""


@dataclass
class Running:
    """URLs and credentials for one booted Core instance."""

    base: str
    version: str
    token: str = ""


def minimum_version(root: Path) -> str:
    """Return the Home Assistant version declared in ``hacs.json``."""
    payload = json.loads((root / "hacs.json").read_text(encoding="utf-8"))
    version = payload.get("homeassistant")
    if not isinstance(version, str) or not version:
        raise CheckError("hacs.json is missing a homeassistant version")
    return version


def _run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a command and return its captured output."""
    return subprocess.run(args, check=check, text=True, capture_output=True)


def _log(message: str) -> None:
    """Print a progress line immediately."""
    print(message, flush=True)


def cleanup() -> None:
    """Remove containers left by this script."""
    for name in (CORE_NAME, MOCK_NAME):
        _run(["docker", "rm", "-f", name], check=False)


def _dump_logs() -> None:
    """Print the tails of both containers after a failed check."""
    for name in (CORE_NAME, MOCK_NAME):
        result = _run(["docker", "logs", "--tail", "120", name], check=False)
        print(f"--- docker logs {name} ---", file=sys.stderr)
        print(result.stdout, file=sys.stderr)
        print(result.stderr, file=sys.stderr)


def _ignore_bytecode(_directory: str, names: list[str]) -> set[str]:
    """Skip bytecode so the copied integration stays small."""
    return {name for name in names if name == "__pycache__" or name.endswith(".pyc")}


def _config_dir() -> Path:
    """Copy the integration into a fresh Home Assistant config directory."""
    config = Path(tempfile.mkdtemp(prefix="ha-min-check-"))
    destination = config / "custom_components" / "homewizard_instant"
    shutil.copytree(
        ROOT / "custom_components" / "homewizard_instant",
        destination,
        ignore=_ignore_bytecode,
    )
    return config


def _start_containers(version: str, config: Path) -> None:
    """Start the emulator and Core on the host network."""
    _log(f"Pulling {IMAGE}:{version}")
    _run(["docker", "pull", f"{IMAGE}:{version}"])
    emulator = ROOT / "scripts" / "p1_emulator.py"
    _log("Starting the P1 emulator")
    _run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            MOCK_NAME,
            "--network",
            "host",
            "-v",
            f"{emulator}:/emulator.py:ro",
            MOCK_IMAGE,
            "python",
            "/emulator.py",
        ]
    )
    _log(f"Starting Home Assistant {version}")
    _run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            CORE_NAME,
            "--network",
            "host",
            "-v",
            f"{config}:/config",
            f"{IMAGE}:{version}",
        ]
    )


def _http(
    method: str,
    url: str,
    body: dict[str, Any] | None = None,
    *,
    token: str | None = None,
    form: dict[str, str] | None = None,
    timeout: float = 180,
) -> tuple[int, Any]:
    """Perform one JSON or form request and return status plus a parsed body."""
    headers: dict[str, str] = {}
    data: bytes | None = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if form is not None:
        data = urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode()
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as err:
        payload = err.read().decode()
        try:
            parsed: Any = json.loads(payload) if payload else {}
        except json.JSONDecodeError:
            parsed = payload
        return err.code, parsed


def _wait_for_onboarding(base: str) -> Any:
    """Wait until Core answers onboarding and the API is actually up."""
    deadline = time.monotonic() + BOOT_TIMEOUT_SECONDS
    last: Any = None
    while time.monotonic() < deadline:
        try:
            status, body = _http("GET", f"{base}/api/onboarding", timeout=10)
            api_status, _api = _http("GET", f"{base}/api/", timeout=10)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
            last = str(err)
            time.sleep(2)
            continue
        if api_status in {200, 401} and status == 200 and isinstance(body, list):
            return body
        last = {"onboarding": status, "api": api_status}
        time.sleep(2)
    raise CheckError(f"Home Assistant did not finish booting: {last}")


def _exchange(base: str, code: str) -> str:
    """Turn an authorization code into an access token."""
    status, tokens = _http(
        "POST",
        f"{base}/auth/token",
        form={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": f"{base}/",
        },
    )
    if status != 200 or not isinstance(tokens, dict) or not tokens.get("access_token"):
        raise CheckError(f"token exchange failed: {status} {tokens}")
    return str(tokens["access_token"])


def _onboard(running: Running, steps: list[dict[str, Any]]) -> None:
    """Create the owner account and finish the first-boot wizard."""
    done = {row.get("step"): bool(row.get("done")) for row in steps}
    client_id = f"{running.base}/"
    status, created = _http(
        "POST",
        f"{running.base}/api/onboarding/users",
        {
            "client_id": client_id,
            "name": "Admin",
            "username": USERNAME,
            "password": PASSWORD,
            "language": "en",
        },
    )
    if status != 200 or not isinstance(created, dict) or not created.get("auth_code"):
        raise CheckError(f"onboarding user failed: {status} {created}")
    running.token = _exchange(running.base, str(created["auth_code"]))
    follow_ups = (
        ("core_config", "/api/onboarding/core_config", {}),
        ("analytics", "/api/onboarding/analytics", {}),
        (
            "integration",
            "/api/onboarding/integration",
            {"client_id": client_id, "redirect_uri": f"{running.base}/?auth_callback=1"},
        ),
    )
    for step, path, payload in follow_ups:
        if done.get(step):
            continue
        step_status, step_body = _http(
            "POST", f"{running.base}{path}", payload, token=running.token
        )
        if step_status not in {200, 204}:
            raise CheckError(f"onboarding {step} failed: {step_status} {step_body}")


def _advance(running: Running, flow_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Submit one config-flow step."""
    status, body = _http(
        "POST",
        f"{running.base}/api/config/config_entries/flow/{flow_id}",
        payload,
        token=running.token,
    )
    if status != 200 or not isinstance(body, dict):
        raise CheckError(f"config flow step failed: {status} {body}")
    return body


def _add_device(running: Running) -> str:
    """Create a config entry for the emulator."""
    status, started = _http(
        "POST",
        f"{running.base}/api/config/config_entries/flow",
        {"handler": "homewizard_instant", "show_advanced_options": False},
        token=running.token,
    )
    if status != 200 or not isinstance(started, dict):
        raise CheckError(f"config flow did not start: {status} {started}")
    flow_id = str(started.get("flow_id") or "")
    current = _advance(running, flow_id, {"ip_address": MOCK_IP})
    if current.get("type") != "create_entry":
        raise CheckError(f"config flow did not create an entry: {json.dumps(current)[:1500]}")
    result = current.get("result")
    if isinstance(result, dict) and result.get("entry_id"):
        return str(result["entry_id"])
    if isinstance(result, str):
        return result
    raise CheckError(f"create_entry had no entry id: {json.dumps(current)[:1500]}")


def _entry(running: Running, entry_id: str) -> dict[str, Any] | None:
    """Return the config entry once Core will list it."""
    status, body = _http(
        "GET",
        f"{running.base}/api/config/config_entries/entry?domain=homewizard_instant",
        token=running.token,
    )
    if status != 200 or not isinstance(body, list):
        return None
    for entry in body:
        if isinstance(entry, dict) and entry.get("entry_id") == entry_id:
            return entry
    return None


def _wait_until_loaded(running: Running, entry_id: str) -> None:
    """Wait until the entry is loaded, or fail on setup_error."""
    deadline = time.monotonic() + BOOT_TIMEOUT_SECONDS
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        last = _entry(running, entry_id)
        state = None if last is None else last.get("state")
        if state == "loaded":
            return
        if state == "setup_error":
            raise CheckError(f"config entry setup_error: {last}")
        time.sleep(2)
    raise CheckError(f"config entry did not load: {last}")


def _states(running: Running) -> list[dict[str, Any]]:
    """Return the state machine."""
    status, states = _http("GET", f"{running.base}/api/states", token=running.token)
    if status != 200 or not isinstance(states, list):
        raise CheckError(f"could not read states: {status}")
    return [state for state in states if isinstance(state, dict)]


def _number(state: dict[str, Any]) -> float | None:
    """Return a numeric entity state."""
    raw = state.get("state")
    if raw in {None, "unknown", "unavailable"}:
        return None
    try:
        return float(str(raw))
    except ValueError:
        return None


def _sensor_near(
    states: list[dict[str, Any]],
    *,
    device_class: str | None = None,
    entity_id_contains: str | None = None,
    value: float,
) -> dict[str, Any]:
    """Return the sensor whose state matches ``value``."""
    for state in states:
        entity_id = str(state.get("entity_id") or "")
        if not entity_id.startswith("sensor."):
            continue
        attributes = state.get("attributes")
        if not isinstance(attributes, dict):
            continue
        if device_class is not None and attributes.get("device_class") != device_class:
            continue
        if entity_id_contains is not None and entity_id_contains not in entity_id:
            continue
        number = _number(state)
        if number is not None and abs(number - value) < 0.05:
            return state
    sensor_ids = [
        state.get("entity_id")
        for state in states
        if str(state.get("entity_id", "")).startswith("sensor.")
    ]
    raise CheckError(
        f"no sensor matched device_class={device_class} "
        f"id~={entity_id_contains} value={value}; saw {sensor_ids}"
    )


def _set_power(value: float) -> None:
    """Tell the emulator to serve a new total power."""
    status, body = _http(
        "POST",
        f"http://{MOCK_IP}/control",
        {"active_power_w": value},
        timeout=10,
    )
    if status != 200:
        raise CheckError(f"emulator control failed: {status} {body}")


def _wait_for_power(running: Running, entity_id: str, value: float) -> None:
    """Wait until one power sensor reports ``value``."""
    deadline = time.monotonic() + UPDATE_TIMEOUT_SECONDS
    last: Any = None
    while time.monotonic() < deadline:
        for state in _states(running):
            if state.get("entity_id") != entity_id:
                continue
            last = state.get("state")
            number = _number(state)
            if number is not None and abs(number - value) < 0.05:
                return
        time.sleep(1)
    raise CheckError(f"{entity_id} did not update to {value}; last state {last}")


def _reject_import_errors() -> None:
    """Fail when Core logged an import crash while loading the integration."""
    result = _run(["docker", "logs", CORE_NAME], check=False)
    logs = f"{result.stdout}\n{result.stderr}"
    if "No module named" in logs and "homewizard" in logs.lower():
        raise CheckError("Home Assistant logged 'No module named' while loading the integration")


def _check_entities(running: Running) -> str:
    """Require power, average demand, and the gas meter, and return the power id."""
    states = _states(running)
    power = _sensor_near(states, device_class="power", value=INITIAL_POWER_W)
    average = _sensor_near(states, entity_id_contains="average_demand", value=AVERAGE_POWER_W)
    gas = _sensor_near(states, device_class="gas", value=GAS_VALUE_M3)
    power_id = str(power["entity_id"])
    _log(
        "Loaded "
        f"{power_id}={power.get('state')}, "
        f"{average.get('entity_id')}={average.get('state')}, "
        f"{gas.get('entity_id')}={gas.get('state')}"
    )
    return power_id


def run_check() -> None:
    """Boot Core at the hacs.json minimum and require a live, updating power sensor."""
    version = minimum_version(ROOT)
    config = _config_dir()
    try:
        _start_containers(version, config)
        running = Running(base=f"http://127.0.0.1:{HOST_PORT}", version=version)
        _log("Waiting for Home Assistant")
        steps = _wait_for_onboarding(running.base)
        if not isinstance(steps, list):
            raise CheckError("onboarding status was not a list")
        _onboard(running, steps)
        _log("Adding the emulated P1 meter")
        entry_id = _add_device(running)
        _wait_until_loaded(running, entry_id)
        _reject_import_errors()
        power_id = _check_entities(running)
        _log(f"Changing emulator power to {UPDATED_POWER_W}")
        _set_power(UPDATED_POWER_W)
        _wait_for_power(running, power_id, UPDATED_POWER_W)
        _log(
            f"HomeWizard Instant loaded on Home Assistant {version}; "
            f"{power_id} updated from {INITIAL_POWER_W} to {UPDATED_POWER_W}"
        )
    finally:
        shutil.rmtree(config, ignore_errors=True)


def main() -> None:
    """Run the check and always remove the containers it started."""
    cleanup()
    try:
        run_check()
    except CheckError as err:
        _dump_logs()
        print(err, file=sys.stderr)
        raise SystemExit(1) from err
    except Exception:
        _dump_logs()
        raise
    finally:
        cleanup()


if __name__ == "__main__":
    main()
