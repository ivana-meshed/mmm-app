# Changelog

All notable changes to the MMM Trainer application will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Version display in Streamlit application sidebar
- Automated release workflow via GitHub Actions
- CHANGELOG.md for tracking version changes
- Snowflake Marketplace deployment documentation

## [1.0.0] - 2025-11-18

### Added
- Initial release of MMM Trainer application
- Streamlit-based web interface for MMM experiments
- Cloud Run deployment on Google Cloud Platform
- Snowflake data integration
- R/Robyn training pipeline
- Batch job queue management
- Google OAuth authentication
- Persistent private key storage in Secret Manager

### Infrastructure
- Terraform configuration for GCP resources
- CI/CD pipelines for production and development
- Multi-platform Docker image builds
- Cloud Run Jobs for training workloads

### Security
- Key-pair authentication for Snowflake
- Secret Manager integration
- Service account-based IAM
- Domain-restricted access control

[Unreleased]: https://github.com/ivana-meshed/mmm-app/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/ivana-meshed/mmm-app/releases/tag/v1.0.0
