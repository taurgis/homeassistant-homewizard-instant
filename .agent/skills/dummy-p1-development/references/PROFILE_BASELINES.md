# Belgian Household Load References for Dummy P1

These references justify the baseline used by the dummy P1 simulator.

## Annual Consumption Anchors

| Source | Value | Note |
|---|---:|---|
| Vlaamse Nutsregulator (Flanders) average household electricity use (2024) | 2,662 kWh/year | Official regulator benchmark |
| Vlaamse Nutsregulator family benchmark category | 3,500 kWh/year | Common planning category |
| Eurostat + Statbel implied Belgium average | about 3,055 kWh/household/year | Derived: 15,884.3 GWh / 5,199,324 households |

## Hourly and Seasonal Shape

Use Belgian system load shape as a proxy when direct national household smart-meter profile data is not available in a simple machine-readable form:

- Elia 2025 hourly ratios: night lows around `0.83`, daytime peaks around `1.12`.
- Weekday profile stronger than weekend profile.
- Monthly normalization suggests winter uplift (`~1.12`) vs summer reduction (`~0.93`).

This proxy should be combined with household-specific random appliance spikes for realistic traces.

## Official / Authoritative Links

- Vlaamse Nutsregulator energy usage page:
  https://www.vlaamsenutsregulator.be/elektriciteit-en-aardgas/energieprijzen-en-facturen/energieverbruik
- Vlaamse Nutsregulator usage profile explanation:
  https://www.vlaamsenutsregulator.be/elektriciteit-en-aardgas/energieprijzen-en-facturen/wat-zijn-gebruiksprofielen
- Synergrid profile files overview:
  https://www.synergrid.be/nl/documentencentrum/statistieken-gegevens/profielen-slp-spp-rlp
- Eurostat household electricity final consumption (Belgium):
  https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/nrg_d_hhq?geo=BE&nrg_bal=FC_OTH_HH_E&siec=E7000&unit=GWH
- Statbel private households:
  https://statbel.fgov.be/en/themes/population/structure-population/households
- Elia open data dataset metadata (`ods001`):
  https://opendata.elia.be/api/explore/v2.1/catalog/datasets/ods001

## Confidence and Limits

- High confidence: annual consumption anchors from regulator and official statistics.
- Medium confidence: hourly and monthly shape from system load proxy.
- Lower confidence: exact appliance-level timing and magnitude for a specific household.
