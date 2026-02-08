# AWS Serverless AI Chatbot

A serverless chatbot application built with Next.js, Python Lambda, API Gateway, and Amazon Bedrock. It features authentication, message quotas, and a secure infrastructure managed by Terraform.

## 🏗 Architecture

- **Frontend**: Next.js 14 (App Router) hosted on S3 + CloudFront.
- **Auth**: Amazon Cognito (User Pools & Identity).
- **Backend**: AWS Lambda (Python 3.12) handling business logic.
- **AI Model**: Amazon Bedrock (default: Amazon Nova Lite).
- **Database**: DynamoDB for tracking user activity and enforcing quotas.
- **API**: API Gateway (HTTP API) with JWT Authorizer.
- **Infrastructure**: Terraform for full "Infrastructure as Code".

## 🚀 Features

- **AI Chat**: Interactive chat interface powered by Bedrock.
- **Authentication**: Secure sign-up/sign-in via Cognito.
- **Rate Limiting**:
  - Global API throttling (Burst/Rate limits at Gateway).
  - User-level daily message quotas (tracked in DynamoDB).
- **Unlimited Admin Access**: Specific users (in "Admins" group) bypass quotas.
- **Local Development**: Full local simulation including Auth & Backend.

## 🛠️ Installation & Deployment

### Prerequisites
- AWS Account & CLI configured (`aws configure`)
- Terraform installed
- Node.js & npm installed
- Python 3.12 installed

### 1. Deploy Infrastructure
All AWS resources are managed via Terraform.

```bash
cd terraform
terraform init
terraform apply
```

After deployment, note the outputs:
- `api_url`
- `cloudfront_domain_name` (Your public URL)
- `cognito_user_pool_id`
- `cognito_client_id`

### 2. Configure Frontend
The GitHub Action automatically handles this for CI/CD. For manual deployment or local dev, create a `.env.local` file:

```env
NEXT_PUBLIC_API_URL=<your-api-url-from-output>
NEXT_PUBLIC_USER_POOL_ID=<your-user-pool-id>
NEXT_PUBLIC_USER_POOL_CLIENT_ID=<your-client-id>
```

### 3. Run Locally

**Frontend**:
```bash
npm install
npm run dev
```

**Backend (Local Simulation)**:
```bash
pip install flask boto3
python backend/local_server.py
```
Open [http://localhost:3000](http://localhost:3000).

## 🛡️ Admins & Quotas

By default, all users are limited to 50 messages/day. To grant unlimited access:

1. Go to AWS Console -> Cognito -> User Pools.
2. Select your user -> Groups.
3. Add user to the **"Admins"** group.

## ✅ Validation

Run the project validation script to check linting and syntax:

```bash
# Git Bash / Linux
./validate.sh
```

## AWS Deployment Setup

### Prerequisites
1. AWS Account
2. GitHub Repository

### Manual Steps
1. **AWS Identity**: Create an IAM User with access to app services.
2. **GitHub Secrets**: Add `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` to your repo settings.
   - **Custom Domain (Optional)**: Add `DOMAIN_NAME` and `CERTIFICATE_ARN` (US-East-1 ACM Cert) to automatically configure your custom domain on deploy.
3. **Terraform State**: 
   - Create S3 bucket with blocked public access.
   - The backend configuration in `terraform/provider.tf` should be set to use this bucket.

### Deployment
Push to `main` branch to trigger the GitHub Action.
