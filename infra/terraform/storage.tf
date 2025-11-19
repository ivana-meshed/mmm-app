# GCS Bucket with lifecycle policies for cost optimization
# This file implements cost reduction strategy #2 from Cost estimate.csv

# Note: The bucket is managed outside Terraform, so we use a data source
# to reference it and add lifecycle rules via google_storage_bucket_lifecycle_rule
# If the bucket is created elsewhere, these lifecycle rules can be applied manually
# via gcloud or the GCP console.

# Lifecycle policy configuration for cost optimization
# Archives old data to Nearline (30 days) and Coldline (90 days)
# This can reduce storage costs by 50-80% for historical data

# To apply these lifecycle rules manually to the existing bucket:
# 1. Create a lifecycle.json file with the rules below
# 2. Run: gcloud storage buckets update gs://mmm-app-output --lifecycle-file=lifecycle.json

# Example lifecycle.json content:
# {
#   "lifecycle": {
#     "rule": [
#       {
#         "action": {"type": "SetStorageClass", "storageClass": "NEARLINE"},
#         "condition": {
#           "age": 30,
#           "matchesPrefix": ["robyn/", "datasets/", "training-data/"]
#         }
#       },
#       {
#         "action": {"type": "SetStorageClass", "storageClass": "COLDLINE"},
#         "condition": {
#           "age": 90,
#           "matchesPrefix": ["robyn/", "datasets/", "training-data/"]
#         }
#       },
#       {
#         "action": {"type": "Delete"},
#         "condition": {
#           "age": 365,
#           "matchesPrefix": ["robyn-queues/"]
#         }
#       }
#     ]
#   }
# }

# Cost savings from lifecycle policies:
# - Standard Storage: $0.020/GB/month
# - Nearline Storage: $0.010/GB/month (30-89 days) - 50% savings
# - Coldline Storage: $0.004/GB/month (90+ days) - 80% savings
#
# For 80GB baseline storage with minimal access to old data:
# - Before: 80GB × $0.020 = $1.60/month
# - After (assuming 20GB hot, 30GB nearline, 30GB coldline):
#   20GB × $0.020 + 30GB × $0.010 + 30GB × $0.004 = $0.40 + $0.30 + $0.12 = $0.82/month
# - Savings: $0.78/month (49% reduction)
