terraform {
  required_version = ">= 1.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Uncomment to use S3 backend for remote state
  # backend "s3" {
  #   bucket = "your-terraform-state-bucket"
  #   key    = "fujisan-viewshed/terraform.tfstate"
  #   region = "ap-northeast-1"
  # }
}

provider "aws" {
  region = var.aws_region
}

# --------------------------------------------------------------------------
# S3 Bucket
# --------------------------------------------------------------------------

resource "aws_s3_bucket" "site" {
  bucket = var.project_name
}

resource "aws_s3_bucket_public_access_block" "site" {
  bucket                  = aws_s3_bucket.site.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# --------------------------------------------------------------------------
# CloudFront Origin Access Control
# --------------------------------------------------------------------------

resource "aws_cloudfront_origin_access_control" "site" {
  name                              = var.project_name
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# --------------------------------------------------------------------------
# CloudFront Cache Policies
# --------------------------------------------------------------------------

# Managed policy for compressible static assets (_next/*, HTML, etc.)
data "aws_cloudfront_cache_policy" "caching_optimized" {
  name = "Managed-CachingOptimized"
}

# Custom policy for data files (PMTiles, GeoJSON).
# Compression is DISABLED to preserve byte-range access for PMTiles.
resource "aws_cloudfront_cache_policy" "data_files" {
  name        = "${var.project_name}-data"
  min_ttl     = 0
  default_ttl = 86400   # 1 day
  max_ttl     = 604800  # 1 week

  parameters_in_cache_key_and_forwarded_to_origin {
    cookies_config {
      cookie_behavior = "none"
    }
    headers_config {
      header_behavior = "none"
    }
    query_strings_config {
      query_string_behavior = "none"
    }
    enable_accept_encoding_brotli = false
    enable_accept_encoding_gzip   = false
  }
}

# Immutable assets cache policy (_next/ hashed files)
resource "aws_cloudfront_cache_policy" "immutable_assets" {
  name        = "${var.project_name}-immutable"
  min_ttl     = 31536000  # 1 year
  default_ttl = 31536000
  max_ttl     = 31536000

  parameters_in_cache_key_and_forwarded_to_origin {
    cookies_config {
      cookie_behavior = "none"
    }
    headers_config {
      header_behavior = "none"
    }
    query_strings_config {
      query_string_behavior = "none"
    }
    enable_accept_encoding_brotli = true
    enable_accept_encoding_gzip   = true
  }
}

# --------------------------------------------------------------------------
# CloudFront Distribution
# --------------------------------------------------------------------------

resource "aws_cloudfront_distribution" "site" {
  enabled             = true
  default_root_object = "index.html"
  price_class         = "PriceClass_200" # Asia, US, Europe
  comment             = "Fujisan Viewshed Web Map"

  origin {
    domain_name              = aws_s3_bucket.site.bucket_regional_domain_name
    origin_id                = "s3"
    origin_access_control_id = aws_cloudfront_origin_access_control.site.id
  }

  # Default: HTML and misc static files
  default_cache_behavior {
    target_origin_id       = "s3"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true
    cache_policy_id        = data.aws_cloudfront_cache_policy.caching_optimized.id
  }

  # _next/* hashed assets — immutable, 1-year cache
  ordered_cache_behavior {
    path_pattern           = "_next/*"
    target_origin_id       = "s3"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true
    cache_policy_id        = aws_cloudfront_cache_policy.immutable_assets.id
  }

  # data/* PMTiles & GeoJSON — no compression for Range request compatibility
  ordered_cache_behavior {
    path_pattern           = "data/*"
    target_origin_id       = "s3"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    compress               = false
    cache_policy_id        = aws_cloudfront_cache_policy.data_files.id
  }

  # SPA: serve index.html for missing paths (S3 returns 403 for non-existent keys)
  custom_error_response {
    error_code         = 403
    response_code      = 200
    response_page_path = "/index.html"
  }

  custom_error_response {
    error_code         = 404
    response_code      = 404
    response_page_path = "/404.html"
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

# --------------------------------------------------------------------------
# S3 Bucket Policy — allow CloudFront OAC
# --------------------------------------------------------------------------

resource "aws_s3_bucket_policy" "site" {
  bucket = aws_s3_bucket.site.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowCloudFrontOAC"
        Effect = "Allow"
        Principal = {
          Service = "cloudfront.amazonaws.com"
        }
        Action   = "s3:GetObject"
        Resource = "${aws_s3_bucket.site.arn}/*"
        Condition = {
          StringEquals = {
            "AWS:SourceArn" = aws_cloudfront_distribution.site.arn
          }
        }
      }
    ]
  })
}
