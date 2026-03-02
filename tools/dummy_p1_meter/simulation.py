"""Simulation model for the dummy HomeWizard P1 meter."""

from __future__ import annotations

import calendar
from collections import deque
from dataclasses import dataclass
from datetime import datetime
import math
import random
import secrets
import threading
import time
from typing import Any
from zoneinfo import ZoneInfo

from .constants import DEFAULT_HOUSEHOLD_YEARLY_KWH


@dataclass(slots=True)
class ApplianceSpike:
    """Transient appliance load component."""

    watts: float
    remaining_s: float


def clamp(value: float, minimum: float, maximum: float) -> float:
    """Clamp value to an inclusive range."""
    return max(minimum, min(maximum, value))


def gaussian(x: float, mean: float, sigma: float) -> float:
    """Return a unit gaussian value for profile shaping."""
    if sigma <= 0:
        return 0.0
    return math.exp(-0.5 * ((x - mean) / sigma) ** 2)


def rssi_to_strength(rssi_db: int) -> int:
    """Convert RSSI dB value to 0..100 strength percentage."""
    if rssi_db <= -100 or rssi_db == 0:
        return 0
    if rssi_db >= -50:
        return 100
    return int(2 * (rssi_db + 100))


class TokenStore:
    """In-memory token registry keyed by local user name."""

    _MAX_ACTIVE_TOKENS = 256

    def __init__(self) -> None:
        self._token_by_name: dict[str, str] = {}
        self._name_by_token: dict[str, str] = {}

    def issue(self, name: str) -> str:
        """Issue or return a stable token for a local user name."""
        token = self._token_by_name.get(name)
        if token is not None:
            return token

        # Keep token memory bounded for long-running simulator sessions.
        if len(self._token_by_name) >= self._MAX_ACTIVE_TOKENS:
            oldest_name, oldest_token = next(iter(self._token_by_name.items()))
            self._token_by_name.pop(oldest_name, None)
            self._name_by_token.pop(oldest_token, None)

        token = secrets.token_hex(32)
        self._token_by_name[name] = token
        self._name_by_token[token] = name
        return token

    def is_valid(self, token: str | None) -> bool:
        """Return whether token is currently authorized."""
        if token is None:
            return False

        return token in self._name_by_token

    def revoke(self, token: str | None = None, name: str | None = None) -> None:
        """Revoke one token by token or name, or all tokens when both are None."""
        if token is not None:
            entry_name = self._name_by_token.pop(token, None)
            if entry_name is not None:
                self._token_by_name.pop(entry_name, None)
            return

        if name is not None:
            entry_token = self._token_by_name.pop(name, None)
            if entry_token is not None:
                self._name_by_token.pop(entry_token, None)
            return

        self._token_by_name.clear()
        self._name_by_token.clear()

    @property
    def count(self) -> int:
        """Return number of active tokens."""
        return len(self._name_by_token)


class P1Simulation:
    """Stateful 1 Hz simulation model for v1/v2 endpoints."""

    # Proxy factors derived from Belgian 2025 Elia total-load ratios and tuned for
    # household simulation scaling. Means are near 1.0 across a full week/year.
    _WEEKDAY_HOURLY_LOAD_FACTORS = (
        0.854,
        0.838,
        0.837,
        0.858,
        0.918,
        1.008,
        1.083,
        1.134,
        1.158,
        1.165,
        1.160,
        1.144,
        1.125,
        1.100,
        1.082,
        1.086,
        1.109,
        1.114,
        1.085,
        1.047,
        1.012,
        0.976,
        0.935,
        0.892,
    )
    _WEEKEND_HOURLY_LOAD_FACTORS = (
        0.834,
        0.814,
        0.804,
        0.802,
        0.816,
        0.845,
        0.884,
        0.933,
        0.977,
        1.008,
        1.027,
        1.020,
        0.994,
        0.968,
        0.952,
        0.959,
        0.992,
        1.012,
        0.996,
        0.972,
        0.943,
        0.914,
        0.880,
        0.842,
    )
    _MONTHLY_LOAD_FACTORS = (
        1.140,
        1.127,
        0.991,
        0.950,
        0.917,
        0.933,
        0.915,
        0.929,
        0.976,
        1.001,
        1.032,
        1.098,
    )

    def __init__(
        self,
        *,
        seed: int,
        timezone_name: str,
        latitude: float,
        pv_peak_w: float,
        serial: str,
        api_enabled: bool,
        v2_auto_authorize: bool,
        household_yearly_kwh: float = DEFAULT_HOUSEHOLD_YEARLY_KWH,
    ) -> None:
        self._rng = random.Random(seed)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._ticker_thread: threading.Thread | None = None

        self._tz = ZoneInfo(timezone_name)
        self._latitude_rad = math.radians(latitude)
        self._pv_peak_w = max(0.0, pv_peak_w)
        self._household_yearly_kwh = clamp(household_yearly_kwh, 1200.0, 9000.0)
        self._average_household_power_w = self._household_yearly_kwh * 1000.0 / 8760.0
        weekday_mean = sum(self._WEEKDAY_HOURLY_LOAD_FACTORS) / 24.0
        weekend_mean = sum(self._WEEKEND_HOURLY_LOAD_FACTORS) / 24.0
        weekly_mean = ((5.0 * weekday_mean) + (2.0 * weekend_mean)) / 7.0
        yearly_month_mean = sum(self._MONTHLY_LOAD_FACTORS) / 12.0
        self._load_profile_normalization = max(0.001, weekly_mean * yearly_month_mean)
        self._serial = serial
        self._api_enabled = api_enabled
        self._v2_auto_authorize = v2_auto_authorize
        self._cloud_enabled = True
        self._status_led_brightness_pct = 100

        self._start_monotonic = time.monotonic()
        self._last_tick_monotonic = self._start_monotonic
        now = datetime.now(self._tz)
        self._month_marker = (now.year, now.month)

        self._wifi_ssid = "P1-SIM-WIFI"
        self._wifi_rssi_db = -58
        self._cloud_factor = 0.9

        self._spikes: list[ApplianceSpike] = []
        self._power_window: deque[tuple[float, float]] = deque()

        self._energy_import_t1_kwh = 3821.527
        self._energy_import_t2_kwh = 2419.944
        self._energy_export_t1_kwh = 281.333
        self._energy_export_t2_kwh = 174.881
        self._monthly_peak_w = 0.0

        self._sag_counts = [0, 0, 0]
        self._swell_counts = [0, 0, 0]
        self._any_power_fail_count = 0
        self._long_power_fail_count = 0

        self._measurement_v1_payload: dict[str, Any] = {}
        self._system_payload: dict[str, Any] = {}
        self._last_sample_dt = now
        self._last_load_w = 0.0
        self._last_solar_w = 0.0
        self._last_hourly_load_factor = 1.0
        self._last_seasonal_load_factor = 1.0
        self._last_combined_load_factor = 1.0

        self._tokens = TokenStore()

        # Prime with an initial sample.
        self._tick_locked(now=self._last_sample_dt, dt_s=1.0)

    @property
    def api_enabled(self) -> bool:
        """Return whether API calls should be accepted."""
        with self._lock:
            return self._api_enabled

    @property
    def v2_auto_authorize(self) -> bool:
        """Return whether v2 user creation auto-authorizes."""
        with self._lock:
            return self._v2_auto_authorize

    def set_api_enabled(self, enabled: bool) -> None:
        """Enable or disable API access."""
        with self._lock:
            self._api_enabled = enabled

    def set_v2_auto_authorize(self, enabled: bool) -> None:
        """Enable or disable automatic v2 token authorization."""
        with self._lock:
            self._v2_auto_authorize = enabled

    def start(self) -> None:
        """Start 1 Hz ticker thread."""
        if self._ticker_thread is not None:
            return

        # Support clean restart on the same simulation instance.
        self._stop_event.clear()
        self._last_tick_monotonic = time.monotonic()

        self._ticker_thread = threading.Thread(
            target=self._run_ticker,
            name="dummy-p1-ticker",
            daemon=True,
        )
        self._ticker_thread.start()

    def stop(self) -> None:
        """Stop ticker thread."""
        self._stop_event.set()
        if self._ticker_thread is not None:
            self._ticker_thread.join(timeout=2.0)
            self._ticker_thread = None

    def _run_ticker(self) -> None:
        """Update simulation at a steady 1 second cadence."""
        next_tick = time.monotonic()
        while not self._stop_event.is_set():
            now_monotonic = time.monotonic()
            if now_monotonic >= next_tick:
                with self._lock:
                    dt_s = clamp(now_monotonic - self._last_tick_monotonic, 0.2, 5.0)
                    self._last_tick_monotonic = now_monotonic
                    self._tick_locked(now=datetime.now(self._tz), dt_s=dt_s)
                next_tick += 1.0

            sleep_for = max(0.0, next_tick - time.monotonic())
            self._stop_event.wait(timeout=sleep_for)

    def _tick_locked(self, *, now: datetime, dt_s: float) -> None:
        """Advance simulation state by dt_s seconds.

        Caller must hold self._lock.
        """
        hour = now.hour + now.minute / 60 + now.second / 3600
        day_of_year = now.timetuple().tm_yday
        weekend = now.weekday() >= 5

        seasonal_solar = self._seasonal_solar_factor(day_of_year)
        load_w = self._simulate_household_load_w(
            now=now,
            hour=hour,
            weekend=weekend,
            dt_s=dt_s,
        )
        solar_w = self._simulate_solar_generation_w(
            hour=hour,
            day_of_year=day_of_year,
            seasonal_solar=seasonal_solar,
            dt_s=dt_s,
        )

        net_power_w = load_w - solar_w
        self._last_load_w = load_w
        self._last_solar_w = solar_w

        tariff = 2 if (hour < 7.0 or hour >= 23.0) else 1
        delta_kwh = abs(net_power_w) * dt_s / 3_600_000

        if net_power_w >= 0:
            if tariff == 1:
                self._energy_import_t1_kwh += delta_kwh
            else:
                self._energy_import_t2_kwh += delta_kwh
        else:
            if tariff == 1:
                self._energy_export_t1_kwh += delta_kwh
            else:
                self._energy_export_t2_kwh += delta_kwh

        if (now.year, now.month) != self._month_marker:
            self._month_marker = (now.year, now.month)
            self._monthly_peak_w = max(0.0, net_power_w)
        else:
            self._monthly_peak_w = max(self._monthly_peak_w, max(0.0, net_power_w))

        self._power_window.append((time.monotonic(), net_power_w))
        while self._power_window and self._power_window[0][0] < time.monotonic() - 900:
            self._power_window.popleft()

        average_15m_w = (
            sum(power for _, power in self._power_window) / len(self._power_window)
            if self._power_window
            else net_power_w
        )

        self._wifi_rssi_db = int(
            clamp(
                self._wifi_rssi_db + self._rng.gauss(0.0, 0.9),
                -72,
                -46,
            )
        )
        wifi_strength = rssi_to_strength(self._wifi_rssi_db)

        if self._rng.random() < 0.0006 * dt_s:
            self._sag_counts[self._rng.randrange(0, 3)] += 1
        if self._rng.random() < 0.0004 * dt_s:
            self._swell_counts[self._rng.randrange(0, 3)] += 1

        phase_split = [
            0.34 + self._rng.gauss(0.0, 0.015),
            0.33 + self._rng.gauss(0.0, 0.015),
            0.33 + self._rng.gauss(0.0, 0.015),
        ]
        total_split = max(0.001, sum(phase_split))
        p_l1 = net_power_w * phase_split[0] / total_split
        p_l2 = net_power_w * phase_split[1] / total_split
        p_l3 = net_power_w - p_l1 - p_l2

        v_l1 = clamp(230 + self._rng.gauss(0.0, 1.2), 223, 238)
        v_l2 = clamp(230 + self._rng.gauss(0.0, 1.2), 223, 238)
        v_l3 = clamp(230 + self._rng.gauss(0.0, 1.2), 223, 238)
        v_avg = (v_l1 + v_l2 + v_l3) / 3

        i_l1 = abs(p_l1) / v_l1
        i_l2 = abs(p_l2) / v_l2
        i_l3 = abs(p_l3) / v_l3
        i_avg = abs(net_power_w) / v_avg if v_avg > 0 else 0.0

        power_factor = clamp(0.94 + self._rng.gauss(0.0, 0.02), 0.82, 0.99)
        apparent_power_va = abs(net_power_w) / power_factor
        apparent_l1_va = abs(p_l1) / power_factor
        apparent_l2_va = abs(p_l2) / power_factor
        apparent_l3_va = abs(p_l3) / power_factor

        reactive_power_var = math.sqrt(max(apparent_power_va**2 - abs(net_power_w) ** 2, 0.0))
        reactive_l1_var = math.sqrt(max(apparent_l1_va**2 - abs(p_l1) ** 2, 0.0))
        reactive_l2_var = math.sqrt(max(apparent_l2_va**2 - abs(p_l2) ** 2, 0.0))
        reactive_l3_var = math.sqrt(max(apparent_l3_va**2 - abs(p_l3) ** 2, 0.0))

        total_import_kwh = self._energy_import_t1_kwh + self._energy_import_t2_kwh
        total_export_kwh = self._energy_export_t1_kwh + self._energy_export_t2_kwh

        self._measurement_v1_payload = {
            "wifi_ssid": self._wifi_ssid,
            "wifi_strength": wifi_strength,
            "smr_version": 50,
            "meter_model": "ISKRA AM550",
            "unique_id": self._serial,
            "active_tariff": tariff,
            "total_power_import_kwh": round(total_import_kwh, 6),
            "total_power_import_t1_kwh": round(self._energy_import_t1_kwh, 6),
            "total_power_import_t2_kwh": round(self._energy_import_t2_kwh, 6),
            "total_power_export_kwh": round(total_export_kwh, 6),
            "total_power_export_t1_kwh": round(self._energy_export_t1_kwh, 6),
            "total_power_export_t2_kwh": round(self._energy_export_t2_kwh, 6),
            "active_power_w": round(net_power_w, 1),
            "active_power_l1_w": round(p_l1, 1),
            "active_power_l2_w": round(p_l2, 1),
            "active_power_l3_w": round(p_l3, 1),
            "active_voltage_v": round(v_avg, 1),
            "active_voltage_l1_v": round(v_l1, 1),
            "active_voltage_l2_v": round(v_l2, 1),
            "active_voltage_l3_v": round(v_l3, 1),
            "active_current_a": round(i_avg, 3),
            "active_current_l1_a": round(i_l1, 3),
            "active_current_l2_a": round(i_l2, 3),
            "active_current_l3_a": round(i_l3, 3),
            "active_frequency_hz": round(50 + self._rng.gauss(0.0, 0.02), 3),
            "active_apparent_power_va": round(apparent_power_va, 1),
            "active_apparent_power_l1_va": round(apparent_l1_va, 1),
            "active_apparent_power_l2_va": round(apparent_l2_va, 1),
            "active_apparent_power_l3_va": round(apparent_l3_va, 1),
            "active_reactive_power_var": round(reactive_power_var, 1),
            "active_reactive_power_l1_var": round(reactive_l1_var, 1),
            "active_reactive_power_l2_var": round(reactive_l2_var, 1),
            "active_reactive_power_l3_var": round(reactive_l3_var, 1),
            "active_power_factor": round(power_factor, 3),
            "active_power_factor_l1": round(power_factor, 3),
            "active_power_factor_l2": round(power_factor, 3),
            "active_power_factor_l3": round(power_factor, 3),
            "active_power_average_w": round(average_15m_w, 1),
            "montly_power_peak_w": round(self._monthly_peak_w, 1),
            "monthly_power_peak_w": round(self._monthly_peak_w, 1),
            "voltage_sag_l1_count": self._sag_counts[0],
            "voltage_sag_l2_count": self._sag_counts[1],
            "voltage_sag_l3_count": self._sag_counts[2],
            "voltage_swell_l1_count": self._swell_counts[0],
            "voltage_swell_l2_count": self._swell_counts[1],
            "voltage_swell_l3_count": self._swell_counts[2],
            "any_power_fail_count": self._any_power_fail_count,
            "long_power_fail_count": self._long_power_fail_count,
        }

        self._system_payload = {
            "wifi_ssid": self._wifi_ssid,
            "wifi_rssi_db": self._wifi_rssi_db,
            "cloud_enabled": self._cloud_enabled,
            "uptime_s": int(time.monotonic() - self._start_monotonic),
            "status_led_brightness_pct": self._status_led_brightness_pct,
            "api_v1_enabled": self._api_enabled,
        }
        self._last_sample_dt = now

    def _interpolate_hourly_load_factor(self, *, hour: float, weekend: bool) -> float:
        """Return linearly interpolated hourly factor for smooth transitions."""
        factors = (
            self._WEEKEND_HOURLY_LOAD_FACTORS
            if weekend
            else self._WEEKDAY_HOURLY_LOAD_FACTORS
        )
        hour_index = int(hour) % 24
        next_index = (hour_index + 1) % 24
        fraction = clamp(hour - float(hour_index), 0.0, 1.0)
        return (factors[hour_index] * (1.0 - fraction)) + (factors[next_index] * fraction)

    def _interpolate_monthly_load_factor(self, *, now: datetime, hour: float) -> float:
        """Return month factor with in-month interpolation (day-of-year aware)."""
        month_index = now.month - 1
        next_month_index = (month_index + 1) % 12
        days_in_month = max(1, calendar.monthrange(now.year, now.month)[1])
        month_progress = ((now.day - 1) + (hour / 24.0)) / float(days_in_month)
        month_progress = clamp(month_progress, 0.0, 1.0)
        current = self._MONTHLY_LOAD_FACTORS[month_index]
        nxt = self._MONTHLY_LOAD_FACTORS[next_month_index]
        return (current * (1.0 - month_progress)) + (nxt * month_progress)

    def _seasonal_solar_factor(self, day_of_year: int) -> float:
        """Return seasonality factor with summer high and winter low output."""
        return clamp(math.sin((2 * math.pi * (day_of_year - 80)) / 365), 0.12, 1.0)

    def _simulate_household_load_w(
        self,
        *,
        now: datetime,
        hour: float,
        weekend: bool,
        dt_s: float,
    ) -> float:
        """Simulate import demand using Belgian-style hourly and seasonal factors."""
        hourly_factor = self._interpolate_hourly_load_factor(hour=hour, weekend=weekend)
        seasonal_factor = self._interpolate_monthly_load_factor(now=now, hour=hour)
        profile_factor = (
            (hourly_factor * seasonal_factor) / self._load_profile_normalization
        )

        self._last_hourly_load_factor = hourly_factor
        self._last_seasonal_load_factor = seasonal_factor
        self._last_combined_load_factor = profile_factor

        base_load = self._average_household_power_w * profile_factor
        occupancy_tweak = (
            45.0 * gaussian(hour, 6.9, 1.3)
            + 60.0 * gaussian(hour, 19.4, 2.1)
            - 35.0 * gaussian(hour, 13.5, 2.6)
            - 20.0 * gaussian(hour, 2.5, 2.2)
        )
        weekend_tweak = (
            25.0 * gaussian(hour, 11.0, 3.2) - 10.0 * gaussian(hour, 7.0, 1.4)
            if weekend
            else 0.0
        )

        activity = clamp(
            0.14 + 0.30 * profile_factor + 0.16 * gaussian(hour, 19.2, 2.0),
            0.04,
            1.30,
        )
        spike_probability = (0.004 + 0.018 * activity) * dt_s
        if self._rng.random() < spike_probability:
            profile = self._rng.choices(
                [
                    (1300, 120),
                    (2000, 55),
                    (950, 450),
                    (420, 1500),
                    (3000, 10),
                ],
                weights=[26, 21, 28, 15, 10],
                k=1,
            )[0]
            watts, duration_s = profile
            duration_s += self._rng.uniform(-0.15 * duration_s, 0.15 * duration_s)
            self._spikes.append(ApplianceSpike(watts=watts, remaining_s=max(4.0, duration_s)))

        spike_load_w = 0.0
        retained_spikes: list[ApplianceSpike] = []
        for spike in self._spikes:
            spike.remaining_s -= dt_s
            if spike.remaining_s > 0:
                retained_spikes.append(spike)
                spike_load_w += spike.watts
        self._spikes = retained_spikes

        random_noise = self._rng.gauss(0.0, 16.0)
        load_w = (
            base_load
            + occupancy_tweak
            + weekend_tweak
            + spike_load_w
            + random_noise
        )
        return max(110.0, load_w)

    def _simulate_solar_generation_w(
        self,
        *,
        hour: float,
        day_of_year: int,
        seasonal_solar: float,
        dt_s: float,
    ) -> float:
        """Simulate PV output with daylight envelope, seasons, and cloud dynamics."""
        gamma = 2.0 * math.pi * (day_of_year - 1) / 365.0
        declination = (
            0.006918
            - 0.399912 * math.cos(gamma)
            + 0.070257 * math.sin(gamma)
            - 0.006758 * math.cos(2 * gamma)
            + 0.000907 * math.sin(2 * gamma)
            - 0.002697 * math.cos(3 * gamma)
            + 0.00148 * math.sin(3 * gamma)
        )

        cos_omega0 = -math.tan(self._latitude_rad) * math.tan(declination)
        cos_omega0 = clamp(cos_omega0, -1.0, 1.0)
        daylight_hours = 2.0 * math.degrees(math.acos(cos_omega0)) / 15.0

        sunrise = 12.0 - daylight_hours / 2.0
        sunset = 12.0 + daylight_hours / 2.0

        if hour <= sunrise or hour >= sunset:
            return 0.0

        day_fraction = (hour - sunrise) / max(0.01, sunset - sunrise)
        sun_envelope = math.sin(math.pi * day_fraction) ** 1.35

        cloud_target = 0.92 - 0.22 * (1.0 - seasonal_solar)
        self._cloud_factor += (cloud_target - self._cloud_factor) * min(1.0, 0.05 * dt_s)
        self._cloud_factor += self._rng.gauss(0.0, 0.018) * math.sqrt(max(dt_s, 0.1))

        if self._rng.random() < 0.0035 * dt_s:
            self._cloud_factor *= self._rng.uniform(0.35, 0.75)
        if self._rng.random() < 0.0015 * dt_s and seasonal_solar > 0.55:
            self._cloud_factor *= self._rng.uniform(1.03, 1.17)

        self._cloud_factor = clamp(self._cloud_factor, 0.22, 1.2)

        clear_sky_pv_w = self._pv_peak_w * seasonal_solar * sun_envelope
        return max(0.0, clear_sky_pv_w * self._cloud_factor)

    def issue_token(self, name: str) -> str:
        """Issue or return a stable token for a local user name."""
        with self._lock:
            return self._tokens.issue(name)

    def is_valid_token(self, token: str | None) -> bool:
        """Return whether token is currently authorized."""
        with self._lock:
            return self._tokens.is_valid(token)

    def revoke_token(self, token: str | None = None, name: str | None = None) -> None:
        """Revoke one token by token or name, or all tokens when both are None."""
        with self._lock:
            self._tokens.revoke(token=token, name=name)

    def update_system_settings(self, payload: dict[str, Any]) -> None:
        """Apply partial system config updates."""
        with self._lock:
            cloud_enabled = payload.get("cloud_enabled")
            if isinstance(cloud_enabled, bool):
                self._cloud_enabled = cloud_enabled

            api_v1_enabled = payload.get("api_v1_enabled")
            if isinstance(api_v1_enabled, bool):
                self._api_enabled = api_v1_enabled

            brightness = payload.get("status_led_brightness_pct")
            if isinstance(brightness, int):
                self._status_led_brightness_pct = int(clamp(brightness, 0, 100))

    def get_device_v2_payload(self) -> dict[str, Any]:
        """Return v2 /api payload."""
        return {
            "product_type": "HWE-P1",
            "product_name": "P1 Meter",
            "serial": self._serial,
            "firmware_version": "6.0.0-sim",
            "api_version": "2.0.0",
        }

    def get_measurement_v1_payload(self) -> dict[str, Any]:
        """Return latest /api/v1/data payload."""
        with self._lock:
            return dict(self._measurement_v1_payload)

    def get_measurement_v2_payload(self) -> dict[str, Any]:
        """Return latest /api/measurement payload."""
        with self._lock:
            m = dict(self._measurement_v1_payload)
            now = self._last_sample_dt

        return {
            "protocol_version": m.get("smr_version"),
            "meter_model": m.get("meter_model"),
            "unique_id": m.get("unique_id"),
            "tariff": m.get("active_tariff"),
            "energy_import_kwh": m.get("total_power_import_kwh"),
            "energy_import_t1_kwh": m.get("total_power_import_t1_kwh"),
            "energy_import_t2_kwh": m.get("total_power_import_t2_kwh"),
            "energy_export_kwh": m.get("total_power_export_kwh"),
            "energy_export_t1_kwh": m.get("total_power_export_t1_kwh"),
            "energy_export_t2_kwh": m.get("total_power_export_t2_kwh"),
            "power_w": m.get("active_power_w"),
            "power_l1_w": m.get("active_power_l1_w"),
            "power_l2_w": m.get("active_power_l2_w"),
            "power_l3_w": m.get("active_power_l3_w"),
            "voltage_v": m.get("active_voltage_v"),
            "voltage_l1_v": m.get("active_voltage_l1_v"),
            "voltage_l2_v": m.get("active_voltage_l2_v"),
            "voltage_l3_v": m.get("active_voltage_l3_v"),
            "current_a": m.get("active_current_a"),
            "current_l1_a": m.get("active_current_l1_a"),
            "current_l2_a": m.get("active_current_l2_a"),
            "current_l3_a": m.get("active_current_l3_a"),
            "frequency_hz": m.get("active_frequency_hz"),
            "apparent_power_va": m.get("active_apparent_power_va"),
            "apparent_power_l1_va": m.get("active_apparent_power_l1_va"),
            "apparent_power_l2_va": m.get("active_apparent_power_l2_va"),
            "apparent_power_l3_va": m.get("active_apparent_power_l3_va"),
            "reactive_power_var": m.get("active_reactive_power_var"),
            "reactive_power_l1_var": m.get("active_reactive_power_l1_var"),
            "reactive_power_l2_var": m.get("active_reactive_power_l2_var"),
            "reactive_power_l3_var": m.get("active_reactive_power_l3_var"),
            "power_factor": m.get("active_power_factor"),
            "power_factor_l1": m.get("active_power_factor_l1"),
            "power_factor_l2": m.get("active_power_factor_l2"),
            "power_factor_l3": m.get("active_power_factor_l3"),
            "voltage_sag_l1_count": m.get("voltage_sag_l1_count"),
            "voltage_sag_l2_count": m.get("voltage_sag_l2_count"),
            "voltage_sag_l3_count": m.get("voltage_sag_l3_count"),
            "voltage_swell_l1_count": m.get("voltage_swell_l1_count"),
            "voltage_swell_l2_count": m.get("voltage_swell_l2_count"),
            "voltage_swell_l3_count": m.get("voltage_swell_l3_count"),
            "any_power_fail_count": m.get("any_power_fail_count"),
            "long_power_fail_count": m.get("long_power_fail_count"),
            "average_power_15m_w": m.get("active_power_average_w"),
            "monthly_power_peak_w": m.get("monthly_power_peak_w"),
            "timestamp": now.isoformat(),
        }

    def get_system_payload(self) -> dict[str, Any]:
        """Return latest /api/v1/system and /api/system payload."""
        with self._lock:
            return dict(self._system_payload)

    def get_telegram_payload(self) -> str:
        """Return best-effort DSMR-like telegram text."""
        with self._lock:
            measurement = dict(self._measurement_v1_payload)
            now = self._last_sample_dt

        dst_flag = "S" if bool(now.dst()) else "W"
        timestamp = now.strftime(f"%y%m%d%H%M%S{dst_flag}")

        power_import_kw = max(0.0, float(measurement["active_power_w"])) / 1000.0
        power_export_kw = max(0.0, -float(measurement["active_power_w"])) / 1000.0

        return "\n".join(
            [
                "/XMX5LGBBFFB231234567",
                "",
                f"0-0:1.0.0({timestamp})",
                f"1-0:1.8.1({measurement['total_power_import_t1_kwh']:010.3f}*kWh)",
                f"1-0:1.8.2({measurement['total_power_import_t2_kwh']:010.3f}*kWh)",
                f"1-0:2.8.1({measurement['total_power_export_t1_kwh']:010.3f}*kWh)",
                f"1-0:2.8.2({measurement['total_power_export_t2_kwh']:010.3f}*kWh)",
                f"1-0:1.7.0({power_import_kw:06.3f}*kW)",
                f"1-0:2.7.0({power_export_kw:06.3f}*kW)",
                "!7C2F",
                "",
            ]
        )

    def get_debug_state(self) -> dict[str, Any]:
        """Return summary for optional simulator debug endpoint."""
        with self._lock:
            return {
                "api_enabled": self._api_enabled,
                "v2_auto_authorize": self._v2_auto_authorize,
                "token_count": self._tokens.count,
                "cloud_enabled": self._cloud_enabled,
                "tz": str(self._tz),
                "serial": self._serial,
                "wifi_rssi_db": self._wifi_rssi_db,
                "active_spikes": len(self._spikes),
                "last_sample": self._last_sample_dt.isoformat(),
                "latest_power_w": self._measurement_v1_payload.get("active_power_w"),
                "latest_load_w": round(self._last_load_w, 1),
                "latest_solar_w": round(self._last_solar_w, 1),
                "household_yearly_kwh": round(self._household_yearly_kwh, 1),
                "hourly_load_factor": round(self._last_hourly_load_factor, 3),
                "seasonal_load_factor": round(self._last_seasonal_load_factor, 3),
                "combined_load_factor": round(self._last_combined_load_factor, 3),
            }
