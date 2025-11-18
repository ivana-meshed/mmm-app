-- Helper script to set up UI service for MMM Trainer Native App

-- Create service specification for web UI
CREATE OR REPLACE FILE FORMAT app_schema.yaml_format
  TYPE = 'CSV'
  FIELD_DELIMITER = NONE
  RECORD_DELIMITER = NONE;

-- Service specification content
CREATE OR REPLACE TEMPORARY TABLE service_spec_content (content VARCHAR);

INSERT INTO service_spec_content VALUES ('spec:
  containers:
  - name: web
    image: /mmm_app/public/mmm-web:1.0.0
    env:
      PORT: "8080"
      STREAMLIT_SERVER_ADDRESS: "0.0.0.0"
      SNOWFLAKE_ACCOUNT: "{{ SNOWFLAKE_ACCOUNT }}"
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
    public: true');

-- Write spec to stage
COPY INTO @app_schema.mmm_specs/mmm_service_spec.yaml
FROM service_spec_content
FILE_FORMAT = (FORMAT_NAME = app_schema.yaml_format)
SINGLE = TRUE
OVERWRITE = TRUE;

-- Create web service
CREATE SERVICE IF NOT EXISTS app_schema.web_service
  IN COMPUTE POOL mmm_web_pool
  FROM @app_schema.mmm_specs
  SPEC = 'mmm_service_spec.yaml';

-- Grant access
GRANT USAGE ON SERVICE app_schema.web_service TO APPLICATION ROLE app_user;

-- Display service endpoint
SELECT 'Web service created. Access endpoint with: SHOW ENDPOINTS IN SERVICE app_schema.web_service;' AS message;
