project_id     = "datawarehouse-422511"
region         = "europe-west1"
bucket_name    = "mmm-app-output"
web_image      = "europe-west1-docker.pkg.dev/datawarehouse-422511/mmm-repo/mmm-web-dev:latest"
training_image = "europe-west1-docker.pkg.dev/datawarehouse-422511/mmm-repo/mmm-training:latest"
deployer_sa    = "github-deployer@datawarehouse-422511.iam.gserviceaccount.com"

sf_user      = "IPENC"
sf_account   = "AMXUZTH-AWS_BRIDGE"
sf_warehouse = "SMALL_WH"
sf_database  = "MESHED_BUYCYCLE"
sf_schema    = "GROWTH"
sf_role      = "ACCOUNTADMIN"

