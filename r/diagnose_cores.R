#!/usr/bin/env Rscript

#' Core Allocation Diagnostic Script
#' 
#' This script investigates why Cloud Run may be limiting actual core availability
#' despite requesting more vCPUs. It collects system information about CPU allocation,
#' cgroups quotas, and other factors that might affect parallel processing.

cat("\n")
cat("════════════════════════════════════════════════════════════════════════\n")
cat("🔍 CLOUD RUN CORE ALLOCATION DIAGNOSTIC\n")
cat("════════════════════════════════════════════════════════════════════════\n\n")

# ---------- 1. ENVIRONMENT VARIABLES ----------
cat("📋 Step 1: Environment Variables\n")
cat("─────────────────────────────────────────────────────────────────────\n")

env_vars <- c(
    "R_MAX_CORES",
    "OMP_NUM_THREADS", 
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "K_SERVICE",
    "K_REVISION",
    "K_CONFIGURATION"
)

for (var in env_vars) {
    val <- Sys.getenv(var, unset = NA)
    if (!is.na(val)) {
        cat(sprintf("  ✓ %-25s = %s\n", var, val))
    } else {
        cat(sprintf("  ✗ %-25s = <not set>\n", var))
    }
}

# ---------- 2. R CORE DETECTION ----------
cat("\n📋 Step 2: R Core Detection Methods\n")
cat("─────────────────────────────────────────────────────────────────────\n")

# parallel::detectCores
detect_cores <- tryCatch(
    parallel::detectCores(),
    error = function(e) NA
)
cat(sprintf("  parallel::detectCores()           = %s\n", 
            ifelse(is.na(detect_cores), "ERROR", detect_cores)))

# parallel::detectCores with logical = FALSE
detect_cores_phys <- tryCatch(
    parallel::detectCores(logical = FALSE),
    error = function(e) NA
)
cat(sprintf("  parallel::detectCores(logical=F)  = %s (physical)\n", 
            ifelse(is.na(detect_cores_phys), "ERROR", detect_cores_phys)))

# parallelly::availableCores
avail_cores <- tryCatch(
    parallelly::availableCores(),
    error = function(e) NA
)
cat(sprintf("  parallelly::availableCores()      = %s (cgroup-aware)\n", 
            ifelse(is.na(avail_cores), "ERROR", avail_cores)))

# ---------- 3. LINUX CGROUPS INVESTIGATION ----------
cat("\n📋 Step 3: Linux Cgroups CPU Quota\n")
cat("─────────────────────────────────────────────────────────────────────\n")

check_cgroup_file <- function(path, description) {
    if (file.exists(path)) {
        tryCatch({
            val <- readLines(path, n = 1, warn = FALSE)
            cat(sprintf("  ✓ %-40s = %s\n", description, val))
            return(as.numeric(val))
        }, error = function(e) {
            cat(sprintf("  ✗ %-40s = ERROR: %s\n", description, conditionMessage(e)))
            return(NA)
        })
    } else {
        cat(sprintf("  ✗ %-40s = <file not found>\n", description))
        return(NA)
    }
}

# cgroups v1 CPU quota
quota_v1 <- check_cgroup_file(
    "/sys/fs/cgroup/cpu/cpu.cfs_quota_us",
    "cpu.cfs_quota_us (v1)"
)
period_v1 <- check_cgroup_file(
    "/sys/fs/cgroup/cpu/cpu.cfs_period_us", 
    "cpu.cfs_period_us (v1)"
)

# cgroups v2 CPU max
if (file.exists("/sys/fs/cgroup/cpu.max")) {
    tryCatch({
        val <- readLines("/sys/fs/cgroup/cpu.max", n = 1, warn = FALSE)
        cat(sprintf("  ✓ %-40s = %s\n", "cpu.max (v2)", val))
        parts <- strsplit(val, " ")[[1]]
        if (length(parts) >= 2 && parts[1] != "max") {
            quota_v2 <- as.numeric(parts[1])
            period_v2 <- as.numeric(parts[2])
        } else {
            quota_v2 <- NA
            period_v2 <- NA
        }
    }, error = function(e) {
        cat(sprintf("  ✗ %-40s = ERROR: %s\n", "cpu.max (v2)", conditionMessage(e)))
        quota_v2 <- NA
        period_v2 <- NA
    })
} else {
    cat(sprintf("  ✗ %-40s = <file not found>\n", "cpu.max (v2)"))
    quota_v2 <- NA
    period_v2 <- NA
}

# Calculate effective CPU quota
cat("\n  📊 Calculated CPU Quota:\n")
if (!is.na(quota_v1) && !is.na(period_v1) && quota_v1 > 0) {
    effective_cpus_v1 <- quota_v1 / period_v1
    cat(sprintf("     cgroups v1: %.2f CPUs (quota=%d / period=%d)\n", 
                effective_cpus_v1, quota_v1, period_v1))
} else if (!is.na(quota_v1) && quota_v1 == -1) {
    cat(sprintf("     cgroups v1: UNLIMITED (quota=-1)\n"))
}

if (!is.na(quota_v2) && !is.na(period_v2)) {
    effective_cpus_v2 <- quota_v2 / period_v2
    cat(sprintf("     cgroups v2: %.2f CPUs (quota=%d / period=%d)\n", 
                effective_cpus_v2, quota_v2, period_v2))
}

# ---------- 4. SYSTEM CPU INFO ----------
cat("\n📋 Step 4: System CPU Information\n")
cat("─────────────────────────────────────────────────────────────────────\n")

# /proc/cpuinfo
if (file.exists("/proc/cpuinfo")) {
    tryCatch({
        cpuinfo <- readLines("/proc/cpuinfo", warn = FALSE)
        processor_lines <- grep("^processor", cpuinfo, value = TRUE)
        cat(sprintf("  /proc/cpuinfo processors     = %d\n", length(processor_lines)))
        
        # Get model name
        model_line <- grep("^model name", cpuinfo, value = TRUE)[1]
        if (!is.na(model_line)) {
            model <- sub("^model name\\s*:\\s*", "", model_line)
            cat(sprintf("  CPU model                    = %s\n", model))
        }
    }, error = function(e) {
        cat(sprintf("  ERROR reading /proc/cpuinfo: %s\n", conditionMessage(e)))
    })
}

# nproc command
nproc_result <- tryCatch(
    system("nproc", intern = TRUE, ignore.stderr = TRUE),
    error = function(e) NA
)
if (!is.na(nproc_result)) {
    cat(sprintf("  nproc (shell command)        = %s\n", nproc_result))
}

# ---------- 5. MEMORY INFORMATION ----------
cat("\n📋 Step 5: Memory Information\n")
cat("─────────────────────────────────────────────────────────────────────\n")

# Memory limit from cgroups
mem_limit_v1 <- check_cgroup_file(
    "/sys/fs/cgroup/memory/memory.limit_in_bytes",
    "memory.limit_in_bytes (v1)"
)
if (!is.na(mem_limit_v1)) {
    mem_gb <- mem_limit_v1 / (1024^3)
    cat(sprintf("     = %.2f GB\n", mem_gb))
}

# cgroups v2 memory max
if (file.exists("/sys/fs/cgroup/memory.max")) {
    tryCatch({
        val <- readLines("/sys/fs/cgroup/memory.max", n = 1, warn = FALSE)
        cat(sprintf("  ✓ %-40s = %s\n", "memory.max (v2)", val))
        if (val != "max") {
            mem_gb_v2 <- as.numeric(val) / (1024^3)
            cat(sprintf("     = %.2f GB\n", mem_gb_v2))
        }
    }, error = function(e) {
        cat(sprintf("  ✗ memory.max (v2) ERROR: %s\n", conditionMessage(e)))
    })
}

# ---------- 6. FUTURE PACKAGE DIAGNOSTICS ----------
cat("\n📋 Step 6: Future Package Configuration\n")
cat("─────────────────────────────────────────────────────────────────────\n")

if (requireNamespace("future", quietly = TRUE)) {
    # Current plan
    current_plan <- class(future::plan())[1]
    cat(sprintf("  Current future plan          = %s\n", current_plan))
    
    # Available workers
    avail_workers <- tryCatch(
        future::availableWorkers(),
        error = function(e) NA
    )
    if (!is.na(avail_workers)) {
        cat(sprintf("  Available workers            = %d\n", length(avail_workers)))
    }
    
    # Number of workers
    nb_workers <- tryCatch(
        future::nbrOfWorkers(),
        error = function(e) NA
    )
    if (!is.na(nb_workers)) {
        cat(sprintf("  Current workers              = %d\n", nb_workers))
    }
} else {
    cat("  ✗ future package not available\n")
}

# ---------- 7. PROCESS LIMITS ----------
cat("\n📋 Step 7: Process Limits (ulimit)\n")
cat("─────────────────────────────────────────────────────────────────────\n")

ulimit_result <- tryCatch(
    system("ulimit -a", intern = TRUE, ignore.stderr = TRUE),
    error = function(e) NULL
)
if (!is.null(ulimit_result) && length(ulimit_result) > 0) {
    # Show key limits
    cpu_line <- grep("cpu time", ulimit_result, value = TRUE, ignore.case = TRUE)
    procs_line <- grep("max user processes", ulimit_result, value = TRUE, ignore.case = TRUE)
    
    if (length(cpu_line) > 0) cat(sprintf("  %s\n", cpu_line[1]))
    if (length(procs_line) > 0) cat(sprintf("  %s\n", procs_line[1]))
} else {
    cat("  ✗ Unable to retrieve ulimit information\n")
}

# ---------- 8. CLOUD RUN SPECIFIC ----------
cat("\n📋 Step 8: Cloud Run Specific Information\n")
cat("─────────────────────────────────────────────────────────────────────\n")

# Check if running in Cloud Run
is_cloud_run <- Sys.getenv("K_SERVICE", unset = "") != ""
cat(sprintf("  Running in Cloud Run         = %s\n", ifelse(is_cloud_run, "YES", "NO")))

if (is_cloud_run) {
    cat(sprintf("  Service name                 = %s\n", Sys.getenv("K_SERVICE", "<unknown>")))
    cat(sprintf("  Revision                     = %s\n", Sys.getenv("K_REVISION", "<unknown>")))
    cat(sprintf("  Configuration                = %s\n", Sys.getenv("K_CONFIGURATION", "<unknown>")))
}

# Check for CPU throttling
if (file.exists("/sys/fs/cgroup/cpu/cpu.stat")) {
    tryCatch({
        cpu_stat <- readLines("/sys/fs/cgroup/cpu/cpu.stat", warn = FALSE)
        throttled_line <- grep("^nr_throttled", cpu_stat, value = TRUE)
        throttled_time_line <- grep("^throttled_time", cpu_stat, value = TRUE)
        
        if (length(throttled_line) > 0) {
            cat(sprintf("  %-30s = %s\n", "CPU throttle events", 
                        sub("^nr_throttled\\s+", "", throttled_line)))
        }
        if (length(throttled_time_line) > 0) {
            throttled_ns <- as.numeric(sub("^throttled_time\\s+", "", throttled_time_line))
            throttled_sec <- throttled_ns / 1e9
            cat(sprintf("  %-30s = %.2f seconds\n", "CPU throttled time", throttled_sec))
        }
    }, error = function(e) {
        cat(sprintf("  ✗ Unable to read CPU throttling stats: %s\n", conditionMessage(e)))
    })
}

# ---------- 9. RECOMMENDATIONS ----------
cat("\n📋 Step 9: Analysis & Recommendations\n")
cat("─────────────────────────────────────────────────────────────────────\n")

# Analyze the data
requested <- as.numeric(Sys.getenv("R_MAX_CORES", "0"))
detected <- if (!is.na(detect_cores)) detect_cores else 0
available <- if (!is.na(avail_cores)) avail_cores else 0

cat(sprintf("\n  Summary:\n"))
cat(sprintf("    • Requested cores (R_MAX_CORES):    %d\n", requested))
cat(sprintf("    • System CPUs (detectCores):        %d\n", detected))
cat(sprintf("    • Available (cgroup-aware):         %d\n", available))

if (available < requested) {
    cat(sprintf("\n  ⚠️  ISSUE DETECTED: Available cores (%d) < Requested (%d)\n", 
                available, requested))
    
    # Diagnose the cause
    if (!is.na(quota_v1) && !is.na(period_v1) && quota_v1 > 0) {
        effective <- quota_v1 / period_v1
        cat(sprintf("\n  🔍 Root Cause: cgroups v1 CPU quota\n"))
        cat(sprintf("     • Quota: %d microseconds\n", quota_v1))
        cat(sprintf("     • Period: %d microseconds\n", period_v1))
        cat(sprintf("     • Effective CPUs: %.2f\n", effective))
        cat(sprintf("\n  💡 Solution: Update Cloud Run job configuration:\n"))
        cat(sprintf("     Set training_max_cores to %d in Terraform\n", floor(effective)))
    } else if (!is.na(quota_v2) && !is.na(period_v2)) {
        effective <- quota_v2 / period_v2
        cat(sprintf("\n  🔍 Root Cause: cgroups v2 CPU quota\n"))
        cat(sprintf("     • Quota: %d microseconds\n", quota_v2))
        cat(sprintf("     • Period: %d microseconds\n", period_v2))
        cat(sprintf("     • Effective CPUs: %.2f\n", effective))
        cat(sprintf("\n  💡 Solution: Update Cloud Run job configuration:\n"))
        cat(sprintf("     Set training_max_cores to %d in Terraform\n", floor(effective)))
    } else {
        cat(sprintf("\n  🔍 Root Cause: Unknown - cgroups quota appears unlimited\n"))
        cat(sprintf("\n  💡 Possible causes:\n"))
        cat(sprintf("     1. Cloud Run cold start - cores may increase after warm-up\n"))
        cat(sprintf("     2. Resource contention on host node\n"))
        cat(sprintf("     3. Cloud Run internal throttling\n"))
        cat(sprintf("\n  💡 Solutions to try:\n"))
        cat(sprintf("     1. Wait 30-60 seconds after start and re-check core count\n"))
        cat(sprintf("     2. Use smaller vCPU configuration (4 vCPU instead of 8)\n"))
        cat(sprintf("     3. Enable CPU boost if available\n"))
    }
} else {
    cat(sprintf("\n  ✅ Core allocation looks good!\n"))
}

cat("\n")
cat("════════════════════════════════════════════════════════════════════════\n")
cat("📝 Diagnostic Complete\n")
cat("════════════════════════════════════════════════════════════════════════\n\n")

# Return diagnostic data as invisible list
invisible(list(
    requested_cores = requested,
    detected_cores = detect_cores,
    available_cores = avail_cores,
    cgroup_quota_v1 = quota_v1,
    cgroup_period_v1 = period_v1,
    cgroup_quota_v2 = quota_v2,
    cgroup_period_v2 = period_v2,
    is_cloud_run = is_cloud_run
))
