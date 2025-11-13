terraform {
  backend "s3" {
    bucket = "mmm-tf-state"
    key    = "terraform.tfstate"
    region = "us-east-1"
    # Optionally enable encryption and DynamoDB for state locking
    # encrypt        = true
    # dynamodb_table = "mmm-tf-state-lock"
  }
}
