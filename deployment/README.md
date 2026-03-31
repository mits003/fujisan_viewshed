# Deployment

AWS serverless deployment using S3 + CloudFront.

## Architecture

```
Client (Browser) --HTTPS--> CloudFront ---> S3
                                            ├── Next.js static export
                                            ├── data/fuji_viewshed.pmtiles
                                            └── data/mountains.geojson
```

- **S3**: Private bucket, accessed only via CloudFront OAC
- **CloudFront**: CDN with cache behaviors optimized for each content type
  - `_next/*` — 1-year immutable cache (hashed assets)
  - `data/*` — 1-day cache, compression disabled for PMTiles Range request compatibility
  - Default — standard cache for HTML

## Prerequisites

- [Terraform](https://developer.hashicorp.com/terraform/install) >= 1.0
- [AWS CLI](https://aws.amazon.com/cli/) configured with credentials
- GitHub repository with Actions enabled (for CI/CD)

## 1. Provision Infrastructure

```bash
cd deployment/terraform
terraform init
terraform plan
terraform apply
```

Note the outputs:

```bash
terraform output
# s3_bucket_name              = "fujisan-viewshed"
# cloudfront_distribution_id  = "E1234567890ABC"
# site_url                    = "https://d111111abcdef8.cloudfront.net"
```

To customize the bucket name or region:

```bash
terraform apply -var="project_name=my-fujisan" -var="aws_region=ap-northeast-1"
```

## 2. Upload Pipeline Data

Configure environment variables, then upload:

```bash
# From project root
cp .env.example .env
# Edit .env with your S3_BUCKET_NAME and CLOUDFRONT_DISTRIBUTION_ID

./scripts/upload-data.sh
```

The script reads from `.env` automatically. This uploads `data/fuji_viewshed.pmtiles` and `data/mountains.geojson`.

## 3. Configure GitHub Actions

The workflow at `.github/workflows/deploy.yml` builds the Next.js frontend and deploys to S3 on push to `main`.

### Required GitHub Secrets and Variables

Set secrets in **Settings > Secrets and variables > Actions > Secrets**:

| Secret | Value | Source |
|--------|-------|--------|
| `AWS_ACCESS_KEY_ID` | IAM user access key ID | AWS IAM console |
| `AWS_SECRET_ACCESS_KEY` | IAM user secret access key | AWS IAM console |

Set variables in **Settings > Secrets and variables > Actions > Variables**:

| Variable | Value | Source |
|----------|-------|--------|
| `S3_BUCKET_NAME` | S3 bucket name | `terraform output s3_bucket_name` |
| `CLOUDFRONT_DISTRIBUTION_ID` | CloudFront distribution ID | `terraform output cloudfront_distribution_id` |

The IAM user needs permissions for `s3:PutObject`, `s3:DeleteObject`, `s3:ListBucket` on the site bucket, and `cloudfront:CreateInvalidation` on the distribution.

## 4. Deploy

Frontend deploys automatically when pushing changes under `web/` to `main`. Manual trigger is also available via workflow dispatch.

## Terraform State

By default, state is stored locally. To use remote state, uncomment the S3 backend block in `deployment/terraform/main.tf`.
