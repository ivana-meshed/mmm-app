# Snowflake Native App Package Structure

This directory contains the files needed to package MMM Trainer as a Snowflake Native App.

## Directory Structure

```
native_app/
├── manifest.yml                  # App manifest (version, config, privileges)
├── setup.sql                     # App installation script
├── README.md                     # User-facing documentation
├── streamlit_native_app.py       # Streamlit UI for native app
└── scripts/
    ├── setup_ui.sql             # Web service setup
    └── setup_training.sql       # Training job setup
```

## Files Overview

### manifest.yml

Defines the application metadata, version, and required privileges. Key sections:
- `version`: App version info
- `artifacts`: References to setup scripts and UI
- `privileges`: Required Snowflake privileges (compute pools, services, etc.)

### setup.sql

Main setup script that runs during app installation. Creates:
- Application roles (`app_admin`, `app_user`)
- Compute pools for web and training workloads
- Stages for specs and results
- Tables and views for job management
- Stored procedures for launching jobs

### README.md

User-facing documentation that appears in Snowflake Marketplace. Includes:
- Feature overview
- Quick start guide
- Data requirements
- Usage instructions
- Pricing and support info

### streamlit_native_app.py

Streamlit application adapted for Snowflake Native Apps:
- Uses `get_active_session()` instead of external connections
- Reads/writes to Snowflake stages instead of GCS
- Integrates with Snowflake job tables and views
- Provides simplified UI for common tasks

### scripts/

Helper scripts for setting up specific components:
- `setup_ui.sql`: Creates web service with Streamlit UI
- `setup_training.sql`: Sets up training job template

## Building the App Package

### Step 1: Prepare Images

First, push Docker images to Snowflake's image repository:

```bash
# Set your Snowflake account info
export SF_ACCOUNT="<org>-<account>"
export SF_REGISTRY="${SF_ACCOUNT}.registry.snowflakecomputing.com"

# Login to Snowflake registry
docker login $SF_REGISTRY

# Tag and push images
docker tag mmm-web:latest $SF_REGISTRY/mmm_app/public/mmm-web:1.0.0
docker tag mmm-training:latest $SF_REGISTRY/mmm_app/public/mmm-training:1.0.0

docker push $SF_REGISTRY/mmm_app/public/mmm-web:1.0.0
docker push $SF_REGISTRY/mmm_app/public/mmm-training:1.0.0
```

### Step 2: Create Application Package

```sql
-- Create application package
CREATE APPLICATION PACKAGE mmm_trainer_pkg;

-- Create stage for package content
CREATE STAGE mmm_trainer_pkg.stage_content
  ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE');

-- Upload package files
-- (Use SnowSQL or Snowflake UI to upload files from native_app/ directory)
PUT file://manifest.yml @mmm_trainer_pkg.stage_content/manifest.yml;
PUT file://setup.sql @mmm_trainer_pkg.stage_content/setup.sql;
PUT file://README.md @mmm_trainer_pkg.stage_content/README.md;
PUT file://streamlit_native_app.py @mmm_trainer_pkg.stage_content/streamlit_native_app.py;
PUT file://scripts/setup_ui.sql @mmm_trainer_pkg.stage_content/scripts/setup_ui.sql;
PUT file://scripts/setup_training.sql @mmm_trainer_pkg.stage_content/scripts/setup_training.sql;
```

### Step 3: Create Version

```sql
-- Create version from uploaded files
ALTER APPLICATION PACKAGE mmm_trainer_pkg
  ADD VERSION v1_0_0 USING '@mmm_trainer_pkg.stage_content';

-- Set as default version
ALTER APPLICATION PACKAGE mmm_trainer_pkg
  SET DEFAULT RELEASE DIRECTIVE
  VERSION = v1_0_0
  PATCH = 0;
```

### Step 4: Test Installation

```sql
-- Install in your account for testing
CREATE APPLICATION mmm_trainer_test
  FROM APPLICATION PACKAGE mmm_trainer_pkg
  USING VERSION v1_0_0;

-- Grant app role to your role
GRANT APPLICATION ROLE mmm_trainer_test.app_user TO ROLE sysadmin;

-- Test the app
USE APPLICATION mmm_trainer_test;
SHOW SERVICES;
SHOW ENDPOINTS IN SERVICE app_schema.web_service;
```

### Step 5: Publish to Marketplace (Optional)

```sql
-- Create listing for Marketplace
CREATE LISTING mmm_trainer_listing FOR APPLICATION PACKAGE mmm_trainer_pkg;

-- Configure listing details
ALTER LISTING mmm_trainer_listing SET
  DEFAULT_RELEASE_DIRECTIVE.VERSION = v1_0_0,
  TITLE = 'MMM Trainer - Marketing Mix Modeling',
  SUBTITLE = 'Advanced MMM powered by R/Robyn',
  DESCRIPTION = 'A comprehensive Marketing Mix Modeling solution that runs directly in Snowflake. Leverage Meta''s R/Robyn framework to analyze marketing effectiveness and optimize budget allocation.',
  PROVIDER = '<your_organization>',
  CATEGORIES = ('Analytics', 'Marketing'),
  DOCUMENTATION = 'https://github.com/ivana-meshed/mmm-app';

-- Publish listing (requires Marketplace provider agreement)
ALTER LISTING mmm_trainer_listing PUBLISH;
```

## Updating the App

When releasing a new version:

1. Update VERSION file in repository root
2. Update version in `manifest.yml`
3. Update changelog in `README.md`
4. Build and push new Docker images with version tag
5. Upload updated files to application package
6. Create new version in package
7. Test thoroughly before publishing

Example:

```sql
-- Upload updated files
PUT file://manifest.yml @mmm_trainer_pkg.stage_content/manifest.yml OVERWRITE=TRUE;
PUT file://setup.sql @mmm_trainer_pkg.stage_content/setup.sql OVERWRITE=TRUE;

-- Add new version
ALTER APPLICATION PACKAGE mmm_trainer_pkg
  ADD VERSION v1_1_0 USING '@mmm_trainer_pkg.stage_content';

-- Update default
ALTER APPLICATION PACKAGE mmm_trainer_pkg
  SET DEFAULT RELEASE DIRECTIVE
  VERSION = v1_1_0
  PATCH = 0;
```

## Testing Checklist

Before publishing to Marketplace:

- [ ] All Docker images pushed to Snowflake registry
- [ ] Application installs without errors
- [ ] All compute pools create successfully
- [ ] Web service starts and is accessible
- [ ] Streamlit UI loads and is functional
- [ ] Job launch procedure works
- [ ] Job history views return data correctly
- [ ] Application can access user data (with granted permissions)
- [ ] Auto-suspend works for compute pools
- [ ] Documentation is complete and accurate
- [ ] Screenshots and demo data prepared

## Troubleshooting

**Application fails to install:**
- Check setup.sql for syntax errors
- Verify manifest.yml is valid YAML
- Ensure all referenced files are uploaded

**Service won't start:**
- Verify images exist in registry
- Check compute pool status
- Review service logs: `SELECT * FROM TABLE(<service>!LOGS())`

**Permission errors:**
- Verify required privileges in manifest.yml
- Check application role grants
- Ensure data access permissions granted by user

## Additional Resources

- [Snowflake Native Apps Documentation](https://docs.snowflake.com/en/developer-guide/native-apps/native-apps-about)
- [Snowpark Container Services](https://docs.snowflake.com/en/developer-guide/snowpark-container-services/overview)
- [Streamlit in Snowflake](https://docs.snowflake.com/en/developer-guide/streamlit/about-streamlit)
