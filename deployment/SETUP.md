# AWS & Terraform Setup

Step-by-step guide to configure AWS credentials and provision infrastructure for this project.

## 1. Install Tools

```bash
# AWS CLI
brew install awscli

# Terraform
brew install terraform
```

## 2. Configure AWS CLI

### Option A: SSO (recommended)

```bash
aws configure sso
```

Follow the prompts:

| Prompt | Value |
|--------|-------|
| SSO session name | `fujisan-dev` (or any name) |
| SSO start URL | Your organization's SSO URL |
| SSO region | `ap-northeast-1` |
| CLI default region | `ap-northeast-1` |
| CLI profile name | `fujisan-dev` |

Then log in:

```bash
aws sso login --profile fujisan-dev
```

Set the profile for the current shell:

```bash
export AWS_PROFILE=fujisan-dev
```

Verify access:

```bash
aws sts get-caller-identity
aws s3 ls
```

### Option B: IAM Access Keys

```bash
aws configure --profile fujisan-dev
```

| Prompt | Value |
|--------|-------|
| AWS Access Key ID | Your access key |
| AWS Secret Access Key | Your secret key |
| Default region | `ap-northeast-1` |
| Default output format | `json` |

Then:

```bash
export AWS_PROFILE=fujisan-dev
```

## 3. Provision Infrastructure with Terraform

```bash
cd deployment/terraform
terraform init
terraform plan     # Review changes
terraform apply    # Apply (type "yes" to confirm)
```

Note the outputs:

```
s3_bucket_name              = "fujisan-viewshed"
cloudfront_distribution_id  = "E22H1HN9QTCKRV"
site_url                    = "https://d37ah2q0fa8woc.cloudfront.net"
```

## 4. Configure Environment

```bash
# From project root
cp .env.example .env
```

Edit `.env` with the Terraform outputs:

```
S3_BUCKET_NAME=fujisan-viewshed
CLOUDFRONT_DISTRIBUTION_ID=E22H1HN9QTCKRV
AWS_REGION=ap-northeast-1
```

## Troubleshooting

### SSO token expired

```
Error when retrieving token from sso: Token has expired and refresh failed
```

Re-authenticate:

```bash
aws sso login --profile fujisan-dev
```

### Terraform resources already exist

If `terraform apply` fails with 409 errors (resources already exist in AWS but not in Terraform state), import them:

```bash
cd deployment/terraform

# S3 bucket
terraform import aws_s3_bucket.site fujisan-viewshed

# CloudFront OAC
OAC_ID=$(aws cloudfront list-origin-access-controls \
  --query "OriginAccessControlList.Items[?Name=='fujisan-viewshed'].Id" --output text)
terraform import aws_cloudfront_origin_access_control.site $OAC_ID

# Cache policies
DATA_POLICY=$(aws cloudfront list-cache-policies --type custom \
  --query "CachePolicyList.Items[?CachePolicy.CachePolicyConfig.Name=='fujisan-viewshed-data'].CachePolicy.Id" --output text)
terraform import aws_cloudfront_cache_policy.data_files $DATA_POLICY

IMMUTABLE_POLICY=$(aws cloudfront list-cache-policies --type custom \
  --query "CachePolicyList.Items[?CachePolicy.CachePolicyConfig.Name=='fujisan-viewshed-immutable'].CachePolicy.Id" --output text)
terraform import aws_cloudfront_cache_policy.immutable_assets $IMMUTABLE_POLICY

# CloudFront distribution (if it exists)
DIST_ID=$(aws cloudfront list-distributions \
  --query "DistributionList.Items[?Comment=='Fujisan Viewshed Web Map'].Id" --output text)
terraform import aws_cloudfront_distribution.site $DIST_ID
```

Then re-run `terraform apply`.
