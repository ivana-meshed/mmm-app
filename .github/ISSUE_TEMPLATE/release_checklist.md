---
name: Release Checklist
about: Checklist for creating a new release
title: 'Release vX.Y.Z'
labels: 'release'
assignees: ''
---

## Release Checklist for vX.Y.Z

### Pre-release

- [ ] All features for this release are merged to `main`
- [ ] All tests pass: `make test`
- [ ] Code is formatted: `make format`
- [ ] CI/CD pipeline is green
- [ ] Update `VERSION` file with new version number
- [ ] Update `CHANGELOG.md` with release notes
  - [ ] Added features documented
  - [ ] Changed/updated features documented
  - [ ] Fixed bugs documented
  - [ ] Deprecated features noted
  - [ ] Security fixes documented
- [ ] Documentation is up to date
  - [ ] README.md reflects new features
  - [ ] Architecture changes documented
  - [ ] API changes documented

### Release Process

- [ ] Create release branch: `git checkout -b release/vX.Y.Z`
- [ ] Commit version updates: `git commit -am "Prepare release vX.Y.Z"`
- [ ] Create PR to main for final review
- [ ] Merge release PR to main
- [ ] Create and push git tag: `git tag vX.Y.Z && git push origin vX.Y.Z`
- [ ] Verify GitHub Actions release workflow completes successfully
- [ ] Verify Docker images are pushed to Artifact Registry
- [ ] Verify GitHub release is created

### Post-release

- [ ] Verify release artifacts:
  - [ ] Docker images tagged with version
  - [ ] GitHub release published
  - [ ] Release notes accurate
- [ ] Test deployment with new version
- [ ] Announce release (if applicable)
- [ ] Update deployment documentation if needed
- [ ] Close this issue

### Rollback Plan

If issues are discovered:
- [ ] Document the issue
- [ ] Create hotfix branch from release tag
- [ ] Fix issue and create patch release (vX.Y.Z+1)
- [ ] Or rollback to previous version

## Release Notes Draft

### Added
- 

### Changed
- 

### Fixed
- 

### Deprecated
- 

### Security
- 

---

**Version:** vX.Y.Z  
**Target Date:** YYYY-MM-DD  
**Release Manager:** @username
