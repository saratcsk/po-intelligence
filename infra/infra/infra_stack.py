import shutil
import subprocess
import sys
from pathlib import Path

import jsii
from aws_cdk import (
    BundlingOptions,
    CfnOutput,
    Duration,
    ILocalBundling,
    RemovalPolicy,
    Stack,
    aws_apigatewayv2 as apigw,
    aws_apigatewayv2_authorizers as authorizers,
    aws_apigatewayv2_integrations as integrations,
    aws_certificatemanager as acm,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_dynamodb as dynamodb,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_route53 as route53,
    aws_route53_targets as targets,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
    aws_s3_notifications as s3n,
    aws_ssm as ssm,
)
from constructs import Construct

BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"   # made by `npm run build`
MODEL_ID = "anthropic.claude-haiku-4-5-20251001-v1:0"           # base model
INFERENCE_PROFILE_ID = f"global.{MODEL_ID}"                      # what the code calls
TOKEN_HASH_PARAM = "/po-intelligence/api-token-sha256"           # CSR token hash, created by hand
ADMIN_TOKEN_HASH_PARAM = "/po-intelligence/admin-token-sha256"   # admin token hash, created by hand
VIEWER_TOKEN_HASH_PARAM = "/po-intelligence/viewer-token-sha256" # S1: read-only demo token hash
CONFIG_KEY = "CONFIG#current"                                    # the admin settings row in the table
DAILY_COUNTERS = {"ForAllValues:StringLike": {"dynamodb:LeadingKeys": ["LIMIT#*"]}}   # S2: counter rows only
DEV_ORIGIN = "http://localhost:5173"                             # the Vite dev server on your Mac


@jsii.implements(ILocalBundling)
class LocalPythonBundler:
    """Build the Lambda zip on this computer (no Docker needed):
    copy our po_pipeline package and pip-install its libraries next to it."""

    def try_bundle(self, output_dir: str, *, image=None, **kwargs) -> bool:
        out = Path(output_dir)
        shutil.copytree(BACKEND_DIR / "po_pipeline", out / "po_pipeline",
                        ignore=shutil.ignore_patterns("__pycache__"))
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "pypdf==6.17.0",
                        "--target", str(out)], check=True)
        return True


class InfraStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, *,
                 certificate: acm.ICertificate | None = None, domain: str | None = None,
                 zone_id: str | None = None, zone_name: str | None = None, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # 0) Website (I5): a private S3 bucket served only through CloudFront over HTTPS.
        site_bucket = s3.Bucket(
            self, "WebsiteBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,  # nobody reads it directly...
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )
        self.site = cloudfront.Distribution(
            self, "WebsiteDistribution",
            default_root_object="index.html",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(site_bucket),  # ...only CloudFront can
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                response_headers_policy=cloudfront.ResponseHeadersPolicy.SECURITY_HEADERS,
            ),
            price_class=cloudfront.PriceClass.PRICE_CLASS_100,   # US/Canada/Europe edges only (cheapest)
            domain_names=[domain] if domain else None,           # I6: po.saratsasikumar.com
            certificate=certificate,
        )
        cloudfront_origin = f"https://{self.site.distribution_domain_name}"
        site_origin = f"https://{domain}" if domain else cloudfront_origin
        web_origins = [DEV_ORIGIN, site_origin]                  # the only sites allowed to call us from a browser
        if domain:
            web_origins.append(cloudfront_origin)                # the cloudfront.net address keeps working
            zone = route53.HostedZone.from_hosted_zone_attributes(
                self, "Zone", hosted_zone_id=zone_id, zone_name=zone_name)
            alias = route53.RecordTarget.from_alias(targets.CloudFrontTarget(self.site))
            record_name = domain.removesuffix("." + zone_name)   # "po"
            route53.ARecord(self, "SiteAddressV4", zone=zone, record_name=record_name, target=alias)
            route53.AaaaRecord(self, "SiteAddressV6", zone=zone, record_name=record_name, target=alias)

        if FRONTEND_DIST.exists():                               # upload the built site, then clear CloudFront's cache
            s3deploy.BucketDeployment(
                self, "DeployWebsite",
                sources=[s3deploy.Source.asset(str(FRONTEND_DIST))],
                destination_bucket=site_bucket,
                distribution=self.site,
                distribution_paths=["/*"],
                log_group=logs.LogGroup(self, "DeployWebsiteLogs", retention=logs.RetentionDays.ONE_WEEK,
                                        removal_policy=RemovalPolicy.DESTROY),
            )

        # 1) S3 bucket: where uploaded PO files (PDFs, images) are stored.
        self.po_bucket = s3.Bucket(
            self,
            "PoDocumentsBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,  # never public
            encryption=s3.BucketEncryption.S3_MANAGED,           # encrypted at rest
            enforce_ssl=True,                                    # HTTPS only
            cors=[s3.CorsRule(                                   # F1: let our web page PUT files via presigned links
                allowed_methods=[s3.HttpMethods.POST],           # S2: size-limited POST uploads only
                allowed_origins=web_origins,
                allowed_headers=["content-type"],
                max_age=3000,
            )],
            lifecycle_rules=[
                s3.LifecycleRule(expiration=Duration.days(90)),  # auto-delete old files (cost)
            ],
            removal_policy=RemovalPolicy.DESTROY,                # demo: delete with the stack
            auto_delete_objects=True,                            # empty the bucket on delete
        )

        # 2) DynamoDB table: one item per PO (extracted fields, status, confidence).
        self.po_table = dynamodb.TableV2(
            self,
            "PoTable",
            partition_key=dynamodb.Attribute(
                name="po_id", type=dynamodb.AttributeType.STRING
            ),
            billing=dynamodb.Billing.on_demand(),                # pay per request, $0 when idle
            removal_policy=RemovalPolicy.DESTROY,                # demo: delete with the stack
        )

        # One package of our backend code, shared by all three Lambda functions.
        backend_code = lambda_.Code.from_asset(
            str(BACKEND_DIR),
            bundling=BundlingOptions(
                image=lambda_.Runtime.PYTHON_3_14.bundling_image,  # only used if local build fails
                local=LocalPythonBundler(),
            ),
        )

        # 3) Lambda: runs the PO pipeline whenever a file lands in incoming/.
        log_group = logs.LogGroup(
            self, "ProcessPoLogs",
            retention=logs.RetentionDays.ONE_WEEK,               # old logs deleted (cost)
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.process_fn = lambda_.Function(
            self,
            "ProcessPoFunction",
            runtime=lambda_.Runtime.PYTHON_3_14,
            architecture=lambda_.Architecture.ARM_64,           # ~20% cheaper than x86
            handler="po_pipeline.handler.lambda_handler",
            code=backend_code,
            memory_size=512,
            timeout=Duration.minutes(2),
            retry_attempts=0,                                    # no automatic (paid) retries
            log_group=log_group,
            environment={
                "TABLE_NAME": self.po_table.table_name,
                "PO_MODEL_ID": INFERENCE_PROFILE_ID,
            },
        )

        # 4) Least-privilege permissions for the Lambda's own role (its workload identity).
        self.po_bucket.grant_read(self.process_fn)               # read this bucket only
        self.process_fn.add_to_role_policy(iam.PolicyStatement(
            sid="SavePoResults",
            actions=["dynamodb:PutItem"],                        # the only call the pipeline makes
            resources=[self.po_table.table_arn],
        ))
        self.process_fn.add_to_role_policy(iam.PolicyStatement(
            sid="ReadAdminSettings",                             # B8: read the settings row, and only that row
            actions=["dynamodb:GetItem"],
            resources=[self.po_table.table_arn],
            conditions={"ForAllValues:StringEquals": {"dynamodb:LeadingKeys": [CONFIG_KEY]}},
        ))
        self.process_fn.add_to_role_policy(iam.PolicyStatement(
            sid="CountDailyProcessing",                          # S2: the daily cap counter, nothing else
            actions=["dynamodb:UpdateItem"],
            resources=[self.po_table.table_arn],
            conditions=DAILY_COUNTERS,
        ))
        self.process_fn.add_to_role_policy(iam.PolicyStatement(
            sid="TextractOcr",
            actions=["textract:DetectDocumentText"],
            resources=["*"],                                     # Textract has no resource-level permissions
        ))
        self.process_fn.add_to_role_policy(iam.PolicyStatement(
            sid="InvokeClaudeHaikuOnly",
            actions=["bedrock:InvokeModel"],                     # Converse uses this permission
            resources=[
                f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/{INFERENCE_PROFILE_ID}",
                f"arn:aws:bedrock:*::foundation-model/{MODEL_ID}",  # a global profile may run in any region
                f"arn:aws:bedrock:::foundation-model/{MODEL_ID}",
            ],
        ))

        # 5) Trigger: new objects under incoming/ start the Lambda.
        self.po_bucket.add_event_notification(
            s3.EventType.OBJECT_CREATED,
            s3n.LambdaDestination(self.process_fn),
            s3.NotificationKeyFilter(prefix="incoming/"),
        )

        # 6) Review API: API Gateway (HTTPS) -> token check -> API Lambda.
        token_param = ssm.StringParameter.from_string_parameter_name(self, "ApiTokenHash", TOKEN_HASH_PARAM)
        admin_token_param = ssm.StringParameter.from_string_parameter_name(
            self, "AdminTokenHash", ADMIN_TOKEN_HASH_PARAM)
        viewer_token_param = ssm.StringParameter.from_string_parameter_name(
            self, "ViewerTokenHash", VIEWER_TOKEN_HASH_PARAM)
        authorizer_fn = lambda_.Function(
            self, "ApiAuthorizerFunction",
            runtime=lambda_.Runtime.PYTHON_3_14,
            architecture=lambda_.Architecture.ARM_64,
            handler="po_pipeline.authorizer.lambda_handler",
            code=backend_code,
            memory_size=256,
            timeout=Duration.seconds(5),
            log_group=logs.LogGroup(self, "ApiAuthorizerLogs", retention=logs.RetentionDays.ONE_WEEK,
                                    removal_policy=RemovalPolicy.DESTROY),
            environment={"TOKEN_HASH_PARAM": TOKEN_HASH_PARAM,
                         "ADMIN_TOKEN_HASH_PARAM": ADMIN_TOKEN_HASH_PARAM,
                         "VIEWER_TOKEN_HASH_PARAM": VIEWER_TOKEN_HASH_PARAM},
        )
        token_param.grant_read(authorizer_fn)                    # read the three hashes, nothing else
        admin_token_param.grant_read(authorizer_fn)
        viewer_token_param.grant_read(authorizer_fn)

        self.api_fn = lambda_.Function(
            self, "ReviewApiFunction",
            runtime=lambda_.Runtime.PYTHON_3_14,
            architecture=lambda_.Architecture.ARM_64,
            handler="po_pipeline.api.lambda_handler",
            code=backend_code,
            memory_size=256,
            timeout=Duration.seconds(15),
            log_group=logs.LogGroup(self, "ReviewApiLogs", retention=logs.RetentionDays.ONE_WEEK,
                                    removal_policy=RemovalPolicy.DESTROY),
            environment={"TABLE_NAME": self.po_table.table_name,
                         "BUCKET_NAME": self.po_bucket.bucket_name},
        )
        self.api_fn.add_to_role_policy(iam.PolicyStatement(
            sid="ReadAndUpdatePos",
            actions=["dynamodb:GetItem", "dynamodb:Scan", "dynamodb:PutItem"],
            resources=[self.po_table.table_arn],
        ))
        self.api_fn.add_to_role_policy(iam.PolicyStatement(
            sid="CountDailyUploads",                             # S2: the daily cap counter, nothing else
            actions=["dynamodb:UpdateItem"],
            resources=[self.po_table.table_arn],
            conditions=DAILY_COUNTERS,
        ))
        self.api_fn.add_to_role_policy(iam.PolicyStatement(
            sid="ViewPoDocuments",                               # presigned GET links
            actions=["s3:GetObject"],
            resources=[self.po_bucket.arn_for_objects("*")],
        ))
        self.api_fn.add_to_role_policy(iam.PolicyStatement(
            sid="UploadIntoIncomingOnly",                        # presigned POST links (S2: size-limited)
            actions=["s3:PutObject"],
            resources=[self.po_bucket.arn_for_objects("incoming/*")],
        ))

        self.http_api = apigw.HttpApi(
            self, "ReviewHttpApi",
            create_default_stage=False,
            default_authorizer=authorizers.HttpLambdaAuthorizer(
                "TokenAuthorizer", authorizer_fn,
                response_types=[authorizers.HttpLambdaResponseType.SIMPLE],
                results_cache_ttl=Duration.minutes(5),           # fewer authorizer runs (cost)
            ),
            cors_preflight=apigw.CorsPreflightOptions(
                allow_origins=web_origins,                       # I5: our site + local dev only, no longer "*"
                allow_methods=[apigw.CorsHttpMethod.GET, apigw.CorsHttpMethod.PUT,
                               apigw.CorsHttpMethod.POST],
                allow_headers=["authorization", "content-type"],
            ),
        )
        apigw.HttpStage(
            self, "DefaultStage",
            http_api=self.http_api,
            stage_name="$default",
            auto_deploy=True,
            throttle=apigw.ThrottleSettings(rate_limit=5, burst_limit=10),  # cost guard
        )
        api_integration = integrations.HttpLambdaIntegration("ReviewApiIntegration", self.api_fn)
        for method, path in [
            (apigw.HttpMethod.GET, "/pos"),
            (apigw.HttpMethod.GET, "/pos/{id}"),
            (apigw.HttpMethod.PUT, "/pos/{id}"),
            (apigw.HttpMethod.POST, "/pos/{id}/approve"),
            (apigw.HttpMethod.POST, "/pos/{id}/reject"),
            (apigw.HttpMethod.POST, "/uploads"),
            (apigw.HttpMethod.GET, "/config"),                   # B8: admin-only (checked in the API code)
            (apigw.HttpMethod.PUT, "/config"),
            (apigw.HttpMethod.GET, "/config/history"),
            (apigw.HttpMethod.POST, "/config/rollback"),
            (apigw.HttpMethod.GET, "/me"),                       # S1: which role is this token?
        ]:
            self.http_api.add_routes(path=path, methods=[method], integration=api_integration)

        # 7) Outputs: printed after deploy so we know the real names.
        CfnOutput(self, "PoBucketName", value=self.po_bucket.bucket_name)
        CfnOutput(self, "PoTableName", value=self.po_table.table_name)
        CfnOutput(self, "ProcessFunctionName", value=self.process_fn.function_name)
        CfnOutput(self, "ApiUrl", value=self.http_api.api_endpoint)
        CfnOutput(self, "SiteUrl", value=site_origin)
