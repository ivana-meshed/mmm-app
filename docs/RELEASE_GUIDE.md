# Release Guide

This guide explains how to create releases for the MMM Trainer application.

## Overview

The MMM Trainer uses semantic versioning and automated release workflows to manage deployments.

## Release Process

### 1. Prepare the Release

1. **Update VERSION file**
   ```bash
   echo "1.1.0" > VERSION
   ```

2. **Update CHANGELOG.md**
   
   Add a new section for the release:
   ```markdown
   ## [1.1.0] - 2025-11-20
   
   ### Added
   - New feature X
   - New feature Y
   
   ### Changed
   - Updated component Z
   
   ### Fixed
   - Bug fix for issue #123
   ```

3. **Update documentation** (if needed)
   - Update README.md
   - Update ARCHITECTURE.md
   - Update any relevant docs/

### 2. Create Release Commit

1. **Commit version changes**
   ```bash
   git checkout -b release/v1.1.0
   git add VERSION CHANGELOG.md
   git commit -m "Prepare release v1.1.0"
   ```

2. **Push to repository**
   ```bash
   git push origin release/v1.1.0
   ```

3. **Create Pull Request** to `main` for final review

### 3. Merge and Tag

1. **Merge the release PR** to `main`

2. **Pull latest main**
   ```bash
   git checkout main
   git pull origin main
   ```

3. **Create and push tag**
   ```bash
   git tag v1.1.0
   git push origin v1.1.0
   ```

### 4. Automated Release Workflow

Once the tag is pushed, GitHub Actions automatically:

1. **Validates** VERSION file matches tag
2. **Builds** Docker images with version tags
3. **Pushes** images to Artifact Registry
4. **Extracts** changelog for this version
5. **Creates** GitHub release with notes
6. **Tags** images with version number

Monitor the workflow: `.github/workflows/release.yml`

### 5. Verify Release

After the workflow completes:

1. **Check GitHub Releases** page
   - Release should be published
   - Release notes should be populated

2. **Verify Docker images**
   ```bash
   gcloud artifacts docker images list \
     europe-west1-docker.pkg.dev/datawarehouse-422511/mmm-repo/mmm-web
   
   # Should show tags: v1.1.0, 1.1.0, latest
   ```

3. **Test deployment** (optional)
   ```bash
   # Update Terraform to use new version
   cd infra/terraform
   terraform plan -var="web_image=...mmm-web:1.1.0"
   ```

## Release Checklist Template

Use this checklist for each release:

```markdown
## Release v1.1.0 Checklist

### Pre-release
- [ ] All features merged to main
- [ ] Tests pass: `make test`
- [ ] Code formatted: `make format`
- [ ] VERSION file updated
- [ ] CHANGELOG.md updated
- [ ] Documentation updated

### Release
- [ ] Release branch created
- [ ] Release PR reviewed and merged
- [ ] Tag created and pushed
- [ ] GitHub Actions workflow successful
- [ ] GitHub release published

### Post-release
- [ ] Docker images verified
- [ ] Deployment tested (dev/prod)
- [ ] Release announced (if applicable)
- [ ] Issues closed for this milestone
```

## Version Numbering

Follow [Semantic Versioning](https://semver.org/):

- **MAJOR** (1.0.0 → 2.0.0): Breaking changes
- **MINOR** (1.0.0 → 1.1.0): New features, backward compatible
- **PATCH** (1.0.0 → 1.0.1): Bug fixes, backward compatible

Examples:
- `1.0.0` - Initial release
- `1.1.0` - Added new Snowflake Marketplace support
- `1.1.1` - Fixed bug in version display
- `2.0.0` - Changed API, breaking change

## Pre-release Versions

For testing releases before official launch:

```bash
# Alpha release
echo "1.1.0-alpha.1" > VERSION
git tag v1.1.0-alpha.1

# Beta release
echo "1.1.0-beta.1" > VERSION
git tag v1.1.0-beta.1

# Release candidate
echo "1.1.0-rc.1" > VERSION
git tag v1.1.0-rc.1
```

Pre-release versions won't be marked as "Latest Release" on GitHub.

## Hotfix Releases

For urgent bug fixes in production:

1. **Create hotfix branch** from the release tag
   ```bash
   git checkout -b hotfix/1.0.1 v1.0.0
   ```

2. **Fix the bug** and commit
   ```bash
   # Make fixes
   git add .
   git commit -m "Fix critical bug in ..."
   ```

3. **Update version** to patch level
   ```bash
   echo "1.0.1" > VERSION
   # Update CHANGELOG.md
   git add VERSION CHANGELOG.md
   git commit -m "Prepare hotfix v1.0.1"
   ```

4. **Merge to main and tag**
   ```bash
   git checkout main
   git merge hotfix/1.0.1
   git tag v1.0.1
   git push origin main v1.0.1
   ```

## Rolling Back a Release

If a release has critical issues:

### Option 1: Create a Patch Release

Most common approach - fix the issue and release a new patch version.

### Option 2: Revert to Previous Version

If urgent:

1. **Deploy previous version** via Terraform
   ```bash
   cd infra/terraform
   terraform apply -var="web_image=...mmm-web:1.0.0"
   ```

2. **Delete problematic tag** (optional)
   ```bash
   git tag -d v1.1.0
   git push origin :refs/tags/v1.1.0
   ```

3. **Create new patch release** with fix

## Release Artifacts

Each release includes:

1. **Git tag**: `vX.Y.Z` format
2. **GitHub Release**: With changelog and assets
3. **Docker Images**:
   - `mmm-web:X.Y.Z`
   - `mmm-web:vX.Y.Z`
   - `mmm-training:X.Y.Z`
   - `mmm-training:vX.Y.Z`
4. **Source Code**: Automatically attached to GitHub release

## Continuous Deployment vs Releases

- **Continuous Deployment** (main branch): `ci.yml` workflow
  - Deploys to production on every main commit
  - Uses `latest` and `<git-sha>` tags
  - For continuous delivery

- **Releases** (tags): `release.yml` workflow
  - Creates versioned, stable releases
  - Uses semantic version tags
  - For milestone deployments

Choose based on your deployment strategy.

## Troubleshooting

### Release Workflow Fails

1. **Check VERSION file format**
   - Must be valid semver (e.g., `1.0.0`)
   - No extra whitespace

2. **Verify tag format**
   - Must start with `v` (e.g., `v1.0.0`)
   - Must match VERSION file

3. **Check CHANGELOG.md**
   - Must have section for this version
   - Format: `## [1.0.0] - YYYY-MM-DD`

### Docker Build Fails

1. **Check Docker context**
   - Ensure all required files are present
   - Verify Dockerfile references are correct

2. **Test local build**
   ```bash
   docker build -f docker/Dockerfile.web -t test .
   ```

### GitHub Release Not Created

1. **Check workflow permissions**
   - Ensure `contents: write` is set
   - Verify GitHub token has necessary permissions

2. **Check release action logs**
   - Review step outputs in GitHub Actions

## References

- [Semantic Versioning](https://semver.org/)
- [Keep a Changelog](https://keepachangelog.com/)
- [GitHub Releases](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)
- [Conventional Commits](https://www.conventionalcommits.org/)

## Questions?

For issues or questions about releases, see:
- GitHub Issues: https://github.com/ivana-meshed/mmm-app/issues
- Release workflow: `.github/workflows/release.yml`
- Release template: `.github/ISSUE_TEMPLATE/release_checklist.md`
