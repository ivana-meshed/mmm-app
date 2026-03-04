# MMM Kickoff Meeting — Client Questionnaire

**Client profile:** Car rental marketplace platform (asset-light, two-sided
marketplace connecting customers with independent local rental operators).
Commission/transaction-fee revenue model.

Use this document as a running agenda and note-taking sheet for the kickoff
meeting. Work through each section in order.

**Legend**
- Priority: 🔴 P1 = must-have for the first MMM run · 🟡 P2 = nice-to-have for v1 · 🟢 P3 = future iteration
- Origin: questions carried over from the original brief are marked **`orig`**; questions added based on the app, Robyn, and marketplace context are marked **`🆕 new`**

---

## 1 — Business Setup & Steering

### Budget structure
| # | Origin | Question | Notes / Answer |
|---|--------|----------|----------------|
| 🔴 1.1 | orig | Is there one global marketing budget, or are budgets fixed per market / business plan? | |
| 🔴 1.2 | orig | How often is budget reallocated across channels? (weekly / monthly / quarterly / ad-hoc) | |
| 🔴 1.3 | orig | What are the biggest pain points in the budgeting process? (e.g. lack of trust in results, internal sign-off, cross-market alignment) | |

### Shifts & events
| # | Origin | Question | Notes / Answer |
|---|--------|----------|----------------|
| 🔴 1.4 | orig | Were there any major budget or channel shifts recently, or are any planned? Provide approximate dates and description. | |

### Business goal / KPI
| # | Origin | Question | Notes / Answer |
|---|--------|----------|----------------|
| 🔴 1.5 | orig | What is the primary KPI the business is steered by — total bookings, closed GMV, new customers, or something else? | |
| 🔴 1.6 | orig | Is this KPI consistent across all markets, or does it differ per country / region? | |
| 🔴 1.7 | 🆕 new | Is the KPI a **count** (bookings, conversions) or a **revenue value** (GMV, commission earned)? — Robyn requires declaring `dep_var_type` as either `"conversion"` or `"revenue"`, which changes how ROAS is computed internally. Getting this wrong inflates or deflates all ROAS figures on pages 6 and 8. | |
| 🔴 1.8 | 🆕 new | For the budget allocator (page 6): what is the correct ROAS denominator — commission earned, total GMV, or bookings × average booking value? These can differ by ×5–10×. | |

### Pricing & promotions
| # | Origin | Question | Notes / Answer |
|---|--------|----------|----------------|
| 🔴 1.9 | orig | Are there site-wide price reduction or promotional campaigns? Are these currently tracked and can the data be shared? | |
| 🟡 1.10 | 🆕 new | Is weekly average booking value (or average platform price index) available as a time series? As a marketplace the booking price is partner-driven and varies over time; if left uncontrolled it absorbs marketing signal. Map as `context_var`. | |

### Seasonality
| # | Origin | Question | Notes / Answer |
|---|--------|----------|----------------|
| 🔴 1.11 | orig | Beyond standard public holidays, are there known seasonal demand peaks? (e.g. summer rental surge, moving season, ski season, weekday vs weekend patterns) | |
| 🟡 1.12 | orig | Are weather-driven demand patterns observable? (rain, cold, snowfall, summer temperatures.) What is the estimated scale of the effect? | |

> **Note (🆕 new):** Robyn uses Facebook Prophet under the hood and automatically fits country-level public holiday calendars using the ISO-2 country code of each market (e.g. `DE`, `FR`, `NL`). Standard national holidays are therefore already handled without needing a manual flag. Custom peaks from 1.11 above should be added as `factor_vars` (binary or ordinal 0/1/2 flags per week).

### Supply-side context variables
| # | Origin | Question | Notes / Answer |
|---|--------|----------|----------------|
| 🟡 1.13 | orig | Is aggregated fleet occupancy data available (e.g. ≥ 85 % utilisation for passenger cars)? At what granularity (daily / weekly)? Can act as a supply-ceiling `context_var` to avoid endogeneity. | |
| 🔴 1.14 | 🆕 new | Is a weekly time series of **number of active partner businesses** or **total listed vehicles** available? This platform grows by recruiting supply-side partners. Without a proxy for supply-side scale as a `context_var`, Robyn may attribute organic booking growth from fleet expansion to marketing spend — a critical confound for a marketplace model. | |
| 🔴 1.15 | 🆕 new | Were there specific **new market launches** (entering a new country or major city cluster) in the data window? Each launch creates a structural step-change in bookings unrelated to marketing. These must be modelled as `factor_vars` (binary on/off flags); if missed, the model will mis-attribute the launch spike to whatever channel was spending at that time. | |

---

## 2 — 🆕 Data Availability & Structure

> **🆕 New section.** These questions have no equivalent in the original brief but are required to configure the app and run a first training job without surprises.

### Snowflake schema & access
| # | Origin | Question | Notes / Answer |
|---|--------|----------|----------------|
| 🔴 2.1 | 🆕 new | Which Snowflake database, schema, and table(s) contain the weekly marketing spend and KPI data? Confirm who owns the schema and who needs to approve read access for a service account. | |
| 🔴 2.2 | 🆕 new | What is the primary date column called and in what format? The app expects a column that maps to `DATE` (ISO format `YYYY-MM-DD`). Confirm whether it is already weekly (Monday-anchored) or daily and needs aggregation. | |
| 🔴 2.3 | 🆕 new | Does the spend/KPI table include a **market / country column** (e.g. `COUNTRY`, `COUNTRY_CODE`, `MARKET`)? The training pipeline filters data per market using this column. If all markets are in one table, confirm the column name and the exact values used (ISO-2 codes preferred: `DE`, `FR`, `NL`, etc.). | |
| 🔴 2.4 | 🆕 new | Are there **separate tables** for different data sources (e.g. one table for paid media spend, another for bookings, another for CRM deliveries), or is everything pre-joined into a single wide table? This determines how much ETL work is needed before the app's Connect Data step. | |

### Data history length
| # | Origin | Question | Notes / Answer |
|---|--------|----------|----------------|
| 🔴 2.5 | 🆕 new | How far back does the spend + KPI data go **per market**? Robyn requires at minimum ~2 years of weekly observations (104 data points) to estimate adstock decay and saturation reliably. Markets with shorter history will likely need to be excluded from v1 or modelled with tighter hyperparameter ranges. | |
| 🟡 2.6 | 🆕 new | How fresh is the data — is last week's spend available by Monday/Tuesday, or is there a longer reporting lag? This determines the practical refresh cadence and how much of a "test window" can realistically be held out. | |

### Exposure metrics (Robyn hard requirement)
| # | Origin | Question | Notes / Answer |
|---|--------|----------|----------------|
| 🔴 2.7 | 🆕 new | For every `paid_media_spends` column there must be a paired `paid_media_vars` column (the exposure metric — impressions, clicks, or sessions). Robyn enforces a strict 1-to-1 pairing and the training job will fail if any spend column lacks a matching exposure column. Confirm that impressions or clicks are available for **every** paid channel in scope, including Bing, Display, YouTube, and App. | |
| 🟡 2.8 | 🆕 new | For channels where both impressions and clicks are available, which should be used as the exposure metric? Impressions better capture reach/awareness (TV, Display, YouTube); clicks better capture intent (Search). Align on this before the mapping step (page 2) to avoid inconsistency across runs. | |

---

## 3 — Marketing Channel Setup

> Verify that every active channel is represented in the data extract and confirm the correct split/granularity for modelling.

### Meta (Facebook / Instagram)
| # | Origin | Question | Notes / Answer |
|---|--------|----------|----------------|
| 🔴 3.1 | orig | Confirm ad formats in use — video, static, Reels? | |
| 🔴 3.2 | orig | Campaign objective split: conversion, reach, and app-install campaigns? *(Modelling priorities — P1: lower/upper funnel + app as separate channels; P2: video vs static impressions; P3: impressions vs clicks)* | |

### SEO / Organic Search
| # | Origin | Question | Notes / Answer |
|---|--------|----------|----------------|
| 🔴 3.3 | orig | Were there major site or page-setup changes that could be reverse-engineered as a step-change variable? (e.g. v1 site Jan-22, v2 Jan-24, v3 Oct-25.) Note: Google Search Console data is typically only available from June 2024 onwards. | |
| 🔴 3.4 | orig | Can non-branded impressions from Google Search Console be shared as an organic input variable? | |

### Blog
| # | Origin | Question | Notes / Answer |
|---|--------|----------|----------------|
| 🟢 3.5 | orig | Is there a content / blog programme? Is it significant enough to model as a separate channel, or can it be treated as part of SEO? | |

### Google Ads
| # | Origin | Question | Notes / Answer |
|---|--------|----------|----------------|
| 🔴 3.6 | orig | Confirm the planned channel split: **search_brand** (SEARCH/SEARCH_PARTNERS + "brand" in campaign name) · **search_nonbrand** · **pmax** (MIXED network) · **display** (CONTENT network) · **youtube** (YOUTUBE network). Any deviations? | |

### Bing / Microsoft Ads
| # | Origin | Question | Notes / Answer |
|---|--------|----------|----------------|
| 🔴 3.7 | orig | Confirm split: **search_brand** · **search_nonbrand** · **shopping** (retailer network) · **pmax** (Cross-network) · **display** (Audience). Any deviations? | |

### CRM / Email
| # | Origin | Question | Notes / Answer |
|---|--------|----------|----------------|
| 🔴 3.8 | orig | Were any CRM channels recently stopped or significantly changed? | |
| 🔴 3.9 | orig | Confirm which email types are in scope: marketing newsletters, onboarding, upsell, birthday campaigns. *(Exclude: booking confirmations, abandoned-cart triggers.)* Metric to use: email deliveries. | |

### App
| # | Origin | Question | Notes / Answer |
|---|--------|----------|----------------|
| 🔴 3.10 | orig | App-install campaigns confirmed in Meta. Are there other channels running install campaigns (Apple Search Ads, Google UAC)? | |
| 🟡 3.11 | 🆕 new | What is the typical lag between app install and first booking? For many marketplace apps this is 2–4 weeks. If the install-to-booking lag is long, a short adstock window on app install spend will underestimate its ROI. Relevant for setting the hyperparameter prior range in the pre-flight test. | |

### TV
| # | Origin | Question | Notes / Answer |
|---|--------|----------|----------------|
| 🔴 3.12 | orig | Do you run TV campaigns? If so, is data available as daily spend, GRPs (Gross Rating Points), or TARPs (Target Audience Rating Points)? | |

### OOH (Out-of-Home)
| # | Origin | Question | Notes / Answer |
|---|--------|----------|----------------|
| 🔴 3.13 | orig | Do you run OOH campaigns? Is spend or impression data available at weekly granularity? | |

### Social / Influencer
| # | Origin | Question | Notes / Answer |
|---|--------|----------|----------------|
| 🔴 3.14 | orig | Were there large-scale influencer partnerships or organic social pushes that should be captured as a variable? | |

### Pricing data
| # | Origin | Question | Notes / Answer |
|---|--------|----------|----------------|
| 🔴 3.15 | orig | Is historical pricing data available (average booking price, discount rates, promo codes applied)? Currently no pricing history is assumed — confirm. | |

### 🆕 Channel-level data quality
| # | Origin | Question | Notes / Answer |
|---|--------|----------|----------------|
| 🔴 3.16 | 🆕 new | Are there channels that were **paused for extended periods** (4+ weeks of zero spend) within the data window? Channels with many zero-spend weeks have weak adstock signal and may need to be excluded or treated with narrower hyperparameter ranges. | |
| 🟡 3.17 | 🆕 new | Do multiple channels tend to **ramp up and down together** (e.g. all channels increase spend in Q4 or during promotional events simultaneously)? High inter-channel correlation makes it hard for Robyn to separate individual channel contributions. If so, flag the periods and plan to add a promotional `factor_var` to absorb the correlated effect. | |
| 🟡 3.18 | 🆕 new | Is any spend attributed to **partner recruitment / B2B marketing** (acquiring new rental operators to the platform) rather than consumer marketing? This spend must be excluded from the MMM data before modelling — it targets the supply side, not the demand side. | |

---

## 4 — MMM Modelling Decisions

### Existing work
| # | Origin | Question | Notes / Answer |
|---|--------|----------|----------------|
| 🔴 4.1 | orig | Are there any existing MMM outputs or analyses the client can share? (Avoids rebuilding from scratch if the goal is to improve on prior work.) | |

### Data & granularity
| # | Origin | Question | Notes / Answer |
|---|--------|----------|----------------|
| 🔴 4.2 | orig | **Weekly vs daily modelling?** Weekly is the recommended default; daily requires high-volume data and is more noise-prone. | |
| 🔴 4.3 | orig | **Missing-data strategy:** impute (mean / forward-fill) or exclude the variable if missing values exceed a threshold? Define the threshold. | |
| 🔴 4.4 | 🆕 new | **Per-market data sufficiency check:** confirm each market to be modelled has ≥ 104 weekly observations with non-zero spend. The training job silently skips markets where the dependent variable or all media spend columns have zero variance — verify in page 3 (Validate Mapping) before submitting the first training run. | |

### Adstock & saturation
| # | Origin | Question | Notes / Answer |
|---|--------|----------|----------------|
| 🔴 4.5 | orig | **Adstocking:** use Robyn defaults or run a pre-flight test to estimate channel-specific decay rates? | |
| 🔴 4.6 | orig | **Saturation curves:** use Robyn recommendation or enrich via pre-flight saturation test? | |
| 🟡 4.7 | 🆕 new | **Geometric vs Weibull adstock?** Geometric adstock assumes an exponential decay (one parameter: theta). Weibull adstock uses a two-parameter curve that can model a delayed-peak carry-over effect — more realistic for brand/awareness channels like TV, OOH, and YouTube where impact builds before it decays. Consider Weibull for those channels in v2. | |

### Model configuration
| # | Origin | Question | Notes / Answer |
|---|--------|----------|----------------|
| 🔴 4.8 | orig | **Ridge penalty:** define the lambda range to explore in the hyperparameter search. | |
| 🔴 4.9 | orig | **Train / validation / test strategy:** recommended approach — optimise on train + validation window; use held-out test set as a sanity check only, not for budget allocation. | |
| 🔴 4.10 | orig | **Primary model scoring metric:** NRMSE, DECOMP.RSSD, or the combined Robyn default objective? | |
| 🔴 4.11 | 🆕 new | **Acceptable model quality floor per market (page 8 filters):** The stability page filters models by R² ≥ threshold, NRMSE ≤ threshold, and DECOMP.RSSD ≤ threshold. The app defaults are R² ≥ 0.70 / NRMSE ≤ 0.15 / DECOMP.RSSD ≤ 0.20. Smaller markets with shorter history or noisier data will rarely meet the "Good" bar — agree upfront on the minimum acceptable threshold so the client is not surprised by a sparse result set on first view. | |
| 🟡 4.12 | 🆕 new | **Budget allocator constraints (page 6):** The built-in optimizer needs upper and lower spend bounds per channel. Are there any contractual minimums (e.g. agency retainer floors, platform spend commitments) or policy maximums per channel? Without these the optimizer will recommend extreme corner solutions. | |
| 🟡 4.13 | 🆕 new | **Model refresh cadence:** How often should the model be retrained once live — monthly, quarterly? This determines how large a "test" window should be held out (holding out too little makes the test point meaningless; holding out too much wastes recent data the model could learn from). | |

---

## 5 — 🆕 Car Rental Marketplace — Specific Considerations

> **🆕 New section.** These topics arise specifically from the asset-light, two-sided marketplace model and have no equivalent in a standard single-brand advertiser MMM engagement.

| # | Origin | Question | Notes / Answer |
|---|--------|----------|----------------|
| 🔴 5.1 | 🆕 new | **How are "markets" defined for modelling purposes?** The training pipeline runs one Robyn model per market, filtered by a country column. Are markets defined at country level (e.g. DE, FR, NL) or at a sub-national level (city clusters, airport zones)? Marketing campaigns are typically country-targeted, so country is usually the right level — but confirm, as a mismatch between campaign targeting granularity and model granularity will add noise. | |
| 🔴 5.2 | 🆕 new | **Which product categories should be in scope for v1?** The platform offers short-term cars, vans, minibuses, and long-term/subscription rentals. These categories likely have different demand drivers and seasonal patterns. Decide whether `dep_var` = total bookings across all categories, or whether to start with the highest-volume category and treat others as separate models later. | |
| 🟡 5.3 | 🆕 new | **What is the typical booking lead time?** (Time from first ad exposure to completed booking.) Short-term rentals are often booked 1–14 days in advance; long-term rentals may have lead times of several weeks. This directly informs the upper bound of the adstock decay window during the pre-flight test — channels that influence long-lead bookings need longer carry-over windows than impulse-purchase categories. | |
| 🟡 5.4 | 🆕 new | **What share of bookings come from returning vs new customers?** If repeat customers are a large share of volume (common in car rental), paid media is mainly acquiring new customers while repeat volume is organic baseline. Understanding the new/return split helps calibrate expected paid media ROAS and set a more realistic decomposition prior (DECOMP.RSSD bound). | |
| 🟡 5.5 | 🆕 new | **What share of bookings are completed in-app vs on the web?** If app bookings are significant, app install campaign ROI is underestimated by standard attribution. This also means the app `paid_media_spends` channel should be kept separate (not merged with Meta conversion spend) so the model can estimate its distinct carry-over effect. | |
| 🟢 5.6 | 🆕 new | **Is there cross-market demand (customers booking in a different country than their home market)?** For a digital marketplace targeting tourists or business travellers, a German user may convert on the French site. If the data attributes bookings to the user's billing country rather than the rental country, there will be a systematic mismatch between where spend is invested and where revenue is recorded. Confirm the attribution logic before modelling. | |

---

## 6 — 🆕 Stakeholder & Process Setup

> **🆕 New section.** MMM results are only useful if they feed into real decisions. These questions reduce the risk of delivering a technically sound model that is never acted on.

| # | Origin | Question | Notes / Answer |
|---|--------|----------|----------------|
| 🔴 6.1 | 🆕 new | **Who will consume the model outputs?** CMO / VP Marketing (strategic allocation), performance marketing team (channel-level ROAS), or CFO / finance (budget justification)? Each audience needs a different emphasis in the results walk-through and a different level of technical detail in the output. | |
| 🔴 6.2 | 🆕 new | **What attribution model is currently in use** (last-click, data-driven / DDA, platform-reported ROAS)? Understanding what the team currently believes about channel performance is critical for anticipating where MMM results will diverge most — and for preparing stakeholders for findings that contradict their current view (e.g. brand search ROAS dropping once attribution is corrected for baseline). | |
| 🔴 6.3 | 🆕 new | **What is the budget decision cadence?** Monthly, quarterly, or annual planning cycle? The answer determines (a) how often the model needs to be refreshed to remain actionable, and (b) how far ahead the built-in 3-month budget allocator forecast on page 6 is actually useful. | |
| 🟡 6.4 | 🆕 new | **Is there an internal data / analytics team** who will own or co-own the model after delivery? If yes, they should be included in the model review sessions and trained on pages 6 and 8. If no, plan for a fully managed service and agree a maintenance SLA. | |
| 🟡 6.5 | 🆕 new | **Data governance & access provisioning timeline.** Who needs to approve a read-only service account for Snowflake? Experience shows this can take days to weeks in larger organisations. Start the access request immediately after the kickoff — it is frequently the longest lead-time item before the first training run. | |

---

## 7 — Next Steps

| Action | Owner | Due |
|--------|-------|-----|
| Provision read-only Snowflake service account *(start immediately — longest lead time)* | Client | |
| Share raw marketing spend data export (or confirm Snowflake table + schema) | Client | |
| Confirm KPI table, dep_var column name, and dep_var_type (revenue vs conversion) | Client | |
| Confirm market / country column name and ISO codes in data | Client | |
| Confirm data history length per market (≥ 104 weeks check) | Consultant | |
| Identify supply-side growth proxy variable (partner count / listed vehicles) | Client + Consultant | |
| Flag new market launch dates for factor variable creation | Client | |
| Map data columns to MMM variable categories (app page 2) | Consultant | |
| Validate mapping and data coverage (app page 3) | Consultant | |
| First model training run (app pages 4–6) | Consultant | |
| Model stability review (app page 8) | Consultant | |
| Results walk-through with client stakeholders | Both | |
