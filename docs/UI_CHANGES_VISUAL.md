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
