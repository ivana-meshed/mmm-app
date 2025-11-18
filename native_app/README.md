# MMM Trainer - Snowflake Native App

Marketing Mix Modeling (MMM) Trainer is a comprehensive solution for running advanced marketing attribution analysis using the R/Robyn framework directly in your Snowflake environment.

## Features

- **Streamlit Web Interface**: User-friendly interface for configuring and monitoring MMM experiments
- **R/Robyn Integration**: Leverages Meta's open-source Robyn framework for robust MMM
- **Snowpark Container Services**: Runs containerized workloads directly in Snowflake
- **Batch Processing**: Queue and manage multiple training experiments
- **Results Visualization**: Interactive dashboards for analyzing model outputs
- **Secure Data Access**: Works with your existing Snowflake data without data movement

## Quick Start

1. **Install the Application**
   ```sql
   CREATE APPLICATION mmm_trainer
     FROM APPLICATION PACKAGE mmm_trainer_pkg
     USING VERSION v1_0_0;
   ```

2. **Grant Application Access to Your Data**
   ```sql
   GRANT USAGE ON DATABASE <your_database> TO APPLICATION mmm_trainer;
   GRANT USAGE ON SCHEMA <your_schema> TO APPLICATION mmm_trainer;
   GRANT SELECT ON TABLE <your_marketing_data_table> TO APPLICATION mmm_trainer;
   ```

3. **Grant Application Role to Users**
   ```sql
   GRANT APPLICATION ROLE mmm_trainer.app_user TO ROLE <your_role>;
   ```

4. **Access the Application**
   ```sql
   USE APPLICATION mmm_trainer;
   SHOW ENDPOINTS IN SERVICE app_schema.web_service;
   ```

## Data Requirements

Your marketing data should include:
- **Date column**: Daily granularity
- **Dependent variable**: Sales, revenue, or conversions
- **Media spend columns**: Advertising spend by channel
- **Context variables**: Seasonality, promotions, etc. (optional)

Example table structure:
```sql
CREATE TABLE marketing_data (
  date DATE,
  revenue DECIMAL(18,2),
  tv_spend DECIMAL(18,2),
  digital_spend DECIMAL(18,2),
  search_spend DECIMAL(18,2),
  is_promo BOOLEAN,
  temperature DECIMAL(5,2)
);
```

## Usage

### Configure Data Connection

1. Navigate to "Connect your Data" page
2. Provide your database, schema, and table name
3. Or write a custom SQL query to prepare your data

### Map Variables

1. Select your date column
2. Choose dependent variable (e.g., revenue)
3. Map media spend columns
4. Optionally add context and organic variables

### Run Experiment

1. Choose training preset (Test run, Production, Custom)
2. Configure hyperparameters if using Custom preset
3. Launch training job
4. Monitor progress in the queue

### View Results

1. Navigate to "Results: Robyn MMM" page
2. Select a completed experiment
3. Explore model coefficients, ROI, and response curves
4. Download results for further analysis

## Compute Resources

The application uses two compute pools:

- **Web Service Pool**: STANDARD_2 instances for the Streamlit interface
- **Training Pool**: HIGHMEM_8 instances for R/Robyn training jobs

Compute pools auto-suspend after inactivity to minimize costs.

## Pricing

This application incurs costs based on:
- Compute pool usage (billed per-second when active)
- Snowflake storage for results
- Standard Snowflake query costs for data access

Compute pools auto-suspend to minimize idle costs.

## Support

For issues, questions, or feature requests:
- GitHub: https://github.com/ivana-meshed/mmm-app
- Documentation: See full documentation in the repository

## Privacy & Security

- All data processing occurs within your Snowflake account
- No data is sent to external services
- Results are stored in Snowflake stages with encryption
- Application follows Snowflake security best practices

## License

Apache-2.0 License

## Version

1.0.0

## About R/Robyn

Robyn is Meta's open-source Marketing Mix Modeling framework. Learn more at:
https://github.com/facebookexperimental/Robyn
