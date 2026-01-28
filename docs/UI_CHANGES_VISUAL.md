# UI Changes - Visual Representation

## Before (Old Structure)

### Prepare Training Data Page - Export
```
┌─────────────────────────────────────────────────────┐
│  Step 4: Reduce Noise & Finalize Variable Selection │
│                                                       │
│  Selected Goal: revenue                               │
│  [Export Configuration Button]                        │
│                                                       │
│  Saves to:                                            │
│  training_data/de/20240115_120000/selected_columns.json
└─────────────────────────────────────────────────────┘
```

### Run Experiment Page - Load (Old)
```
┌─────────────────────────────────────────────────────┐
│  Training Data Configuration                          │
│  (from Prepare Training Data)                         │
│                                                       │
│  Select Training Data Config                          │
│  ┌───────────────────────────────────────────────┐  │
│  │ DE - 20240115_120000                          ▼│  │
│  └───────────────────────────────────────────────┘  │
│                                                       │
│  Single dropdown showing: COUNTRY - TIMESTAMP         │
└─────────────────────────────────────────────────────┘
```

---

## After (New Structure)

### Prepare Training Data Page - Export
```
┌─────────────────────────────────────────────────────┐
│  Step 4: Reduce Noise & Finalize Variable Selection │
│                                                       │
│  Selected Goal: revenue                               │
│  [Export Configuration Button]                        │
│                                                       │
│  Saves to:                                            │
│  training_data/de/revenue/20240115_120000/selected_columns.json
│                     ^^^^^^^^                          │
│                  Goal included!                       │
└─────────────────────────────────────────────────────┘
```

### Run Experiment Page - Load (New)
```
┌─────────────────────────────────────────────────────────────────┐
│  Training Data Configuration                                      │
│  (from Prepare Training Data)                                     │
│                                                                   │
│  Three side-by-side filters:                                      │
│                                                                   │
│  ┌────────────┬─────────────────┬─────────────────────────────┐ │
│  │  Country   │      Goal       │        Timestamp            │ │
│  ├────────────┼─────────────────┼─────────────────────────────┤ │
│  │  ┌──────┐  │  ┌───────────┐  │  ┌────────────────────────┐│ │
│  │  │  DE  │  │  │ revenue   ▼│  │  │ 20240115_120000       ▼││ │
│  │  └──────┘  │  └───────────┘  │  └────────────────────────┘│ │
│  │ (readonly) │   (dropdown)    │      (dropdown)            │ │
│  └────────────┴─────────────────┴─────────────────────────────┘ │
│                                                                   │
│  ✅ Loaded: DE - revenue - 20240115_120000                        │
└─────────────────────────────────────────────────────────────────┘
```

### Key Improvements

1. **Filters Side-by-Side**: All three filters (Country, Goal, Timestamp) are displayed in columns next to each other

2. **Clear Hierarchy**: Visual representation of the filtering hierarchy:
   ```
   Country → Goal → Timestamp
   ```

3. **Cascading Selection**: 
   - Select country (readonly, auto-detected)
   - Select goal → shows timestamps for that goal
   - Select timestamp → loads configuration

4. **Better UX**:
   - Country is readonly (derived from export context)
   - Goal dropdown only shows goals available for that country
   - Timestamp dropdown only shows timestamps for selected country+goal
   - Auto-selection works for all three levels

---

## GCS Path Structure

### Before
```
mmm-app-output/
└── training_data/
    ├── de/
    │   ├── 20240115_120000/
    │   │   └── selected_columns.json  ← goal="revenue" inside JSON
    │   └── 20240116_140000/
    │       └── selected_columns.json  ← goal="conversions" inside JSON
    └── fr/
        └── 20240115_120000/
            └── selected_columns.json  ← goal="revenue" inside JSON
```

**Problem**: Cannot filter by goal without reading all JSON files

### After
```
mmm-app-output/
└── training_data/
    ├── de/
    │   ├── revenue/
    │   │   ├── 20240115_120000/
    │   │   │   └── selected_columns.json
    │   │   └── 20240117_100000/
    │   │       └── selected_columns.json
    │   └── conversions/
    │       └── 20240116_140000/
    │           └── selected_columns.json
    └── fr/
        └── revenue/
            └── 20240115_120000/
                └── selected_columns.json
```

**Benefit**: Can filter by goal directly from path structure, faster listing and clearer organization

---

## Filter Flow

### Old Flow
```
1. User navigates to Run Experiment
2. System lists all training configs for country
3. Shows dropdown with timestamps only
4. Goal is hidden inside JSON
```

### New Flow
```
1. User navigates to Run Experiment
2. System shows country (from export context)
3. System lists available goals for that country
4. User selects goal from dropdown
5. System lists timestamps for country+goal
6. User selects timestamp
7. Configuration loads
```

---

## Data Migration

The migration script handles the restructuring:

```
OLD: training_data/de/20240115_120000/selected_columns.json
                    ↓
            Read JSON to get goal="revenue"
                    ↓
NEW: training_data/de/revenue/20240115_120000/selected_columns.json
```

### Migration Safety

- ✅ Dry-run mode available
- ✅ Old files can remain (not deleted by default)
- ✅ Files with missing goal are skipped
- ✅ Detailed logging and summary
- ✅ Can rollback by keeping old files

---

## Result Pages - Goal Filtering

### Before (No Goal Filtering)

**View Results Page:**
```
┌─────────────────────────────────────────────────────┐
│  View Model Results                                   │
│                                                       │
│  Experiment Name: [gmv001 ▼]                         │
│  Country: [DE, FR, UK ☑]                             │
│  Timestamp (optional): [20240115_120000 ▼]           │
│                                                       │
│  Shows all results regardless of goal variable       │
└─────────────────────────────────────────────────────┘
```

**View Best Results Page:**
```
┌─────────────────────────────────────────────────────┐
│  View Best Results                                    │
│                                                       │
│  Revision: [gmv001 ▼]                                │
│  Countries: [DE, FR, UK ☑]                           │
│                                                       │
│  Compares results across different goals mixed       │
└─────────────────────────────────────────────────────┘
```

**Review Model Stability Page:**
```
┌─────────────────────────────────────────────────────┐
│  Review Model Stability                               │
│                                                       │
│  Experiment Name: [gmv001 ▼]                         │
│  Country: [DE ☑]                                     │
│  Timestamp (optional): [20240115_120000 ▼]           │
│                                                       │
│  Analyzes stability without goal filtering           │
└─────────────────────────────────────────────────────┘
```

### After (With Goal Filtering)

**View Results Page:**
```
┌─────────────────────────────────────────────────────┐
│  View Model Results                                   │
│                                                       │
│  Experiment Name: [gmv001 ▼]                         │
│  Country: [DE, FR, UK ☑]                             │
│  Goal (dep_var): [revenue, conversions ☑] ← NEW!    │
│  Timestamp (optional): [20240115_120000 ▼]           │
│                                                       │
│  Shows only results for selected goals               │
└─────────────────────────────────────────────────────┘
```

**View Best Results Page:**
```
┌─────────────────────────────────────────────────────┐
│  View Best Results                                    │
│                                                       │
│  Revision: [gmv001 ▼]                                │
│  Countries: [DE, FR, UK ☑]                           │
│  Goal (dep_var): [revenue ☑] ← NEW!                 │
│                                                       │
│  Compares best results for same goal variable        │
└─────────────────────────────────────────────────────┘
```

**Review Model Stability Page:**
```
┌─────────────────────────────────────────────────────┐
│  Review Model Stability                               │
│                                                       │
│  Experiment Name: [gmv001 ▼]                         │
│  Country: [DE ☑]                                     │
│  Goal (dep_var): [revenue ☑] ← NEW!                 │
│  Timestamp (optional): [20240115_120000 ▼]           │
│                                                       │
│  Analyzes stability for selected goal variable       │
└─────────────────────────────────────────────────────┘
```

### Key Improvements for Result Pages

1. **Consistent Filtering**: All result pages now have goal filtering in the same location (between Country and Timestamp)

2. **Goal Extraction**: 
   - Goal (dep_var) extracted from `training-configs/{timestamp}/job_config.json`
   - No need to read all model outputs to determine goal
   - Cached in session state for performance

3. **Session State Persistence**:
   - `view_results_goals_value`: View Results page
   - `view_best_results_goals_value`: View Best Results page
   - `model_stability_goals_value`: Review Model Stability page

4. **Performance Optimization**:
   - Goal information cached per revision/country combination
   - Cache keys: `goals_cache_{page}_{rev}_{countries}`
   - Reduces repeated GCS API calls

5. **User Experience**:
   - Multiselect allows comparing multiple goals
   - Countries automatically filter when goals selected
   - Clear warning if goal information unavailable
   - Selections persist across page navigation
