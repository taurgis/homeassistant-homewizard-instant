"""Defines HomeWizard sensor descriptions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, cast

from homewizard_energy.models import CombinedModels, ExternalDevice

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS,
    EntityCategory,
    UnitOfApparentPower,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfReactivePower,
)
from homeassistant.helpers.typing import StateType
from homeassistant.util.dt import utcnow
from homeassistant.util.variance import ignore_variance


@dataclass(frozen=True, kw_only=True)
class HomeWizardSensorEntityDescription(SensorEntityDescription):
    """Class describing HomeWizard sensor entities."""

    enabled_fn: Callable[[CombinedModels], bool] = lambda x: True
    has_fn: Callable[[CombinedModels], bool]
    value_fn: Callable[[CombinedModels], StateType | datetime]


@dataclass(frozen=True, kw_only=True)
class HomeWizardExternalSensorEntityDescription(SensorEntityDescription):
    """Class describing HomeWizard sensor entities."""

    suggested_device_class: SensorDeviceClass
    device_name: str


def to_percentage(value: float | None) -> float | None:
    """Convert 0..1 value to percentage when value is not None."""
    return value * 100 if value is not None else None


def uptime_to_datetime(value: int) -> datetime:
    """Convert seconds to datetime timestamp."""
    return utcnow().replace(microsecond=0) - timedelta(seconds=value)


uptime_to_stable_datetime = ignore_variance(uptime_to_datetime, timedelta(minutes=5))


def _phase_counter_sensor(
    key_prefix: str,
    phase: int,
    measurement_attr: str,
) -> HomeWizardSensorEntityDescription:
    """Build a per-phase diagnostic counter sensor description."""
    return HomeWizardSensorEntityDescription(
        key=f"{key_prefix}_l{phase}_count",
        translation_key=f"{key_prefix}_phase_count",
        translation_placeholders={"phase": str(phase)},
        entity_category=EntityCategory.DIAGNOSTIC,
        has_fn=lambda data: getattr(data.measurement, measurement_attr) is not None,
        value_fn=lambda data: getattr(data.measurement, measurement_attr),
    )


def _phase_measurement_sensor(
    *,
    key_template: str,
    translation_key: str,
    measurement_attr_template: str,
    phase: int,
    native_unit_of_measurement: str,
    device_class: SensorDeviceClass,
    state_class: SensorStateClass,
    entity_registry_enabled_default: bool,
    suggested_display_precision: int | None = None,
    value_transform: Callable[[float | None], StateType | datetime] | None = None,
) -> HomeWizardSensorEntityDescription:
    """Build a per-phase sensor using measurement field naming patterns."""
    measurement_attr = measurement_attr_template.format(phase=phase)

    transform_fn: Callable[[float | None], StateType | datetime]
    if value_transform is None:

        def _identity(value: float | None) -> StateType | datetime:
            return value

        transform_fn = _identity
    else:
        transform_fn = value_transform

    def has_value(data: CombinedModels) -> bool:
        return getattr(data.measurement, measurement_attr) is not None

    def phase_value(data: CombinedModels) -> StateType | datetime:
        raw_value = cast(float | None, getattr(data.measurement, measurement_attr))
        return transform_fn(raw_value)

    return HomeWizardSensorEntityDescription(
        key=key_template.format(phase=phase),
        translation_key=translation_key,
        translation_placeholders={"phase": str(phase)},
        native_unit_of_measurement=native_unit_of_measurement,
        device_class=device_class,
        state_class=state_class,
        entity_registry_enabled_default=entity_registry_enabled_default,
        suggested_display_precision=suggested_display_precision,
        has_fn=has_value,
        value_fn=phase_value,
    )


def _phase_measurement_sensors(
    *,
    key_template: str,
    translation_key: str,
    measurement_attr_template: str,
    native_unit_of_measurement: str,
    device_class: SensorDeviceClass,
    state_class: SensorStateClass,
    entity_registry_enabled_default: bool,
    suggested_display_precision: int | None = None,
    value_transform: Callable[[float | None], StateType | datetime] | None = None,
) -> tuple[HomeWizardSensorEntityDescription, ...]:
    """Build phase sensors for L1/L2/L3 with consistent metadata."""
    return tuple(
        _phase_measurement_sensor(
            key_template=key_template,
            translation_key=translation_key,
            measurement_attr_template=measurement_attr_template,
            phase=phase,
            native_unit_of_measurement=native_unit_of_measurement,
            device_class=device_class,
            state_class=state_class,
            entity_registry_enabled_default=entity_registry_enabled_default,
            suggested_display_precision=suggested_display_precision,
            value_transform=value_transform,
        )
        for phase in (1, 2, 3)
    )


def _tariff_energy_sensor(
    *,
    direction: str,
    tariff: int,
    hide_when_zero: bool,
) -> HomeWizardSensorEntityDescription:
    """Build import/export tariff sensors with duplicate handling for tariff 1."""
    measurement_attr = f"energy_{direction}_t{tariff}_kwh"
    next_tariff_attr = f"energy_{direction}_t2_kwh"

    if tariff == 1:

        def has_fn(data: CombinedModels) -> bool:
            return (
                getattr(data.measurement, measurement_attr) is not None
                and getattr(data.measurement, next_tariff_attr) is not None
            )

    else:

        def has_fn(data: CombinedModels) -> bool:
            return getattr(data.measurement, measurement_attr) is not None

    def value_fn(data: CombinedModels) -> StateType | datetime:
        return cast(StateType | datetime, getattr(data.measurement, measurement_attr))

    if hide_when_zero:

        def enabled_fn(data: CombinedModels) -> bool:
            value = cast(float | int | None, getattr(data.measurement, measurement_attr))
            return value != 0

        return HomeWizardSensorEntityDescription(
            key=f"total_power_{direction}_t{tariff}_kwh",
            translation_key=f"total_energy_{direction}_tariff_kwh",
            translation_placeholders={"tariff": str(tariff)},
            native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            device_class=SensorDeviceClass.ENERGY,
            state_class=SensorStateClass.TOTAL_INCREASING,
            enabled_fn=enabled_fn,
            has_fn=has_fn,
            value_fn=value_fn,
        )

    return HomeWizardSensorEntityDescription(
        key=f"total_power_{direction}_t{tariff}_kwh",
        translation_key=f"total_energy_{direction}_tariff_kwh",
        translation_placeholders={"tariff": str(tariff)},
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        has_fn=has_fn,
        value_fn=value_fn,
    )


def _tariff_energy_sensors(
    direction: str,
    *,
    hide_when_zero: bool,
) -> tuple[HomeWizardSensorEntityDescription, ...]:
    """Build tariff 1..4 sensors for import/export energy groups."""
    return tuple(
        _tariff_energy_sensor(
            direction=direction,
            tariff=tariff,
            hide_when_zero=hide_when_zero,
        )
        for tariff in (1, 2, 3, 4)
    )


SENSORS: Final[tuple[HomeWizardSensorEntityDescription, ...]] = (
    HomeWizardSensorEntityDescription(
        key="smr_version",
        translation_key="dsmr_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        has_fn=lambda data: data.measurement.protocol_version is not None,
        value_fn=lambda data: data.measurement.protocol_version,
    ),
    HomeWizardSensorEntityDescription(
        key="meter_model",
        translation_key="meter_model",
        entity_category=EntityCategory.DIAGNOSTIC,
        has_fn=lambda data: data.measurement.meter_model is not None,
        value_fn=lambda data: data.measurement.meter_model,
    ),
    HomeWizardSensorEntityDescription(
        key="unique_meter_id",
        translation_key="unique_meter_id",
        entity_category=EntityCategory.DIAGNOSTIC,
        has_fn=lambda data: data.measurement.unique_id is not None,
        value_fn=lambda data: data.measurement.unique_id,
    ),
    HomeWizardSensorEntityDescription(
        key="wifi_ssid",
        translation_key="wifi_ssid",
        entity_category=EntityCategory.DIAGNOSTIC,
        has_fn=(
            lambda data: data.system is not None and data.system.wifi_ssid is not None
        ),
        value_fn=(
            lambda data: data.system.wifi_ssid if data.system is not None else None
        ),
    ),
    HomeWizardSensorEntityDescription(
        key="active_tariff",
        translation_key="active_tariff",
        has_fn=lambda data: data.measurement.tariff is not None,
        value_fn=(
            lambda data: None
            if data.measurement.tariff is None
            else str(data.measurement.tariff)
        ),
        device_class=SensorDeviceClass.ENUM,
        options=["1", "2", "3", "4"],
    ),
    HomeWizardSensorEntityDescription(
        key="wifi_strength",
        translation_key="wifi_strength",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        has_fn=(
            lambda data: data.system is not None
            and data.system.wifi_strength_pct is not None
        ),
        value_fn=(
            lambda data: data.system.wifi_strength_pct
            if data.system is not None
            else None
        ),
    ),
    HomeWizardSensorEntityDescription(
        key="wifi_rssi",
        translation_key="wifi_rssi",
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        has_fn=(
            lambda data: data.system is not None
            and data.system.wifi_rssi_db is not None
        ),
        value_fn=(
            lambda data: data.system.wifi_rssi_db if data.system is not None else None
        ),
    ),
    HomeWizardSensorEntityDescription(
        key="total_power_import_kwh",
        translation_key="total_energy_import_kwh",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        has_fn=lambda data: data.measurement.energy_import_kwh is not None,
        value_fn=lambda data: data.measurement.energy_import_kwh,
    ),
    *_tariff_energy_sensors("import", hide_when_zero=False),
    HomeWizardSensorEntityDescription(
        key="total_power_export_kwh",
        translation_key="total_energy_export_kwh",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        has_fn=lambda data: data.measurement.energy_export_kwh is not None,
        enabled_fn=lambda data: data.measurement.energy_export_kwh != 0,
        value_fn=lambda data: data.measurement.energy_export_kwh,
    ),
    *_tariff_energy_sensors("export", hide_when_zero=True),
    HomeWizardSensorEntityDescription(
        key="active_power_w",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        has_fn=lambda data: data.measurement.power_w is not None,
        value_fn=lambda data: data.measurement.power_w,
    ),
    *_phase_measurement_sensors(
        key_template="active_power_l{phase}_w",
        translation_key="active_power_phase_w",
        measurement_attr_template="power_l{phase}_w",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=True,
        suggested_display_precision=0,
    ),
    HomeWizardSensorEntityDescription(
        key="active_voltage_v",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        has_fn=lambda data: data.measurement.voltage_v is not None,
        value_fn=lambda data: data.measurement.voltage_v,
    ),
    *_phase_measurement_sensors(
        key_template="active_voltage_l{phase}_v",
        translation_key="active_voltage_phase_v",
        measurement_attr_template="voltage_l{phase}_v",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    HomeWizardSensorEntityDescription(
        key="active_current_a",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        has_fn=lambda data: data.measurement.current_a is not None,
        value_fn=lambda data: data.measurement.current_a,
    ),
    *_phase_measurement_sensors(
        key_template="active_current_l{phase}_a",
        translation_key="active_current_phase_a",
        measurement_attr_template="current_l{phase}_a",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    HomeWizardSensorEntityDescription(
        key="active_frequency_hz",
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        has_fn=lambda data: data.measurement.frequency_hz is not None,
        value_fn=lambda data: data.measurement.frequency_hz,
    ),
    HomeWizardSensorEntityDescription(
        key="active_apparent_power_va",
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        device_class=SensorDeviceClass.APPARENT_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        has_fn=lambda data: data.measurement.apparent_power_va is not None,
        value_fn=lambda data: data.measurement.apparent_power_va,
    ),
    *_phase_measurement_sensors(
        key_template="active_apparent_power_l{phase}_va",
        translation_key="active_apparent_power_phase_va",
        measurement_attr_template="apparent_power_l{phase}_va",
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        device_class=SensorDeviceClass.APPARENT_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    HomeWizardSensorEntityDescription(
        key="active_reactive_power_var",
        native_unit_of_measurement=UnitOfReactivePower.VOLT_AMPERE_REACTIVE,
        device_class=SensorDeviceClass.REACTIVE_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        has_fn=lambda data: data.measurement.reactive_power_var is not None,
        value_fn=lambda data: data.measurement.reactive_power_var,
    ),
    *_phase_measurement_sensors(
        key_template="active_reactive_power_l{phase}_var",
        translation_key="active_reactive_power_phase_var",
        measurement_attr_template="reactive_power_l{phase}_var",
        native_unit_of_measurement=UnitOfReactivePower.VOLT_AMPERE_REACTIVE,
        device_class=SensorDeviceClass.REACTIVE_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    HomeWizardSensorEntityDescription(
        key="active_power_factor",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.POWER_FACTOR,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        has_fn=lambda data: data.measurement.power_factor is not None,
        value_fn=lambda data: to_percentage(data.measurement.power_factor),
    ),
    *_phase_measurement_sensors(
        key_template="active_power_factor_l{phase}",
        translation_key="active_power_factor_phase",
        measurement_attr_template="power_factor_l{phase}",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.POWER_FACTOR,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_transform=to_percentage,
    ),
    _phase_counter_sensor("voltage_sag", 1, "voltage_sag_l1_count"),
    _phase_counter_sensor("voltage_sag", 2, "voltage_sag_l2_count"),
    _phase_counter_sensor("voltage_sag", 3, "voltage_sag_l3_count"),
    _phase_counter_sensor("voltage_swell", 1, "voltage_swell_l1_count"),
    _phase_counter_sensor("voltage_swell", 2, "voltage_swell_l2_count"),
    _phase_counter_sensor("voltage_swell", 3, "voltage_swell_l3_count"),
    HomeWizardSensorEntityDescription(
        key="any_power_fail_count",
        translation_key="any_power_fail_count",
        entity_category=EntityCategory.DIAGNOSTIC,
        has_fn=lambda data: data.measurement.any_power_fail_count is not None,
        value_fn=lambda data: data.measurement.any_power_fail_count,
    ),
    HomeWizardSensorEntityDescription(
        key="long_power_fail_count",
        translation_key="long_power_fail_count",
        entity_category=EntityCategory.DIAGNOSTIC,
        has_fn=lambda data: data.measurement.long_power_fail_count is not None,
        value_fn=lambda data: data.measurement.long_power_fail_count,
    ),
    HomeWizardSensorEntityDescription(
        key="active_power_average_w",
        translation_key="active_power_average_w",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        has_fn=lambda data: data.measurement.average_power_15m_w is not None,
        value_fn=lambda data: data.measurement.average_power_15m_w,
    ),
    HomeWizardSensorEntityDescription(
        key="monthly_power_peak_w",
        translation_key="monthly_power_peak_w",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        has_fn=lambda data: data.measurement.monthly_power_peak_w is not None,
        value_fn=lambda data: data.measurement.monthly_power_peak_w,
    ),
    HomeWizardSensorEntityDescription(
        key="uptime",
        translation_key="uptime",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        has_fn=(
            lambda data: data.system is not None and data.system.uptime_s is not None
        ),
        value_fn=(
            lambda data: (
                uptime_to_stable_datetime(data.system.uptime_s)
                if data.system is not None and data.system.uptime_s is not None
                else None
            )
        ),
    ),
)


EXTERNAL_SENSORS = {
    ExternalDevice.DeviceType.GAS_METER: HomeWizardExternalSensorEntityDescription(
        key="gas_meter",
        translation_key="gas_meter",
        suggested_device_class=SensorDeviceClass.GAS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        device_name="Gas meter",
    ),
    ExternalDevice.DeviceType.HEAT_METER: HomeWizardExternalSensorEntityDescription(
        key="heat_meter",
        translation_key="heat_meter",
        suggested_device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        device_name="Heat meter",
    ),
    ExternalDevice.DeviceType.WARM_WATER_METER: HomeWizardExternalSensorEntityDescription(
        key="warm_water_meter",
        translation_key="warm_water_meter",
        suggested_device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL_INCREASING,
        device_name="Warm water meter",
    ),
    ExternalDevice.DeviceType.WATER_METER: HomeWizardExternalSensorEntityDescription(
        key="water_meter",
        translation_key="water_meter",
        suggested_device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL_INCREASING,
        device_name="Water meter",
    ),
    ExternalDevice.DeviceType.INLET_HEAT_METER: HomeWizardExternalSensorEntityDescription(
        key="inlet_heat_meter",
        translation_key="inlet_heat_meter",
        suggested_device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        device_name="Inlet heat meter",
    ),
}
