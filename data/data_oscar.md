# daily_market_kpis — Data Definition & Update Note
**Table:** `dwh.bl.daily_market_kpis`
**Grain:** 1 row per market per day
**Date range:** 2024-01-01 → current date
**Markets:** Belgium, Denmark, Germany, Netherlands, Spain, USA
*(Sweden rows exist in the table but carry no data — exclude from all analysis)*

---

## What's new in this version

### 1. Google campaign intent classification (`_3` columns)

A third grouping of Google Ads data has been added alongside the existing two:

| Grouping | Logic | Columns |
|---|---|---|
| `_1` | Ad network type (Search / PMax / Display / YouTube) | `GOOGLE_SEARCH_NONBRAND_*`, `GOOGLE_PMAX_*`, etc. |
| `_2` | Nonbrand search + PMax vs everything else | `GOOGLE_SEARCH_NONBRAND_*_2`, `GOOGLE_PMAX_*_2`, `GOOGLE_OTHER_*_2` |
| `_3` | Campaign intent classification by campaign name | `GOOGLE_{CATEGORY}_{METRIC}_3` |

The `_3` grouping adds **16 mutually exclusive categories** per metric (impressions, clicks, cost):

| Category | Description |
|---|---|
| `experiment` | Any campaign containing `(Experiment)` in the name |
| `long_term` | Long Term / Minileasing / Bilabonnement campaigns |
| `car_type_truck` | Moving truck / Autotransporter / Lastbil / Flyttevogn campaigns |
| `car_type_van` | Varevogn / Kassebil / Kassevogn / Varebil / Minibus / Kølebil campaigns |
| `car_type_other` | Autotrailer / Foodtruck campaigns |
| `car_type_car` | National Biludlejning / Elbil / Keyword Billeje / Keyword Biludlejning campaigns |
| `brand` | Brand campaigns |
| `competitor` | Competitor / Konkurrenter campaigns |
| `display` | Display campaigns |
| `video` | Video / YouTube campaigns |
| `b2b` | B2B Lead Generation campaigns |
| `app` | App install campaigns |
| `geo` | City-targeted + Biludlejning city campaigns (deprecated, see limitations) |
| `nonbrand` | Generic keyword campaigns (Billeje / Lej en Bil / Car Rental) |
| `performance_max` | Campaigns running on MIXED ad network type (PMax) |
| `other` | Anything not matched above |

**Columns follow the pattern:** `GOOGLE_{CATEGORY}_{METRIC}_3`
e.g. `GOOGLE_CAR_TYPE_TRUCK_COST_3`, `GOOGLE_GEO_IMPRESSIONS_3`, `GOOGLE_NONBRAND_CLICKS_3`

**Validation:** `_3` categories sum exactly to `GOOGLE_TOTAL_{METRIC}` on every row. ✅

**Important:** `_1` and `_2` columns are **identical to the previous version** — no breaking changes.

---

### 2. Bookings and GMV split by vehicle type and geography

Bookings and GMV are now broken out into mutually exclusive sub-categories:

**Vehicle type (non-Copenhagen only):**
- `BOOKINGS_VAN` / `GMV_NET_EUR_VAN` — Van, US Van, US Cargo Van
- `BOOKINGS_MINIBUS` / `GMV_NET_EUR_MINIBUS` — Minibus
- `BOOKINGS_TRUCK` / `GMV_NET_EUR_TRUCK` — Moving Truck, US Moving Truck
- `BOOKINGS_CAR` / `GMV_NET_EUR_CAR` — Car, US Car, US SUV, US 7-Seater
- `BOOKINGS_OTHER` / `GMV_NET_EUR_OTHER` — Others, US Other, US Pickup Truck

**Geography:**
- `BOOKINGS_COPENHAGEN` / `GMV_NET_EUR_COPENHAGEN` — bookings at Copenhagen locations
- `BOOKINGS_NON_COPENHAGEN` / `GMV_NET_EUR_NON_COPENHAGEN` — all other locations

**Mutual exclusivity:** Copenhagen bookings are excluded from all vehicle type buckets. A booking at a Copenhagen location is counted only in `BOOKINGS_COPENHAGEN`. The sum of all seven buckets (van + minibus + truck + car + other + copenhagen + non_copenhagen is NOT the right check — the correct checks are:
- `van + minibus + truck + car + other + copenhagen == bookings` ✅
- `copenhagen + non_copenhagen == bookings` ✅

**Bug fixed:** A precedence bug in the previous version caused all non-Copenhagen rows to be counted in every vehicle bucket regardless of vehicle type. This has been corrected. Numbers will differ from any prior manual calculation.

---

## Data availability by source

| Source | Belgium | Denmark | Germany | Netherlands | Spain | USA |
|---|---|---|---|---|---|---|
| Google Ads (_1, _2, _3) | 2024-01-01 → now | 2024-01-01 → now | 2024-01-01 → now | 2024-01-01 → now | 2024-01-01 → now | 2024-06-25 → now |
| Facebook Ads | 2024-01-01 → now | 2024-01-01 → now | 2024-01-10 → now | 2024-01-01 → now | 2024-01-01 → now | 2024-07-11 → now |
| Bing Ads | 2024-01-01 → now | 2024-01-01 → now | 2024-01-01 → now | 2024-01-01 → now | 2024-01-01 → now | 2024-06-25 → now |
| Bookings / GMV | 2024-01-01 → now | 2024-01-01 → now | 2024-01-01 → now | 2024-01-01 → now | 2024-01-01 → now | 2024-05-20 → now |
| Occupancy / Availability | 2024-11-26 → now | 2024-11-26 → now | 2024-11-26 → now | 2024-11-26 → now | 2024-11-26 → now | 2024-11-26 → now |
| CRM | 2024-01-01 → now | 2024-01-01 → now | 2024-01-01 → now | 2024-01-01 → now | 2024-01-01 → now | 2024-01-01 → now |
| Weather | 2024-01-01 → 2026-03-25 | 2024-01-01 → 2026-03-25 | 2024-01-01 → 2026-03-25 | 2024-01-01 → 2026-03-25 | 2024-01-01 → 2026-03-25 | — |
| Sessions / GA4 | 2024-01-01 → now | 2024-01-01 → now | 2024-01-01 → now | 2024-01-01 → now | 2024-01-01 → now | 2025-01-14 → now |
| SEO (monthly grain) | 2024-01-01 → now | 2024-01-01 → now | 2024-01-01 → now | 2024-01-01 → now | 2024-01-01 → now | 2025-01-01 → now |
| TV / Radio spend | — | 2024-01-01 → 2026-04-30 | — | — | — | — |
| Prices | 2025-06-04 → now | 2025-06-04 → now | 2025-06-04 → now | 2025-06-04 → now | 2025-06-04 → now | 2025-06-04 → now |

---

## Known limitations

**`_3` classification is built for Denmark only.**
The campaign name patterns (Danish keywords: biludlejning, kassevogn, billeje, etc.) only apply to DK campaigns. For other markets, the `%city%` pattern in particular may misclassify campaigns — e.g. a campaign called "New York City" would be tagged as `geo`. Do not use `_3` columns for non-Denmark markets without re-validating the classification.

**Occupancy and availability: 18 months of history only.**
`fct_vehicle_status_daily` backfill only covers from 2024-11-26. If these are used as control variables in Robyn, the model period should be set to start no earlier than December 2024, or these variables should be excluded for longer-horizon models.

**Prices: 12 months of history only.**
`AVG_BASE_PRICE`, `AVG_PRICE`, and car-specific variants are available from June 2025 only. Same constraint as occupancy for MMM use.

**Weather: stale since March 25, 2026.**
`dwh.stg.meteo_weather` has not been refreshed. Approximately 10 weeks of weather data is missing at the tail for all European markets. USA weather is not available at all. A pipeline refresh is pending.

**TV / Radio spend: Denmark only, gap at tail.**
`WBR_TOTAL_SPEND`, `MMM_OSCAR_TV`, `MMM_OSCAR_RADIO` are populated for Denmark only and run through April 30, 2026. The ~5 week gap (May–June 2026) may reflect data not yet available from the media agency.

**Geo spend bucket: structural break pre/post 2023.**
`GOOGLE_GEO_COST_3` will show significant spend in the historical period due to city-targeted Biludlejning campaigns that were active until ~2023 and have since been deprecated. For MMM, either use a structural break variable for the geo channel or restrict the model period to post-2023 where geo spend is negligible.

**SEO columns are monthly grain joined to daily rows.**
`SEO_BRAND_CLICKS_MONTH` and `SEO_BRAND_IMPRESSIONS_MONTH` repeat the same monthly value for every day in that month. Do not aggregate these columns across days — sum at month level only, or use the first day of the month.

**Sweden rows should be excluded.**
Sweden is present in `dim_markets` but has no operational data. All spend, bookings, and most other columns are null. Filter with `WHERE market_name != 'Sweden'`.
