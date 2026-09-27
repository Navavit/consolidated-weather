# Paper outline (working draft)

**Working title:** *Do global weather models capture Thai cities? Multi-model verification of daily rainfall and the nocturnal urban heat island against urban stations, dense rain gauges and citizen reports*

**Target journals:** Meteorological Applications (RMetS) · Urban Climate · Atmosphere (MDPI)

> Numbers marked ⟨…⟩ come from `analysis/` and are filled in once the retrospective run (2024-03 → 2025-08) completes.
> Preliminary numbers below are from 2024-03 → 2024-10 only.

---

## 1. Introduction
- Most people in Thailand live in, and make daily decisions in, urban areas (commuting, flash flooding, heat stress).
- Global NWP (and new AI models) are run at 9–28 km with little or no urban representation; station-based verification in tropical Southeast Asia is sparse, and urban vs rural skill is rarely separated.
- Research questions
  - **RQ1** Is daily rainfall forecast skill different at urban-centre stations than at rural stations?
  - **RQ2** Do models reproduce the observed nocturnal urban heat island (UHI)? Is the urban–rural Tmin contrast underestimated?
  - **RQ3** Does km-scale guidance (TMD WRF 2 km, Google Weather ~5 km) improve urban rainfall forecasts over global models? *(prospective, dense Bangkok gauges)*
  - **RQ4** Can citizen rain/no-rain reports serve as verification data in cities? *(prospective)*

## 2. Data
### 2.1 Observations
- **NOAA GSOD** 2024-01 → 2025-08, ⟨120⟩ Thai WMO stations; GSOD day = 00–24 UTC = 07–07 local (matches Thai climatological day).
  - Precipitation QC: keep flag `G` (24-h complete); treat flag `I` with 0.00 as dry. Excluding `I` biases the sample to wet days (mean 9.3 vs 5.0 mm d⁻¹) — sensitivity reported.
  - Station coordinates from GSOD; 29 stations differ > 2 km from TMD API metadata (10 by > 10 km) → forecasts re-extracted at GSOD coordinates.
- **Climatology** GSOD 2000–2023 (⟨834,943⟩ station-days): monthly rain means, ±7-day smoothed day-of-year Tmax/Tmin.
- **Prospective** (Sep 2026 →): TMD 3-hourly synoptic obs, ThaiWater telemetry gauges (20 inner-Bangkok gauges ≥ 2 km apart; Ayutthaya), citizen reports.

### 2.2 Urbanisation
- WorldPop 2020 1-km population density, 3×3-km mean around GSOD coordinates, Degree-of-Urbanisation thresholds:
  urban centre ≥ 1,500 km⁻² (n = ⟨25⟩), urban cluster 300–1,500 (⟨52⟩), rural < 300 (⟨36⟩), airports separate (⟨6⟩).
- Robustness: Local Climate Zones map (⟨todo⟩).

### 2.3 Forecasts
- Open-Meteo Previous Runs API (issued 1, 3, 7 days before the valid day), UTC days.
- Models: ECMWF IFS 0.25°, ECMWF AIFS (AI, from 2025-02), NOAA GFS, DWD ICON, ECCC GEM, JMA GSM, CMA GRAPES, MF ARPEGE, UKMO UM.
- Excluded after QC: CMA GRAPES temperature (≈ −6 °C systematic offset), NOAA GFS D+7 rainfall (anomalous dry, FBI 0.14).

## 3. Methods
- Common sample: per lead, models with ≥ 90 % coverage; only station-days available for all of them.
- Rainfall: CSI, ETS, POD, FAR, FBI at 1, 10, 35.1 mm d⁻¹ (TMD categories); MAE; MAE skill vs climatology.
- Temperature: MAE, bias, MAE skill vs climatology.
- Urban–rural: difference of means / ETS; **95 % CI by day-block bootstrap** (1,000 resamples; whole days resampled to respect spatial correlation); region-stratified check (within-region differences, weighted).

## 4. Results (preliminary: 2024-03 → 2024-10)
### 4.1 Overall skill
- Rain ≥ 10 mm, D+1 ETS: ECMWF IFS 0.171 [0.153–0.188] best; ICON/ARPEGE/GFS ≈ 0.147–0.149; GEM 0.117.
- ECMWF over-forecasts rain days (FBI 1.47) yet has highest ETS; ICON/ARPEGE under-forecast (FBI 0.84–0.89) with lowest MAE.
- Heavy rain ≥ 35.1 mm: ETS 0.02–0.08 for all models.
- MAE skill vs climatology for rain amount only 0–17 %.
- Tmax: all models too cold by 1.5–3.3 °C at all station types → raw Tmax worse than station climatology.

### 4.2 Urban vs rural (RQ1–RQ2)
- **Observed nocturnal UHI:** Tmin urban centre − rural = **1.47 °C** (within-region: 1.39 °C).
- **Models underestimate it:** modelled contrast 0.76–1.30 °C; bias(urban) − bias(rural) = −0.17 to −0.71 °C, **significant for all six core models**; same sign in all 5 regions for ICON and ECMWF IFS.
- Daytime (Tmax) UHI small (0.49 °C) and not consistently misrepresented.
- Rain ≥ 10 mm ETS: urban − rural differences −0.02 to +0.004, none significant.

### 4.3 AI vs physics (2025-03 → 2025-08) ⟨pending⟩

## 5. Discussion
- Why models miss the nocturnal UHI: grid-box averaging over mixed urban/rural land, simplified or absent urban canopy schemes, 2-m diagnostic assumptions.
- Practical implication: urban heat-risk forecasts (night-time Tmin, heat index) need urban-aware post-processing; a simple station-type bias correction ⟨quantify⟩.
- Rainfall: point verification at 9–28 km cannot resolve urban convective enhancement → motivates RQ3 with dense gauges and km-scale models.
- Limitations: airport siting, GSOD QC, single evaluation period, representativeness of point vs grid.

## 6. Data & code availability
- Code and live system: https://github.com/Navavit/consolidated-weather (analysis in `analysis/`)
- Forecast archive and observations: branch `data` of the same repository
- Licences: Open-Meteo CC BY 4.0; GSOD public domain; WorldPop CC BY 4.0; Google Weather API data subject to Google Maps Platform terms (check before publication).
