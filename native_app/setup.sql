-- Setup script for MMM Trainer Native App
-- This script runs when the application is installed

-- Create application roles
CREATE APPLICATION ROLE IF NOT EXISTS app_admin;
CREATE APPLICATION ROLE IF NOT EXISTS app_user;

-- Create versioned schema for app objects
CREATE OR ALTER VERSIONED SCHEMA app_schema;
GRANT USAGE ON SCHEMA app_schema TO APPLICATION ROLE app_user;

-- Create compute pool for web service
CREATE COMPUTE POOL IF NOT EXISTS mmm_web_pool
  MIN_NODES = 1
  MAX_NODES = 3
  INSTANCE_FAMILY = STANDARD_2
  AUTO_RESUME = TRUE
  AUTO_SUSPEND_SECS = 3600;

GRANT USAGE ON COMPUTE POOL mmm_web_pool TO APPLICATION ROLE app_user;

-- Create compute pool for training jobs
CREATE COMPUTE POOL IF NOT EXISTS mmm_training_pool
  MIN_NODES = 1
  MAX_NODES = 10
  INSTANCE_FAMILY = HIGHMEM_8
  AUTO_RESUME = TRUE
  AUTO_SUSPEND_SECS = 1800;

GRANT USAGE ON COMPUTE POOL mmm_training_pool TO APPLICATION ROLE app_user;

-- Create stage for service specifications
CREATE STAGE IF NOT EXISTS app_schema.mmm_specs
  ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE');

-- Create stage for storing results
CREATE STAGE IF NOT EXISTS app_schema.mmm_results
  ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE');

GRANT READ, WRITE ON STAGE app_schema.mmm_results TO APPLICATION ROLE app_user;

-- Create table for job history
CREATE TABLE IF NOT EXISTS app_schema.job_history (
  job_id VARCHAR(255),
  country VARCHAR(100),
  status VARCHAR(50),
  created_at TIMESTAMP_NTZ,
  started_at TIMESTAMP_NTZ,
  completed_at TIMESTAMP_NTZ,
  config_path VARCHAR(500),
  result_path VARCHAR(500),
  error_message VARCHAR(5000)
);

GRANT SELECT, INSERT, UPDATE ON TABLE app_schema.job_history TO APPLICATION ROLE app_user;

-- Create stored procedure to launch training jobs
CREATE OR REPLACE PROCEDURE app_schema.launch_training_job(
  config_json VARCHAR
)
RETURNS VARCHAR
LANGUAGE SQL
AS
$$
DECLARE
  job_id VARCHAR;
BEGIN
  -- Generate unique job ID
  job_id := CONCAT('job_', TO_VARCHAR(SYSTIMESTAMP(), 'YYYYMMDDHH24MISSFF'));
  
  -- Insert job record
  INSERT INTO app_schema.job_history (job_id, status, created_at, config_path)
  VALUES (:job_id, 'PENDING', CURRENT_TIMESTAMP(), :config_json);
  
  -- TODO: Launch actual training job via Snowpark Container Services
  -- This would be implemented when container service is ready
  
  RETURN job_id;
END;
$$;

GRANT USAGE ON PROCEDURE app_schema.launch_training_job(VARCHAR) TO APPLICATION ROLE app_user;

-- Create view for active jobs
CREATE OR REPLACE VIEW app_schema.active_jobs AS
SELECT 
  job_id,
  country,
  status,
  created_at,
  started_at,
  DATEDIFF('second', started_at, CURRENT_TIMESTAMP()) AS duration_seconds
FROM app_schema.job_history
WHERE status IN ('PENDING', 'RUNNING')
ORDER BY created_at DESC;

GRANT SELECT ON VIEW app_schema.active_jobs TO APPLICATION ROLE app_user;

-- Create view for completed jobs
CREATE OR REPLACE VIEW app_schema.completed_jobs AS
SELECT 
  job_id,
  country,
  status,
  created_at,
  completed_at,
  DATEDIFF('second', started_at, completed_at) AS duration_seconds,
  result_path
FROM app_schema.job_history
WHERE status IN ('SUCCEEDED', 'FAILED')
ORDER BY completed_at DESC;

GRANT SELECT ON VIEW app_schema.completed_jobs TO APPLICATION ROLE app_user;
