# AWS-native Knowledge Vault

An AWS-native serverless application that enables users to securely upload personal documents and query them through a RAG-powered conversational interface.

---

## Tech Stack

**Application**

| Layer | Technology |
|---|---|
| Frontend | Next.js 15 (App Router, static export), Tailwind CSS |
| Auth UI | AWS Amplify UI React |
| Backend | Python 3.12 (AWS Lambda) |
| Infrastructure | Terraform |

**AWS Services**

| Service | Purpose |
|---|---|
| Amazon Cognito | Email/password sign-up and sign-in, JWT issuance |
| API Gateway (HTTP API) | REST API with JWT authorizer |
| AWS Lambda | Chat handler and document processor |
| Amazon S3 | Frontend hosting and document vault |
| Amazon CloudFront | CDN for the frontend |
| Amazon Bedrock | Nova Lite (LLM) + Titan Text Embeddings v2 |
| Amazon Textract | Text extraction from PDF, DOCX, and images |
| Amazon DynamoDB | Document chunks/embeddings, processing status, usage quotas |

---

## Local Development

**Prerequisites:** Node.js ≥ 18, Python 3.12, AWS CLI configured with credentials that have access to the deployed AWS resources.

**Frontend**

```bash
npm install
npm run dev    # http://localhost:3000
```

**Backend**

```bash
cd backend
pip install flask boto3 python-dotenv
python local_server.py    # http://localhost:8000
```

Create `backend/.env` with your deployed resource values (see Environment Variables below). Set `NEXT_PUBLIC_API_URL=http://localhost:8000/chat` in `.env.local` to point the frontend at the local server.

---

## Deployment

**Prerequisites:** Terraform ≥ 1.5, AWS CLI configured.

Before first deploy, create an S3 bucket for Terraform state and update the `bucket` value in `terraform/provider.tf`.

```bash
cd terraform
terraform init
terraform apply
```

Terraform outputs the values needed for the frontend environment variables.

---

## Environment Variables

**`.env.local`** — frontend (not committed)

| Variable | Description |
|---|---|
| `NEXT_PUBLIC_API_URL` | API Gateway URL with `/chat` suffix |
| `NEXT_PUBLIC_USER_POOL_ID` | Cognito User Pool ID |
| `NEXT_PUBLIC_USER_POOL_CLIENT_ID` | Cognito App Client ID |

**`backend/.env`** — local backend simulation (not committed)

| Variable | Description |
|---|---|
| `AWS_REGION` | AWS region (e.g. `us-east-1`) |
| `KNOWLEDGE_VAULT_BUCKET` | S3 vault bucket name |
| `USER_USAGE_TABLE` | DynamoDB usage quotas table name |
| `CHUNKS_TABLE` | DynamoDB document chunks table name |
| `DOCUMENT_STATUS_TABLE` | DynamoDB document status table name |
| `BEDROCK_MODEL_ID` | Bedrock model ID (default: `amazon.nova-lite-v1:0`) |

**GitHub Secrets** — required for CI/CD

| Secret | Description |
|---|---|
| `AWS_ACCESS_KEY_ID` | IAM user access key |
| `AWS_SECRET_ACCESS_KEY` | IAM user secret key |
| `DOMAIN_NAME` | *(Optional)* Custom domain for CloudFront |
| `CERTIFICATE_ARN` | *(Optional)* ACM certificate ARN (must be in us-east-1) |

---

## CI/CD

Pushing to `main` triggers the GitHub Actions workflow in `.github/workflows/deploy.yml`, which:

1. Runs `terraform apply` to provision or update all infrastructure
2. Captures Terraform outputs (API URL, Cognito IDs, S3 bucket, CloudFront ID)
3. Builds the Next.js app with the correct `NEXT_PUBLIC_*` environment variables injected at build time
4. Syncs the static output to the S3 frontend bucket
5. Invalidates the CloudFront distribution

No manual frontend configuration is needed — the build always uses live Terraform output values.

---

## Validation

```bash
./validate.sh    # lint + syntax check (Git Bash / Linux / macOS)
```

---

## License

MIT — see [LICENSE](LICENSE)

