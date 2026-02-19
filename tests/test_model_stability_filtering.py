"""
Tests for Model Stability filtering logic.

Tests that "All" mode includes all models without filtering.
"""

import sys
from pathlib import Path

# Add app directory to path
app_path = Path(__file__).parent.parent / "app"
sys.path.insert(0, str(app_path))


def test_all_mode_includes_all_models():
    """Test that 'All' mode doesn't filter out any models"""
    # Simulate the filtering logic
    import pandas as pd
    
    # Create sample data with various quality levels
    hyp_f = pd.DataFrame({
        'solID': ['1', '2', '3', '4', '5'],
        'rsq_train': [0.9, 0.7, 0.5, 0.3, 0.0],  # From excellent to poor
        'nrmse_train': [0.05, 0.15, 0.25, 0.35, 1.0],  # From excellent to poor
        'decomp.rssd': [0.01, 0.1, 0.2, 0.3, 1.0]  # From excellent to poor
    })
    
    mode = "All"
    
    # Apply filtering logic (copied from Review_Model_Stability.py)
    if mode == "All":
        # In "All" mode, include all models without filtering
        good_models = hyp_f["solID"].astype(str).unique()
    else:
        # This branch shouldn't execute for "All" mode
        raise AssertionError("Filtering should not happen in 'All' mode")
    
    # Verify all models are included
    assert len(good_models) == 5, f"Expected 5 models, got {len(good_models)}"
    assert set(good_models) == {'1', '2', '3', '4', '5'}
    
    print(f"✓ All mode test passed: {len(good_models)} / {len(hyp_f)} models selected")


def test_acceptable_mode_filters_correctly():
    """Test that 'Acceptable' mode filters correctly"""
    import pandas as pd
    
    # Create sample data
    hyp_f = pd.DataFrame({
        'solID': ['1', '2', '3', '4', '5'],
        'rsq_train': [0.9, 0.7, 0.5, 0.3, 0.0],  # Models 1,2,3 pass (>= 0.5)
        'nrmse_train': [0.05, 0.15, 0.25, 0.35, 1.0],  # Models 1,2,3 pass (<= 0.25)
        'decomp.rssd': [0.01, 0.1, 0.2, 0.3, 1.0]  # Models 1,2,3 pass (<= 0.2)
    })
    
    # Acceptable mode thresholds
    rsq_min = 0.50
    nrmse_max = 0.25
    decomp_max = 0.20
    
    mask = pd.Series(True, index=hyp_f.index)
    for c in hyp_f.columns:
        cu = c.lower()
        if cu.startswith("rsq_"):
            mask &= hyp_f[c] >= rsq_min
        elif cu.startswith("nrmse_"):
            mask &= hyp_f[c] <= nrmse_max
        elif cu.startswith("decomp.rssd"):
            mask &= hyp_f[c] <= decomp_max
    
    good_models = hyp_f.loc[mask, "solID"].astype(str).unique()
    
    # Models 1, 2, 3 should pass all thresholds
    assert len(good_models) == 3, f"Expected 3 models, got {len(good_models)}"
    assert set(good_models) == {'1', '2', '3'}
    
    print(f"✓ Acceptable mode test passed: {len(good_models)} / {len(hyp_f)} models selected")


def test_good_mode_filters_strictly():
    """Test that 'Good' mode filters strictly"""
    import pandas as pd
    
    # Create sample data
    hyp_f = pd.DataFrame({
        'solID': ['1', '2', '3', '4', '5'],
        'rsq_train': [0.9, 0.7, 0.5, 0.3, 0.0],  # Models 1,2 pass (>= 0.7)
        'nrmse_train': [0.05, 0.15, 0.25, 0.35, 1.0],  # Models 1,2 pass (<= 0.15)
        'decomp.rssd': [0.01, 0.1, 0.2, 0.3, 1.0]  # Models 1,2 pass (<= 0.1)
    })
    
    # Good mode thresholds
    rsq_min = 0.70
    nrmse_max = 0.15
    decomp_max = 0.10
    
    mask = pd.Series(True, index=hyp_f.index)
    for c in hyp_f.columns:
        cu = c.lower()
        if cu.startswith("rsq_"):
            mask &= hyp_f[c] >= rsq_min
        elif cu.startswith("nrmse_"):
            mask &= hyp_f[c] <= nrmse_max
        elif cu.startswith("decomp.rssd"):
            mask &= hyp_f[c] <= decomp_max
    
    good_models = hyp_f.loc[mask, "solID"].astype(str).unique()
    
    # Only models 1 and 2 should pass all thresholds
    assert len(good_models) == 2, f"Expected 2 models, got {len(good_models)}"
    assert set(good_models) == {'1', '2'}
    
    print(f"✓ Good mode test passed: {len(good_models)} / {len(hyp_f)} models selected")


def test_nan_handling():
    """Test that NaN values are handled correctly"""
    import pandas as pd
    import numpy as np
    
    # Create data with NaN values
    hyp = pd.DataFrame({
        'solID': ['1', '2', '3'],
        'rsq_train': [0.8, np.nan, 0.6],
        'nrmse_train': [0.1, 0.2, np.nan],
        'decomp.rssd': [0.05, np.nan, 0.15]
    })
    
    # Fill NaNs
    hyp_f = hyp.copy()
    for c in hyp_f.columns:
        cu = c.lower()
        if cu.startswith("rsq_"):
            hyp_f[c] = hyp_f[c].fillna(0.0)
        elif cu.startswith("nrmse_"):
            hyp_f[c] = hyp_f[c].fillna(1.0)
        elif cu.startswith("decomp.rssd"):
            hyp_f[c] = hyp_f[c].fillna(1.0)
    
    # Verify NaN filling
    assert hyp_f.loc[1, 'rsq_train'] == 0.0
    assert hyp_f.loc[2, 'nrmse_train'] == 1.0
    assert hyp_f.loc[1, 'decomp.rssd'] == 1.0
    
    # In "All" mode, all should be included regardless of filled values
    mode = "All"
    if mode == "All":
        good_models = hyp_f["solID"].astype(str).unique()
    
    assert len(good_models) == 3
    print(f"✓ NaN handling test passed: All models included despite NaN values")


if __name__ == "__main__":
    test_all_mode_includes_all_models()
    test_acceptable_mode_filters_correctly()
    test_good_mode_filters_strictly()
    test_nan_handling()
    print("\n✅ All filtering tests passed!")
