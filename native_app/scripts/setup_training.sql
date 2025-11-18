-- Helper script to set up training job service for MMM Trainer Native App

-- Create job specification
CREATE OR REPLACE TEMPORARY TABLE job_spec_content (content VARCHAR);

INSERT INTO job_spec_content VALUES ('spec:
  containers:
  - name: training
    image: /mmm_app/public/mmm-training:1.0.0
    env:
      SNOWFLAKE_ACCOUNT: "{{ SNOWFLAKE_ACCOUNT }}"
    resources:
      requests:
        cpu: 8
        memory: 32Gi
      limits:
        cpu: 32
        memory: 128Gi');

-- Write spec to stage
COPY INTO @app_schema.mmm_specs/mmm_training_spec.yaml
FROM job_spec_content
FILE_FORMAT = (FORMAT_NAME = app_schema.yaml_format)
SINGLE = TRUE
OVERWRITE = TRUE;

-- Create training job service (this will be used as a template for job execution)
CREATE SERVICE IF NOT EXISTS app_schema.training_job_template
  IN COMPUTE POOL mmm_training_pool
  FROM @app_schema.mmm_specs
  SPEC = 'mmm_training_spec.yaml'
  QUERY_WAREHOUSE = NULL;  -- Jobs don't need a warehouse

-- Grant access
GRANT USAGE ON SERVICE app_schema.training_job_template TO APPLICATION ROLE app_user;

SELECT 'Training job template created successfully.' AS message;
