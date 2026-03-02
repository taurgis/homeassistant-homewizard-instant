"""Defines HomeWizard sensor descriptions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

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
    HomeWizardSensorEntityDescription(
        key="total_power_import_t1_kwh",
        translation_key="total_energy_import_tariff_kwh",
        translation_placeholders={"tariff": "1"},
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        has_fn=lambda data: (
            # SKT/SDM230/630 provides both total and tariff 1: duplicate.
            data.measurement.energy_import_t1_kwh is not None
            and data.measurement.energy_import_t2_kwh is not None
        ),
        value_fn=lambda data: data.measurement.energy_import_t1_kwh,
    ),
    HomeWizardSensorEntityDescription(
        key="total_power_import_t2_kwh",
        translation_key="total_energy_import_tariff_kwh",
        translation_placeholders={"tariff": "2"},
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        has_fn=lambda data: data.measurement.energy_import_t2_kwh is not None,
        value_fn=lambda data: data.measurement.energy_import_t2_kwh,
    ),
    HomeWizardSensorEntityDescription(
        key="total_power_import_t3_kwh",
        translation_key="total_energy_import_tariff_kwh",
        translation_placeholders={"tariff": "3"},
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        has_fn=lambda data: data.measurement.energy_import_t3_kwh is not None,
        value_fn=lambda data: data.measurement.energy_import_t3_kwh,
    ),
    HomeWizardSensorEntityDescription(
        key="total_power_import_t4_kwh",
        translation_key="total_energy_import_tariff_kwh",
        translation_placeholders={"tariff": "4"},
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        has_fn=lambda data: data.measurement.energy_import_t4_kwh is not None,
        value_fn=lambda data: data.measurement.energy_import_t4_kwh,
    ),
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
    HomeWizardSensorEntityDescription(
        key="total_power_export_t1_kwh",
        translation_key="total_energy_export_tariff_kwh",
        translation_placeholders={"tariff": "1"},
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        has_fn=lambda data: (
            # SKT/SDM230/630 provides both total and tariff 1: duplicate.
            data.measurement.energy_export_t1_kwh is not None
            and data.measurement.energy_export_t2_kwh is not None
        ),
        enabled_fn=lambda data: data.measurement.energy_export_t1_kwh != 0,
        value_fn=lambda data: data.measurement.energy_export_t1_kwh,
    ),
    HomeWizardSensorEntityDescription(
        key="total_power_export_t2_kwh",
        translation_key="total_energy_export_tariff_kwh",
        translation_placeholders={"tariff": "2"},
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        has_fn=lambda data: data.measurement.energy_export_t2_kwh is not None,
        enabled_fn=lambda data: data.measurement.energy_export_t2_kwh != 0,
        value_fn=lambda data: data.measurement.energy_export_t2_kwh,
    ),
    HomeWizardSensorEntityDescription(
        key="total_power_export_t3_kwh",
        translation_key="total_energy_export_tariff_kwh",
        translation_placeholders={"tariff": "3"},
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        has_fn=lambda data: data.measurement.energy_export_t3_kwh is not None,
        enabled_fn=lambda data: data.measurement.energy_export_t3_kwh != 0,
        value_fn=lambda data: data.measurement.energy_export_t3_kwh,
    ),
    HomeWizardSensorEntityDescription(
        key="total_power_export_t4_kwh",
        translation_key="total_energy_export_tariff_kwh",
        translation_placeholders={"tariff": "4"},
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        has_fn=lambda data: data.measurement.energy_export_t4_kwh is not None,
        enabled_fn=lambda data: data.measurement.energy_export_t4_kwh != 0,
        value_fn=lambda data: data.measurement.energy_export_t4_kwh,
    ),
    HomeWizardSensorEntityDescription(
        key="active_power_w",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        has_fn=lambda data: data.measurement.power_w is not None,
        value_fn=lambda data: data.measurement.power_w,
    ),
    HomeWizardSensorEntityDescription(
        key="active_power_l1_w",
        translation_key="active_power_phase_w",
        translation_placeholders={"phase": "1"},
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        has_fn=lambda data: data.measurement.power_l1_w is not None,
        value_fn=lambda data: data.measurement.power_l1_w,
    ),
    HomeWizardSensorEntityDescription(
        key="active_power_l2_w",
        translation_key="active_power_phase_w",
        translation_placeholders={"phase": "2"},
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        has_fn=lambda data: data.measurement.power_l2_w is not None,
        value_fn=lambda data: data.measurement.power_l2_w,
    ),
    HomeWizardSensorEntityDescription(
        key="active_power_l3_w",
        translation_key="active_power_phase_w",
        translation_placeholders={"phase": "3"},
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        has_fn=lambda data: data.measurement.power_l3_w is not None,
        value_fn=lambda data: data.measurement.power_l3_w,
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
    HomeWizardSensorEntityDescription(
        key="active_voltage_l1_v",
        translation_key="active_voltage_phase_v",
        translation_placeholders={"phase": "1"},
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        has_fn=lambda data: data.measurement.voltage_l1_v is not None,
        value_fn=lambda data: data.measurement.voltage_l1_v,
    ),
    HomeWizardSensorEntityDescription(
        key="active_voltage_l2_v",
        translation_key="active_voltage_phase_v",
        translation_placeholders={"phase": "2"},
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        has_fn=lambda data: data.measurement.voltage_l2_v is not None,
        value_fn=lambda data: data.measurement.voltage_l2_v,
    ),
    HomeWizardSensorEntityDescription(
        key="active_voltage_l3_v",
        translation_key="active_voltage_phase_v",
        translation_placeholders={"phase": "3"},
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        has_fn=lambda data: data.measurement.voltage_l3_v is not None,
        value_fn=lambda data: data.measurement.voltage_l3_v,
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
    HomeWizardSensorEntityDescription(
        key="active_current_l1_a",
        translation_key="active_current_phase_a",
        translation_placeholders={"phase": "1"},
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        has_fn=lambda data: data.measurement.current_l1_a is not None,
        value_fn=lambda data: data.measurement.current_l1_a,
    ),
    HomeWizardSensorEntityDescription(
        key="active_current_l2_a",
        translation_key="active_current_phase_a",
        translation_placeholders={"phase": "2"},
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        has_fn=lambda data: data.measurement.current_l2_a is not None,
        value_fn=lambda data: data.measurement.current_l2_a,
    ),
    HomeWizardSensorEntityDescription(
        key="active_current_l3_a",
        translation_key="active_current_phase_a",
        translation_placeholders={"phase": "3"},
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        has_fn=lambda data: data.measurement.current_l3_a is not None,
        value_fn=lambda data: data.measurement.current_l3_a,
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
    HomeWizardSensorEntityDescription(
        key="active_apparent_power_l1_va",
        translation_key="active_apparent_power_phase_va",
        translation_placeholders={"phase": "1"},
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        device_class=SensorDeviceClass.APPARENT_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        has_fn=lambda data: data.measurement.apparent_power_l1_va is not None,
        value_fn=lambda data: data.measurement.apparent_power_l1_va,
    ),
    HomeWizardSensorEntityDescription(
        key="active_apparent_power_l2_va",
        translation_key="active_apparent_power_phase_va",
        translation_placeholders={"phase": "2"},
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        device_class=SensorDeviceClass.APPARENT_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        has_fn=lambda data: data.measurement.apparent_power_l2_va is not None,
        value_fn=lambda data: data.measurement.apparent_power_l2_va,
    ),
    HomeWizardSensorEntityDescription(
        key="active_apparent_power_l3_va",
        translation_key="active_apparent_power_phase_va",
        translation_placeholders={"phase": "3"},
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        device_class=SensorDeviceClass.APPARENT_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        has_fn=lambda data: data.measurement.apparent_power_l3_va is not None,
        value_fn=lambda data: data.measurement.apparent_power_l3_va,
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
    HomeWizardSensorEntityDescription(
        key="active_reactive_power_l1_var",
        translation_key="active_reactive_power_phase_var",
        translation_placeholders={"phase": "1"},
        native_unit_of_measurement=UnitOfReactivePower.VOLT_AMPERE_REACTIVE,
        device_class=SensorDeviceClass.REACTIVE_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        has_fn=lambda data: data.measurement.reactive_power_l1_var is not None,
        value_fn=lambda data: data.measurement.reactive_power_l1_var,
    ),
    HomeWizardSensorEntityDescription(
        key="active_reactive_power_l2_var",
        translation_key="active_reactive_power_phase_var",
        translation_placeholders={"phase": "2"},
        native_unit_of_measurement=UnitOfReactivePower.VOLT_AMPERE_REACTIVE,
        device_class=SensorDeviceClass.REACTIVE_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        has_fn=lambda data: data.measurement.reactive_power_l2_var is not None,
        value_fn=lambda data: data.measurement.reactive_power_l2_var,
    ),
    HomeWizardSensorEntityDescription(
        key="active_reactive_power_l3_var",
        translation_key="active_reactive_power_phase_var",
        translation_placeholders={"phase": "3"},
        native_unit_of_measurement=UnitOfReactivePower.VOLT_AMPERE_REACTIVE,
        device_class=SensorDeviceClass.REACTIVE_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        has_fn=lambda data: data.measurement.reactive_power_l3_var is not None,
        value_fn=lambda data: data.measurement.reactive_power_l3_var,
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
    HomeWizardSensorEntityDescription(
        key="active_power_factor_l1",
        translation_key="active_power_factor_phase",
        translation_placeholders={"phase": "1"},
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.POWER_FACTOR,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        has_fn=lambda data: data.measurement.power_factor_l1 is not None,
        value_fn=lambda data: to_percentage(data.measurement.power_factor_l1),
    ),
    HomeWizardSensorEntityDescription(
        key="active_power_factor_l2",
        translation_key="active_power_factor_phase",
        translation_placeholders={"phase": "2"},
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.POWER_FACTOR,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        has_fn=lambda data: data.measurement.power_factor_l2 is not None,
        value_fn=lambda data: to_percentage(data.measurement.power_factor_l2),
    ),
    HomeWizardSensorEntityDescription(
        key="active_power_factor_l3",
        translation_key="active_power_factor_phase",
        translation_placeholders={"phase": "3"},
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.POWER_FACTOR,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        has_fn=lambda data: data.measurement.power_factor_l3 is not None,
        value_fn=lambda data: to_percentage(data.measurement.power_factor_l3),
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
