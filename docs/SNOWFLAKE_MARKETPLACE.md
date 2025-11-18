# Snowflake Marketplace Deployment Guide

This guide explains how to package and deploy the MMM Trainer application for Snowflake Marketplace as a Native App or through Snowpark Container Services.

## Overview

The MMM Trainer can be deployed in Snowflake environments in two ways:

1. **Snowpark Container Services** (Recommended for existing Snowflake customers)
2. **Snowflake Native App** (For Marketplace distribution)

## Prerequisites

- Snowflake account with ACCOUNTADMIN role
- Access to Snowflake Marketplace (for Native App deployment)
- Container registry access (for Snowpark Container Services)
- Google Cloud project (for building images)

## Option 1: Snowpark Container Services Deployment

Snowpark Container Services allows you to run containerized applications directly in Snowflake.

### Architecture for Snowpark Container Services

```
┌─────────────────────────────────────┐
│   Snowflake Account                 │
│                                     │
│  ┌──────────────────────────────┐  │
│  │  Snowpark Container Service  │  │
│  │                              │  │
│  │  ┌────────────────────────┐  │  │
│  │  │   MMM Web Service      │  │  │
│  │  │   (Streamlit UI)       │  │  │
│  │  └────────────────────────┘  │  │
│  │                              │  │
│  │  ┌────────────────────────┐  │  │
│  │  │   MMM Training Jobs    │  │  │
│  │  │   (R/Robyn)            │  │  │
│  │  └────────────────────────┘  │  │
│  └──────────────────────────────┘  │
│                                     │
│  ┌──────────────────────────────┐  │
│  │  Snowflake Tables/Stages     │  │
│  │  (Data storage)              │  │
│  └──────────────────────────────┘  │
└─────────────────────────────────────┘
```

### Step 1: Build and Push Images to Snowflake Registry

1. **Authenticate with Snowflake Image Repository:**

```bash
# Login to Snowflake
snow login

# Configure Docker to use Snowflake registry
# Format: <org>-<account>.registry.snowflakecomputing.com
export SF_REGISTRY="<org>-<account>.registry.snowflakecomputing.com"

# Login to Snowflake registry
docker login $SF_REGISTRY
```

2. **Build and tag images for Snowflake:**

```bash
# Set variables
export SF_REGISTRY="<org>-<account>.registry.snowflakecomputing.com"
export SF_DATABASE="MMM_APP"
export SF_SCHEMA="PUBLIC"

# Build web service image
docker build -t $SF_REGISTRY/$SF_DATABASE/$SF_SCHEMA/mmm-web:1.0.0 \
  -f docker/Dockerfile.web .

# Build training base image
docker build -t $SF_REGISTRY/$SF_DATABASE/$SF_SCHEMA/mmm-training-base:1.0.0 \
  -f docker/Dockerfile.training-base .

# Build training job image
docker build -t $SF_REGISTRY/$SF_DATABASE/$SF_SCHEMA/mmm-training:1.0.0 \
  --build-arg BASE_REF=$SF_REGISTRY/$SF_DATABASE/$SF_SCHEMA/mmm-training-base:1.0.0 \
  -f docker/Dockerfile.training .

# Push images
docker push $SF_REGISTRY/$SF_DATABASE/$SF_SCHEMA/mmm-web:1.0.0
docker push $SF_REGISTRY/$SF_DATABASE/$SF_SCHEMA/mmm-training-base:1.0.0
docker push $SF_REGISTRY/$SF_DATABASE/$SF_SCHEMA/mmm-training:1.0.0
```

### Step 2: Create Snowpark Container Services

1. **Create compute pool:**

```sql
-- Create compute pool for web service
CREATE COMPUTE POOL mmm_web_pool
  MIN_NODES = 1
  MAX_NODES = 3
  INSTANCE_FAMILY = STANDARD_2;

-- Create compute pool for training jobs
CREATE COMPUTE POOL mmm_training_pool
  MIN_NODES = 1
  MAX_NODES = 10
  INSTANCE_FAMILY = HIGHMEM_8;
```

2. **Create service specification file:**

Create `mmm_service_spec.yaml`:

```yaml
spec:
  containers:
  - name: web
    image: /<database>/<schema>/mmm-web:1.0.0
    env:
      PORT: "8080"
      STREAMLIT_SERVER_ADDRESS: "0.0.0.0"
    resources:
      requests:
        cpu: 2
        memory: 4Gi
      limits:
        cpu: 4
        memory: 8Gi
  endpoints:
  - name: web
    port: 8080
    public: true
```

3. **Create the service:**

```sql
-- Create stage for service spec
CREATE STAGE IF NOT EXISTS mmm_specs;

-- Upload service spec (do this via SnowSQL or UI)
PUT file://mmm_service_spec.yaml @mmm_specs AUTO_COMPRESS=FALSE OVERWRITE=TRUE;

-- Create the service
CREATE SERVICE mmm_web_service
  IN COMPUTE POOL mmm_web_pool
  FROM @mmm_specs
  SPEC = 'mmm_service_spec.yaml';

-- Check service status
SHOW SERVICES;
SELECT SYSTEM$GET_SERVICE_STATUS('mmm_web_service');
```

4. **Create job specification for training:**

Create `mmm_training_job_spec.yaml`:

```yaml
spec:
  containers:
  - name: training
    image: /<database>/<schema>/mmm-training:1.0.0
    resources:
      requests:
        cpu: 8
        memory: 32Gi
      limits:
        cpu: 32
        memory: 128Gi
```

### Step 3: Configure Data Access

1. **Grant necessary privileges:**

```sql
-- Grant access to data tables
GRANT USAGE ON DATABASE <source_db> TO SERVICE mmm_web_service;
GRANT USAGE ON SCHEMA <source_db>.<source_schema> TO SERVICE mmm_web_service;
GRANT SELECT ON ALL TABLES IN SCHEMA <source_db>.<source_schema> TO SERVICE mmm_web_service;

-- Grant access to result storage
GRANT READ, WRITE ON STAGE mmm_results TO SERVICE mmm_web_service;
```

### Step 4: Access the Application

```sql
-- Get service endpoint
SHOW ENDPOINTS IN SERVICE mmm_web_service;
```

The endpoint URL will be displayed, which you can access in your browser.

## Option 2: Snowflake Native App Deployment

Native Apps are the preferred method for distributing applications on Snowflake Marketplace.

### Architecture for Native App

```
┌─────────────────────────────────────────┐
│   Snowflake Marketplace                 │
│                                         │
│  ┌───────────────────────────────────┐  │
│  │     Native App Package            │  │
│  │                                   │  │
│  │  ┌─────────────────────────────┐  │  │
│  │  │  Application Objects        │  │  │
│  │  │  - Stored Procedures        │  │  │
│  │  │  - UDFs                     │  │  │
│  │  │  - Views                    │  │  │
│  │  └─────────────────────────────┘  │  │
│  │                                   │  │
│  │  ┌─────────────────────────────┐  │  │
│  │  │  Container Services         │  │  │
│  │  │  - Web UI (Streamlit)       │  │  │
│  │  │  - Training Jobs (R/Robyn)  │  │  │
│  │  └─────────────────────────────┘  │  │
│  └───────────────────────────────────┘  │
└─────────────────────────────────────────┘
```

### Step 1: Create Native App Structure

Create a `native_app/` directory structure:

```
native_app/
├── manifest.yml
├── setup.sql
├── README.md
├── icon.png
└── scripts/
    ├── setup_ui.sql
    └── setup_training.sql
```

### Step 2: Create Manifest File

Create `native_app/manifest.yml`:

```yaml
manifest_version: 1

version:
  name: "1.0.0"
  label: "MMM Trainer v1.0.0"
  comment: "Marketing Mix Modeling Trainer powered by R/Robyn"

artifacts:
  setup_script: setup.sql
  readme: README.md
  
  default_streamlit: streamlit_app.py
  
  extension_code: true

configuration:
  log_level: INFO
  trace_level: OFF

references:
  - snowflake_ml
  - snowpark_container_services

privileges:
  - EXECUTE TASK
  - EXECUTE MANAGED TASK
  - CREATE COMPUTE POOL
  - CREATE SERVICE
```

### Step 3: Create Setup Script

Create `native_app/setup.sql`:

```sql
-- Setup script for MMM Trainer Native App

-- Create application schema
CREATE APPLICATION ROLE IF NOT EXISTS app_admin;
CREATE APPLICATION ROLE IF NOT EXISTS app_user;

CREATE OR ALTER VERSIONED SCHEMA app_schema;
GRANT USAGE ON SCHEMA app_schema TO APPLICATION ROLE app_user;

-- Create compute pool for the app
CREATE COMPUTE POOL IF NOT EXISTS mmm_app_pool
  MIN_NODES = 1
  MAX_NODES = 5
  INSTANCE_FAMILY = STANDARD_2
  AUTO_RESUME = TRUE
  AUTO_SUSPEND_SECS = 3600;

GRANT USAGE ON COMPUTE POOL mmm_app_pool TO APPLICATION ROLE app_user;

-- Create stage for storing results
CREATE STAGE IF NOT EXISTS app_schema.mmm_results
  ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE');

GRANT READ, WRITE ON STAGE app_schema.mmm_results TO APPLICATION ROLE app_user;

-- Create service for web UI
CREATE SERVICE IF NOT EXISTS app_schema.web_service
  IN COMPUTE POOL mmm_app_pool
  FROM @app_schema.mmm_specs
  SPEC = 'mmm_service_spec.yaml';

GRANT USAGE ON SERVICE app_schema.web_service TO APPLICATION ROLE app_user;

-- Create stored procedure to launch training jobs
CREATE OR REPLACE PROCEDURE app_schema.launch_training_job(
  config_json VARCHAR
)
RETURNS VARCHAR
LANGUAGE SQL
AS
$$
BEGIN
  -- Create job execution
  EXECUTE IMMEDIATE 'EXECUTE JOB SERVICE app_schema.training_job USING (' || :config_json || ')';
  RETURN 'Training job launched successfully';
END;
$$;

GRANT USAGE ON PROCEDURE app_schema.launch_training_job(VARCHAR) TO APPLICATION ROLE app_user;

-- Create view for job history
CREATE OR REPLACE VIEW app_schema.job_history AS
  SELECT * FROM @app_schema.mmm_results/job_history.csv
  (FILE_FORMAT => 'CSV_FORMAT');

GRANT SELECT ON VIEW app_schema.job_history TO APPLICATION ROLE app_user;
```

### Step 4: Create Streamlit App for Native App

Create `native_app/streamlit_app.py`:

```python
"""
Streamlit entry point for MMM Trainer Native App
"""
import streamlit as st
from snowflake.snowpark.context import get_active_session

# Get Snowflake session
session = get_active_session()

st.title("MMM Trainer - Snowflake Native App")
st.caption("Marketing Mix Modeling powered by R/Robyn")

# Rest of the Streamlit app code...
# This would integrate with the existing app/streamlit_app.py
# but use Snowflake session instead of external connections
```

### Step 5: Package and Upload to Marketplace

1. **Create application package:**

```sql
-- Create application package
CREATE APPLICATION PACKAGE mmm_trainer_pkg;

-- Create stage for package files
CREATE STAGE mmm_trainer_pkg.stage_content
  ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE');

-- Upload package files
PUT file://native_app/manifest.yml @mmm_trainer_pkg.stage_content/manifest.yml AUTO_COMPRESS=FALSE;
PUT file://native_app/setup.sql @mmm_trainer_pkg.stage_content/setup.sql AUTO_COMPRESS=FALSE;
PUT file://native_app/README.md @mmm_trainer_pkg.stage_content/README.md AUTO_COMPRESS=FALSE;
PUT file://native_app/icon.png @mmm_trainer_pkg.stage_content/icon.png AUTO_COMPRESS=FALSE;

-- Create version
ALTER APPLICATION PACKAGE mmm_trainer_pkg
  ADD VERSION v1_0_0 USING '@mmm_trainer_pkg.stage_content';

-- Set default version
ALTER APPLICATION PACKAGE mmm_trainer_pkg
  SET DEFAULT RELEASE DIRECTIVE
  VERSION = v1_0_0
  PATCH = 0;
```

2. **Create listing for Marketplace:**

```sql
-- Create listing
CREATE LISTING mmm_trainer_listing FOR APPLICATION PACKAGE mmm_trainer_pkg;

-- Set listing properties
ALTER LISTING mmm_trainer_listing SET
  DEFAULT_RELEASE_DIRECTIVE.VERSION = v1_0_0,
  TITLE = 'MMM Trainer - Marketing Mix Modeling',
  SUBTITLE = 'Advanced MMM powered by R/Robyn',
  DESCRIPTION = 'A comprehensive Marketing Mix Modeling solution...',
  PROVIDER = '<your_organization>';
```

## Configuration for Snowflake Environment

### Environment Variables

When deploying in Snowflake, adjust these configurations:

1. **Data Connection**: Instead of external Snowflake connection, use internal session
2. **Storage**: Use Snowflake stages instead of GCS
3. **Authentication**: Use Snowflake roles instead of Google OAuth

### Adaptation Requirements

The following files need Snowflake-specific versions:

1. `app/config/settings.py` - Add Snowflake-native configuration
2. `app/utils/snowflake_connector.py` - Use `get_active_session()` for Native Apps
3. `app/utils/gcs_utils.py` - Replace with Snowflake stage operations
4. `app_shared.py` - Adapt job execution to use Snowflake jobs

### Create Snowflake-Specific Configuration

Add to `app/config/settings.py`:

```python
import os

# Detect if running in Snowflake environment
IS_SNOWFLAKE_NATIVE = os.getenv("SNOWFLAKE_ACCOUNT") is not None

if IS_SNOWFLAKE_NATIVE:
    # Use Snowflake stages for storage
    STORAGE_TYPE = "snowflake_stage"
    RESULTS_STAGE = "@app_schema.mmm_results"
    
    # Use Snowflake session instead of external connection
    USE_SNOWFLAKE_SESSION = True
else:
    # Use GCS for storage (Cloud Run deployment)
    STORAGE_TYPE = "gcs"
    GCS_BUCKET = os.getenv("GCS_BUCKET", "mmm-app-output")
    
    # Use external Snowflake connection
    USE_SNOWFLAKE_SESSION = False
```

## Testing the Deployment

### For Snowpark Container Services

```sql
-- Test service health
SELECT SYSTEM$GET_SERVICE_STATUS('mmm_web_service');

-- Test job execution
CALL launch_training_job('{"country": "US", "preset": "Test run"}');

-- View logs
SELECT * FROM TABLE(mmm_web_service!LOGS());
```

### For Native App

```sql
-- Install app in test account
CREATE APPLICATION mmm_trainer_test
  FROM APPLICATION PACKAGE mmm_trainer_pkg
  USING VERSION v1_0_0;

-- Grant privileges
GRANT APPLICATION ROLE mmm_trainer_test.app_user TO ROLE sysadmin;

-- Test the app
USE APPLICATION mmm_trainer_test;
SHOW SERVICES;
```

## Cost Optimization

1. **Compute Pools**: Set appropriate AUTO_SUSPEND_SECS to minimize idle costs
2. **Instance Families**: Use STANDARD for web UI, HIGHMEM for training
3. **Scaling**: Configure MIN_NODES=1 and scale up based on demand
4. **Storage**: Use Snowflake stages for efficient data access

## Security Considerations

1. **Data Access**: Grant only necessary privileges to the application
2. **Network Policy**: Restrict service access to authorized networks
3. **Encryption**: All data encrypted at rest using Snowflake SSE
4. **Audit**: Enable audit logging for application usage

## Support and Documentation

For detailed Snowflake documentation:
- [Snowpark Container Services](https://docs.snowflake.com/en/developer-guide/snowpark-container-services/overview)
- [Native Apps](https://docs.snowflake.com/en/developer-guide/native-apps/native-apps-about)
- [Snowflake Marketplace](https://docs.snowflake.com/en/user-guide/data-marketplace)

## Troubleshooting

### Common Issues

**Service fails to start:**
- Check compute pool status: `SHOW COMPUTE POOLS;`
- Verify image exists in registry: `SHOW IMAGES IN IMAGE REPOSITORY;`
- Check service logs: `SELECT * FROM TABLE(<service_name>!LOGS());`

**Permission errors:**
- Verify application roles: `SHOW APPLICATION ROLES;`
- Check granted privileges: `SHOW GRANTS TO APPLICATION ROLE app_user;`

**Performance issues:**
- Scale compute pool: `ALTER COMPUTE POOL ... SET MAX_NODES = 10;`
- Check resource utilization: `SHOW SERVICES;`

## Migration from GCP to Snowflake

To migrate an existing GCP deployment to Snowflake:

1. Export configuration from Cloud Run
2. Adapt storage paths from GCS to Snowflake stages
3. Migrate secrets from Secret Manager to Snowflake secrets
4. Update connection logic to use Snowflake session
5. Test thoroughly in Snowflake environment
6. Deploy as Native App or Container Service

## Next Steps

1. Review and customize the native app structure for your needs
2. Build and test images in Snowflake registry
3. Create application package and test installation
4. Submit to Snowflake Marketplace for review
5. Monitor usage and gather user feedback
