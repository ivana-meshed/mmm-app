"""
Tests for Review Model Stability helper functions.

Tests the run_has_required_files function to ensure it correctly validates
that model runs have all required output files.
"""

from unittest.mock import Mock


# Copy the function here for standalone testing
FILE_XAGG = "xDecompAgg.parquet"
FILE_HYP = "resultHypParam.parquet"
FILE_MEDIA = "mediaVecCollect.parquet"
FILE_XVEC = "xDecompVecCollect.parquet"


def run_has_required_files(run_blobs, required_files=None):
    """
    Check if a run has all required model output files.
    
    Args:
        run_blobs: List of blobs for a specific run
        required_files: List of required filenames. Defaults to the standard Robyn output files.
    
    Returns:
        bool: True if all required files are present, False otherwise
    """
    if required_files is None:
        required_files = [FILE_XAGG, FILE_HYP, FILE_MEDIA, FILE_XVEC]
    
    # Extract filenames from blob paths
    blob_files = set()
    for blob in run_blobs:
        # Extract filename from path (e.g., "output_models_data/xDecompAgg.parquet" -> "xDecompAgg.parquet")
        parts = blob.name.split("/")
        if len(parts) >= 2 and parts[-2] == "output_models_data":
            blob_files.add(parts[-1])
    
    # Check if all required files are present
    return all(f in blob_files for f in required_files)


def test_run_has_required_files_complete():
    """Test run_has_required_files with complete set of files"""
    # Mock blobs with all required files
    blobs = []
    for fname in ["xDecompAgg.parquet", "resultHypParam.parquet", "mediaVecCollect.parquet", "xDecompVecCollect.parquet"]:
        blob = Mock()
        blob.name = f"robyn/default/de/0213_162352/output_models_data/{fname}"
        blobs.append(blob)
    
    # Add extra file
    extra_blob = Mock()
    extra_blob.name = "robyn/default/de/0213_162352/some_other_file.txt"
    blobs.append(extra_blob)
    
    result = run_has_required_files(blobs)
    assert result is True, "Should return True when all required files are present"


def test_run_has_required_files_missing_one():
    """Test run_has_required_files when one file is missing"""
    # Missing xDecompVecCollect.parquet
    blobs = []
    for fname in ["xDecompAgg.parquet", "resultHypParam.parquet", "mediaVecCollect.parquet"]:
        blob = Mock()
        blob.name = f"robyn/default/de/0213_162352/output_models_data/{fname}"
        blobs.append(blob)
    
    result = run_has_required_files(blobs)
    assert result is False, "Should return False when required files are missing"


def test_run_has_required_files_empty():
    """Test run_has_required_files with empty blob list"""
    result = run_has_required_files([])
    assert result is False, "Should return False for empty blob list"


def test_run_has_required_files_wrong_directory():
    """Test run_has_required_files when files are in wrong directory"""
    # Files not in output_models_data directory
    blobs = []
    for fname in ["xDecompAgg.parquet", "resultHypParam.parquet", "mediaVecCollect.parquet", "xDecompVecCollect.parquet"]:
        blob = Mock()
        blob.name = f"robyn/default/de/0213_162352/{fname}"
        blobs.append(blob)
    
    result = run_has_required_files(blobs)
    assert result is False, "Should return False when files are not in output_models_data/"


def test_run_has_required_files_custom_requirements():
    """Test run_has_required_files with custom required files list"""
    blobs = []
    for fname in ["file1.txt", "file2.txt"]:
        blob = Mock()
        blob.name = f"robyn/default/de/0213_162352/output_models_data/{fname}"
        blobs.append(blob)
    
    # Should pass with these custom requirements
    result = run_has_required_files(blobs, required_files=["file1.txt", "file2.txt"])
    assert result is True
    
    # Should fail if we require a file that's not present
    result = run_has_required_files(blobs, required_files=["file1.txt", "file3.txt"])
    assert result is False


if __name__ == "__main__":
    # Run tests
    test_run_has_required_files_complete()
    print("✓ test_run_has_required_files_complete passed")

    test_run_has_required_files_missing_one()
    print("✓ test_run_has_required_files_missing_one passed")

    test_run_has_required_files_empty()
    print("✓ test_run_has_required_files_empty passed")

    test_run_has_required_files_wrong_directory()
    print("✓ test_run_has_required_files_wrong_directory passed")

    test_run_has_required_files_custom_requirements()
    print("✓ test_run_has_required_files_custom_requirements passed")

    print("\nAll tests passed!")
