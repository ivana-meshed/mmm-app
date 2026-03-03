# MMM Kickoff Meeting — Client Questionnaire

**Client profile:** Car rental marketplace platform (asset-light, two-sided
marketplace connecting customers with independent local rental operators).
Commission/transaction-fee revenue model.

Use this document as a running agenda and note-taking sheet for the kickoff
meeting. Work through each section in order.  
Priority tags: 🔴 **P1** = must-have for the first MMM run · 🟡 **P2** =
nice-to-have for v1 · 🟢 **P3** = future iteration.

---

## 1 — Business Setup & Steering

### Budget structure
| # | Question | Notes / Answer |
|---|----------|----------------|
| 🔴 1.1 | Is there one global marketing budget, or are budgets fixed per market / business plan? | |
| 🔴 1.2 | How often is budget reallocated across channels? (weekly / monthly / quarterly / ad-hoc) | |
| 🔴 1.3 | What are the biggest pain points in the budgeting process? (e.g. lack of trust in results, internal sign-off, cross-market alignment) | |

### Shifts & events
| # | Question | Notes / Answer |
|---|----------|----------------|
| 🔴 1.4 | Were there any major budget or channel shifts recently, or are any planned? Provide approximate dates and description. | |

### Business goal / KPI
| # | Question | Notes / Answer |
|---|----------|----------------|
| 🔴 1.5 | What is the primary KPI the business is steered by — total bookings, closed GMV, new customers, or something else? | |
| 🔴 1.6 | Is this KPI consistent across all markets, or does it differ per country / region? | |

### Pricing & promotions
| # | Question | Notes / Answer |
|---|----------|----------------|
| 🔴 1.7 | Are there site-wide price reduction or promotional campaigns? Are these currently tracked and can the data be shared? | |

### Seasonality
| # | Question | Notes / Answer |
|---|----------|----------------|
| 🔴 1.8 | Beyond standard public holidays, are there known seasonal demand peaks? (e.g. summer rental surge, moving season, ski season, weekday vs weekend patterns) | |
| 🟡 1.9 | Are weather-driven demand patterns observable? (rain, cold, snowfall, summer temperatures.) What is the estimated scale of the effect? | |

### Supply-side / context variables
| # | Question | Notes / Answer |
|---|----------|----------------|
| 🟡 1.10 | Is aggregated fleet occupancy data available (e.g. ≥ 85 % utilisation for passenger cars)? At what granularity (daily / weekly)? This can act as a supply-ceiling variable to avoid endogeneity. | |

---

## 2 — Marketing Channel Setup

> Verify that every active channel is represented in the data extract,
> and confirm the correct split/granularity for modelling.

### Meta (Facebook / Instagram)
| # | Question | Notes / Answer |
|---|----------|----------------|
| 🔴 2.1 | Confirm ad formats in use — video, static, Reels? | |
| 🔴 2.2 | Campaign objective split: conversion, reach, and app-install campaigns? *(Modelling priorities — P1: lower/upper funnel + app as separate channels; P2: video vs static impressions; P3: impressions vs clicks)* | |

### SEO / Organic Search
| # | Question | Notes / Answer |
|---|----------|----------------|
| 🔴 2.3 | Were there major site or page-setup changes that could be reverse-engineered as a step-change variable? (e.g. v1 site Jan-22, v2 Jan-24, v3 Oct-25.) Note: Google Search Console data is typically only available from June 2024 onwards. | |
| 🔴 2.4 | Can non-branded impressions from Google Search Console be shared as an organic input variable? | |

### Blog
| # | Question | Notes / Answer |
|---|----------|----------------|
| 🟢 2.5 | Is there a content / blog programme? Is it significant enough to model as a separate channel, or can it be treated as part of SEO? | |

### Google Ads
| # | Question | Notes / Answer |
|---|----------|----------------|
| 🔴 2.6 | Confirm the planned channel split: **search_brand** (SEARCH/SEARCH_PARTNERS + "brand" in campaign name) · **search_nonbrand** · **pmax** (MIXED network) · **display** (CONTENT network) · **youtube** (YOUTUBE network). Any deviations? | |

### Bing / Microsoft Ads
| # | Question | Notes / Answer |
|---|----------|----------------|
| 🔴 2.7 | Confirm split: **search_brand** · **search_nonbrand** · **shopping** (retailer network) · **pmax** (Cross-network) · **display** (Audience). Any deviations? | |

### CRM / Email
| # | Question | Notes / Answer |
|---|----------|----------------|
| 🔴 2.8 | Were any CRM channels recently stopped or significantly changed? | |
| 🔴 2.9 | Confirm which email types are in scope: marketing newsletters, onboarding, upsell, birthday campaigns. *(Exclude: booking confirmations, abandoned-cart triggers.)* Metric to use: email deliveries. | |

### App
| # | Question | Notes / Answer |
|---|----------|----------------|
| 🔴 2.10 | App-install campaigns are confirmed in Meta. Are there other channels running install campaigns (Apple Search Ads, Google UAC)? | |

### TV
| # | Question | Notes / Answer |
|---|----------|----------------|
| 🔴 2.11 | Do you run TV campaigns? If so, is data available as daily spend, GRPs (Gross Rating Points), or TARPs (Target Audience Rating Points)? | |

### OOH (Out-of-Home)
| # | Question | Notes / Answer |
|---|----------|----------------|
| 🔴 2.12 | Do you run OOH campaigns? Is spend or impression data available at weekly granularity? | |

### Social / Influencer
| # | Question | Notes / Answer |
|---|----------|----------------|
| 🔴 2.13 | Were there large-scale influencer partnerships or organic social pushes that should be captured as a variable? | |

### Pricing data
| # | Question | Notes / Answer |
|---|----------|----------------|
| 🔴 2.14 | Is historical pricing data available (average booking price, discount rates, promo codes applied)? Currently no pricing history is assumed — confirm. | |

---

## 3 — MMM Modelling Decisions

### Existing work
| # | Question | Notes / Answer |
|---|----------|----------------|
| 🔴 3.1 | Are there any existing MMM outputs or analyses the client can share? (Avoids rebuilding from scratch if the goal is to improve on prior work.) | |

### Data & granularity
| # | Question | Notes / Answer |
|---|----------|----------------|
| 🔴 3.2 | **Weekly vs daily modelling?** Weekly is recommended as the default for most budget sizes; daily requires high-volume data and is more noise-prone. | |
| 🔴 3.3 | **Missing-data strategy:** impute (mean / forward-fill) or exclude the variable if missing values exceed a threshold? Define the threshold. | |

### Adstock & saturation
| # | Question | Notes / Answer |
|---|----------|----------------|
| 🔴 3.4 | **Adstocking:** use Robyn defaults or run a pre-flight test to estimate channel-specific decay rates? | |
| 🔴 3.5 | **Saturation curves:** use Robyn recommendation or enrich via pre-flight saturation test? | |

### Model configuration
| # | Question | Notes / Answer |
|---|----------|----------------|
| 🔴 3.6 | **Ridge penalty:** define the lambda range to explore in the hyperparameter search. | |
| 🔴 3.7 | **Train / validation / test strategy:** recommended approach — optimise on train + validation window; use the held-out test set as a sanity check only, not as input to budget allocation. | |
| 🔴 3.8 | **Primary model scoring metric:** NRMSE, DECOMP.RSSD, or the combined Robyn default objective? | |

---

## 4 — Next Steps

| Action | Owner | Due |
|--------|-------|-----|
| Share raw marketing spend data export | Client | |
| Share Snowflake / BI access credentials | Client | |
| Confirm KPI table and date range | Client | |
| Map data columns to MMM variable categories | Consultant | |
| First model training run (pages 5–6) | Consultant | |
| Model stability review (page 8) | Consultant | |
| Results walk-through with client | Both | |
