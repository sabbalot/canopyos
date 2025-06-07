# Versioning Strategy for Private Repositories

## Overview

This document outlines the versioning strategy for your private repositories, including Git tagging, semantic versioning, CI/CD integration, and coordinated releases across multiple repositories.

## Semantic Versioning Strategy

### Version Format: `MAJOR.MINOR.PATCH`

**Example: `v1.2.3`**

- **MAJOR** (1): Breaking changes, incompatible API changes
- **MINOR** (2): New features, backward-compatible functionality
- **PATCH** (3): Bug fixes, backward-compatible fixes

### Version Examples

```
v1.0.0 - Initial stable release
v1.0.1 - Bug fix
v1.1.0 - New feature
v2.0.0 - Breaking change
```

## Git Tagging Strategy

### Repository-Specific Tagging

**Each repository maintains its own version:**

```bash
# Backend repository (grow-assistant-services)
v1.0.0-backend
v1.0.1-backend
v1.1.0-backend

# Frontend repository (grow-assistant-app)
v1.0.0-frontend
v1.0.1-frontend
v1.1.0-frontend

# Deployment repository (grow-assistant-deployment)
v1.0.0-deployment
v1.0.1-deployment
```

### Creating Git Tags

**Manual tagging process:**

```bash
# In each repository
git checkout main
git pull origin main

# Create annotated tag
git tag -a v1.0.0-backend -m "Release v1.0.0-backend

- Initial stable release
- Core automation features
- Multi-arch Docker support"

# Push tag to GitHub
git push origin v1.0.0-backend
```

**Automated tagging in CI/CD:**

```yaml
# Add to GitHub Actions workflow
- name: Create Release Tag
  if: github.ref == 'refs/heads/main'
  run: |
    # Get current version from package.json or setup.py
    VERSION=$(grep version package.json | cut -d '"' -f4)  # Node.js
    # VERSION=$(python setup.py --version)  # Python
    
    # Create tag
    git tag -a "v${VERSION}-backend" -m "Release v${VERSION}-backend"
    git push origin "v${VERSION}-backend"
```

## Docker Image Tagging Strategy

### Tag Mapping

**Git tags → Docker tags:**

```yaml
Git Tag: v1.0.0-backend    → Docker: sabbalot/grow-assistant-backend:1.0.0
Git Tag: v1.1.0-frontend   → Docker: sabbalot/grow-assistant-app:1.1.0
Git Tag: main branch       → Docker: sabbalot/grow-assistant-backend:latest
Git Tag: dev branch        → Docker: sabbalot/grow-assistant-backend:dev
```

### Multi-Tag Strategy

**Each Docker image gets multiple tags:**

```bash
# For version v1.0.0-backend
sabbalot/grow-assistant-backend:1.0.0      # Specific version
sabbalot/grow-assistant-backend:1.0        # Minor version
sabbalot/grow-assistant-backend:1          # Major version
sabbalot/grow-assistant-backend:latest     # Latest stable
```

## Updated GitHub Actions Workflow

### Enhanced CI/CD with Versioning

```yaml
name: Build and Push Versioned Docker Image

on:
  push:
    branches: [main, dev]
    tags: ['v*-backend']  # Trigger on version tags
  pull_request:
    branches: [main, dev]

env:
  REGISTRY: docker.io
  IMAGE_NAME: sabbalot/grow-assistant-backend

jobs:
  build-and-push:
    if: github.event_name != 'pull_request'
    runs-on: ubuntu-latest
    
    steps:
      - name: Checkout
        uses: actions/checkout@v4
        with:
          fetch-depth: 0  # Needed for git describe

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Log in to Docker Hub
        uses: docker/login-action@v3
        with:
          username: ${{ secrets.DOCKER_HUB_USERNAME }}
          password: ${{ secrets.DOCKER_HUB_ACCESS_TOKEN }}

      - name: Extract version and tags
        id: meta
        run: |
          # Determine version based on trigger
          if [[ $GITHUB_REF == refs/tags/v*-backend ]]; then
            # Tagged release
            VERSION=${GITHUB_REF#refs/tags/v}
            VERSION=${VERSION%-backend}
            echo "version=${VERSION}" >> $GITHUB_OUTPUT
            echo "is_release=true" >> $GITHUB_OUTPUT
            
            # Create multiple version tags
            TAGS="${{ env.IMAGE_NAME }}:${VERSION}"
            TAGS="${TAGS},${{ env.IMAGE_NAME }}:$(echo ${VERSION} | cut -d. -f1-2)"
            TAGS="${TAGS},${{ env.IMAGE_NAME }}:$(echo ${VERSION} | cut -d. -f1)"
            TAGS="${TAGS},${{ env.IMAGE_NAME }}:latest"
            echo "tags=${TAGS}" >> $GITHUB_OUTPUT
            
          elif [[ $GITHUB_REF == refs/heads/main ]]; then
            # Main branch
            VERSION="main-${GITHUB_SHA::8}"
            echo "version=${VERSION}" >> $GITHUB_OUTPUT
            echo "is_release=false" >> $GITHUB_OUTPUT
            echo "tags=${{ env.IMAGE_NAME }}:latest,${{ env.IMAGE_NAME }}:${VERSION}" >> $GITHUB_OUTPUT
            
          elif [[ $GITHUB_REF == refs/heads/dev ]]; then
            # Dev branch
            VERSION="dev-${GITHUB_SHA::8}"
            echo "version=${VERSION}" >> $GITHUB_OUTPUT
            echo "is_release=false" >> $GITHUB_OUTPUT
            echo "tags=${{ env.IMAGE_NAME }}:dev,${{ env.IMAGE_NAME }}:${VERSION}" >> $GITHUB_OUTPUT
          fi

      - name: Build and push
        uses: docker/build-push-action@v5
        with:
          context: .
          platforms: linux/amd64,linux/arm64
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: |
            org.opencontainers.image.version=${{ steps.meta.outputs.version }}
            org.opencontainers.image.source=${{ github.server_url }}/${{ github.repository }}
            org.opencontainers.image.revision=${{ github.sha }}

      - name: Create GitHub Release
        if: steps.meta.outputs.is_release == 'true'
        uses: actions/create-release@v1
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        with:
          tag_name: ${{ github.ref_name }}
          release_name: Release ${{ steps.meta.outputs.version }}
          body: |
            ## Docker Images
            
            ```bash
            docker pull ${{ env.IMAGE_NAME }}:${{ steps.meta.outputs.version }}
            ```
            
            **Platforms:** linux/amd64, linux/arm64
          draft: false
          prerelease: false
```

## Version Management Workflow

### 1. Development Cycle

```bash
# Feature development
git checkout -b feature/new-sensor-support
# ... develop feature ...
git commit -m "Add new sensor support"
git push origin feature/new-sensor-support

# Create PR to dev branch
# After review, merge to dev
```

### 2. Release Preparation

```bash
# Create release branch
git checkout dev
git checkout -b release/1.1.0
git push origin release/1.1.0

# Update version in package.json/setup.py
# Update CHANGELOG.md
# Test thoroughly

# Merge to main
git checkout main
git merge release/1.1.0
```

### 3. Version Tagging

```bash
# Create release tag
git tag -a v1.1.0-backend -m "Release v1.1.0-backend

New Features:
- Advanced sensor support
- Improved automation rules
- Performance optimizations

Bug Fixes:
- Fixed data logging issue
- Corrected timezone handling"

git push origin v1.1.0-backend
```

### 4. Docker Image Release

```bash
# Triggered automatically by tag push
# Results in:
sabbalot/grow-assistant-backend:1.1.0
sabbalot/grow-assistant-backend:1.1
sabbalot/grow-assistant-backend:1
sabbalot/grow-assistant-backend:latest
```

## Multi-Repository Coordination

### Release Coordination Strategy

**Option 1: Independent Versioning**
- Each repo versions independently
- Users specify versions in docker-compose.yml
- Deployment repo documents compatible versions

**Option 2: Coordinated Releases**
- Major releases coordinate across repos
- Use deployment repo version as coordinator

### Deployment Repository Versioning

**docker-compose.yml with version pinning:**

```yaml
version: '3.8'

services:
  app:
    image: sabbalot/grow-assistant-app:1.0.0  # Pinned version
    # ... config
    
  python_backend:
    image: sabbalot/grow-assistant-backend:1.1.0  # Pinned version
    # ... config
```

**Version compatibility matrix in README:**

```markdown
## Version Compatibility

| Deployment | Backend | Frontend | Release Date |
|------------|---------|----------|--------------|
| v1.0.0     | v1.0.0  | v1.0.0   | 2024-01-15  |
| v1.0.1     | v1.0.1  | v1.0.0   | 2024-01-20  |
| v1.1.0     | v1.1.0  | v1.1.0   | 2024-02-01  |
```

## Version Management Tools

### Automated Version Bumping

**For Node.js projects (frontend):**

```json
// package.json scripts
{
  "scripts": {
    "version:patch": "npm version patch",
    "version:minor": "npm version minor", 
    "version:major": "npm version major",
    "release": "npm version patch && git push && git push --tags"
  }
}
```

**For Python projects (backend):**

```bash
# Using bump2version
pip install bump2version

# .bumpversion.cfg
[bumpversion]
current_version = 1.0.0
commit = True
tag = True
tag_name = v{new_version}-backend

[bumpversion:file:setup.py]
```

### Release Notes Automation

**GitHub Actions for changelog:**

```yaml
- name: Generate Changelog
  uses: github-changelog-generator-action@v1
  with:
    token: ${{ secrets.GITHUB_TOKEN }}
    since-tag: v1.0.0-backend
```

## Best Practices

### 1. Version Consistency
- Keep related components in sync for major releases
- Document breaking changes clearly
- Use pre-release tags for testing (v1.1.0-beta1)

### 2. Docker Tag Strategy
- Always provide specific version tags
- Maintain latest tag for stable releases
- Use dev tags for development builds

### 3. Release Documentation
- Maintain CHANGELOG.md in each repo
- Document breaking changes
- Include migration guides for major versions

### 4. Testing Strategy
- Test specific version combinations
- Validate multi-arch builds
- Verify rollback procedures

## Troubleshooting

### Common Issues

**Tag conflicts:**
```bash
# If tag already exists
git tag -d v1.0.0-backend  # Delete local
git push origin :refs/tags/v1.0.0-backend  # Delete remote
```

**Version synchronization:**
```bash
# Check current versions across repos
git ls-remote --tags origin | grep backend
git ls-remote --tags origin | grep frontend
```

**Docker tag verification:**
```bash
# Verify multi-arch images
docker buildx imagetools inspect sabbalot/grow-assistant-backend:1.0.0
```

---

**This versioning strategy provides professional release management while maintaining your hybrid private/public approach.** 