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
- Fair model comparison: paired differences vs a reference model (ECMWF IFS) on identical station-days; bias-insensitive **SEDI** and **frequency-matched ETS** (model-specific threshold giving the observed event frequency) alongside raw ETS, because ETS rewards over-forecasting and MAE rewards under-forecasting.
- Uncertainty: **two-way bootstrap** resampling days (spatial correlation) *and* stations (few stations per class), 1,000 resamples; Benjamini–Hochberg FDR 5 % across all tests.
- Urban–rural: difference of means/ETS; confounder control by station-level regression of mean bias on urban class + elevation + log distance to coast + region (stations bootstrapped); rural stations are higher (median 119 vs 29 m) and farther from the sea (145 vs 77 km).

## 4. Results (interim: 2024-03 → ~2025-05, fairness-adjusted)
### 4.1 Model ranking — rain ≥ 10 mm (52,033 station-days, main period)
- D+1: ECMWF IFS ETS 0.192, SEDI 0.536; every other model significantly lower in raw ETS and SEDI (FDR 5 %).
- **After frequency matching the gap shrinks:** ICON, ARPEGE and GFS are no longer significantly below IFS (IFS over-forecasts rain days, FBI 1.38–1.51); at D+3 ICON ≈ IFS (+0.005 [−0.013, +0.022]).
- ECCC GEM and CMA GRAPES remain significantly worst under all three scores.
- Heavy rain ≥ 35.1 mm: ETS 0.03–0.09 for all models.

### 4.2 AI vs physics (2025-03 → , ECMWF AIFS available)
- **AIFS beats IFS at all leads**: ETS +0.036 (D+1), +0.059 (D+3), +0.053 (D+7); frequency-matched ETS +0.039/+0.046/+0.050; SEDI significant at D+3 and D+7 (D+1 CI [−0.005, +0.098]).
- AIFS has near-unbiased rain frequency (FBI 1.07–1.19 vs IFS 1.31–1.60), so its advantage is **not** an over-forecasting artefact.
- Caveat: only one (mostly wet-season) period; AIFS vs IFS at 0.25° open-data resolution.

### 4.3 Urban vs rural (RQ1–RQ2), D+1
- Observed nocturnal UHI (Tmin urban centre − rural): **1.94 °C [1.03, 2.86]** raw; **0.87 °C [0.27, 1.33]** after controlling elevation, coast distance and region.
- Model bias(urban) − bias(rural) for Tmin: −0.70 (ICON), −0.95 (GEM), −0.70 (IFS), −0.83 (ARPEGE) °C — **significant after two-way bootstrap + FDR**; GFS (−0.49, p = 0.03) and JMA (−0.25) not significant after FDR.
- **Confounder-adjusted urban effect on Tmin bias: −0.70 to −1.04 °C** (CI excludes 0 for 5 of 6 models) — about the same size as the adjusted observed UHI, i.e. the models represent **almost none** of the urban night-time warming.
- Tmax: no significant urban–rural difference in bias. Rain ≥ 10 mm ETS: urban 0.01–0.03 lower, not significant.

## 5. Discussion
- Why models miss the nocturnal UHI: grid-box averaging over mixed urban/rural land, simplified or absent urban canopy schemes, 2-m diagnostic assumptions.
- Practical implication: urban heat-risk forecasts (night-time Tmin, heat index) need urban-aware post-processing; a simple station-type bias correction ⟨quantify⟩.
- Rainfall: point verification at 9–28 km cannot resolve urban convective enhancement → motivates RQ3 with dense gauges and km-scale models.
- Limitations: airport siting, GSOD QC, single evaluation period, representativeness of point vs grid.

## 6. Data & code availability
- Code and live system: https://github.com/Navavit/consolidated-weather (analysis in `analysis/`)
- Forecast archive and observations: branch `data` of the same repository
- Licences: Open-Meteo CC BY 4.0; GSOD public domain; WorldPop CC BY 4.0; Google Weather API data subject to Google Maps Platform terms (check before publication).
