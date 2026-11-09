import aws_cdk as cdk
from aws_cdk.assertions import Match, Template

from infra.infra_stack import InfraStack


_TEMPLATE = None


def get_template() -> Template:
    global _TEMPLATE
    if _TEMPLATE is None:                      # build once: each build re-bundles the Lambda code
        app = cdk.App()
        _TEMPLATE = Template.from_stack(InfraStack(app, "TestStack"))
    return _TEMPLATE


def all_statements():
    return [s for policy in get_template().find_resources("AWS::IAM::Policy").values()
            for s in policy["Properties"]["PolicyDocument"]["Statement"]]


def test_bucket_blocks_all_public_access():
    get_template().has_resource_properties(
        "AWS::S3::Bucket",
        {
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "BlockPublicPolicy": True,
                "IgnorePublicAcls": True,
                "RestrictPublicBuckets": True,
            }
        },
    )


def test_bucket_rejects_non_https_requests():
    get_template().has_resource_properties(
        "AWS::S3::BucketPolicy",
        {
            "PolicyDocument": Match.object_like(
                {
                    "Statement": Match.array_with(
                        [
                            Match.object_like(
                                {
                                    "Effect": "Deny",
                                    "Condition": {"Bool": {"aws:SecureTransport": "false"}},
                                }
                            )
                        ]
                    )
                }
            )
        },
    )


def test_table_is_on_demand_with_po_id_key():
    get_template().has_resource_properties(
        "AWS::DynamoDB::GlobalTable",
        {
            "BillingMode": "PAY_PER_REQUEST",
            "KeySchema": [{"AttributeName": "po_id", "KeyType": "HASH"}],
        },
    )


def test_lambda_has_cost_guards():
    get_template().has_resource_properties(
        "AWS::Lambda::Function",
        {"Runtime": "python3.14", "Timeout": 120},
    )


def test_lambda_may_only_call_claude_haiku():
    statements = [
        s
        for policy in get_template().find_resources("AWS::IAM::Policy").values()
        for s in policy["Properties"]["PolicyDocument"]["Statement"]
    ]
    bedrock = [s for s in statements if s.get("Sid") == "InvokeClaudeHaikuOnly"][0]
    assert bedrock["Action"] == "bedrock:InvokeModel"
    assert all("claude-haiku-4-5" in str(r) for r in bedrock["Resource"])


def test_upload_folder_triggers_lambda():
    get_template().has_resource_properties(
        "Custom::S3BucketNotifications",
        {"NotificationConfiguration": {"LambdaFunctionConfigurations": [
            Match.object_like({"Events": ["s3:ObjectCreated:*"]})]}},
    )



def test_every_api_route_requires_the_token():
    routes = get_template().find_resources("AWS::ApiGatewayV2::Route")
    assert len(routes) == 11
    assert all(r["Properties"]["AuthorizationType"] == "CUSTOM" for r in routes.values())


def test_api_is_throttled():
    get_template().has_resource_properties(
        "AWS::ApiGatewayV2::Stage",
        {"DefaultRouteSettings": {"ThrottlingRateLimit": 5, "ThrottlingBurstLimit": 10}},
    )


def test_pipeline_lambda_can_only_put_items():
    statements = [
        s
        for policy in get_template().find_resources("AWS::IAM::Policy").values()
        for s in policy["Properties"]["PolicyDocument"]["Statement"]
    ]
    save = [s for s in statements if s.get("Sid") == "SavePoResults"][0]
    assert save["Action"] == "dynamodb:PutItem"


def test_pipeline_can_read_only_the_settings_row():
    read = [s for s in all_statements() if s.get("Sid") == "ReadAdminSettings"][0]
    assert read["Action"] == "dynamodb:GetItem"
    assert read["Condition"] == {"ForAllValues:StringEquals": {"dynamodb:LeadingKeys": ["CONFIG#current"]}}


def test_authorizer_knows_both_token_hashes():
    get_template().has_resource_properties(
        "AWS::Lambda::Function",
        {"Handler": "po_pipeline.authorizer.lambda_handler",
         "Environment": {"Variables": {"TOKEN_HASH_PARAM": "/po-intelligence/api-token-sha256",
                                       "ADMIN_TOKEN_HASH_PARAM": "/po-intelligence/admin-token-sha256",
                                       "VIEWER_TOKEN_HASH_PARAM": "/po-intelligence/viewer-token-sha256"}}},
    )


def test_browser_may_only_upload_from_our_site():
    get_template().has_resource_properties(
        "AWS::S3::Bucket",
        {"CorsConfiguration": {"CorsRules": [Match.object_like({
            "AllowedMethods": ["POST"],
            "AllowedOrigins": ["http://localhost:5173", Match.any_value()]})]}},   # dev + CloudFront
    )


def test_api_no_longer_accepts_every_website():
    api = list(get_template().find_resources("AWS::ApiGatewayV2::Api").values())[0]
    origins = api["Properties"]["CorsConfiguration"]["AllowOrigins"]
    assert "*" not in origins and "http://localhost:5173" in origins and len(origins) == 2


def test_website_is_https_only_through_cloudfront():
    get_template().has_resource_properties(
        "AWS::CloudFront::Distribution",
        {"DistributionConfig": Match.object_like({
            "DefaultRootObject": "index.html",
            "DefaultCacheBehavior": Match.object_like({"ViewerProtocolPolicy": "redirect-to-https"}),
            "PriceClass": "PriceClass_100",
        })},
    )
    get_template().resource_count_is("AWS::CloudFront::OriginAccessControl", 1)   # bucket stays private


def test_daily_counters_are_the_only_rows_lambdas_can_update():
    counters = [s for s in all_statements() if s.get("Sid") in ("CountDailyProcessing", "CountDailyUploads")]
    assert len(counters) == 2
    for statement in counters:
        assert statement["Action"] == "dynamodb:UpdateItem"
        assert statement["Condition"] == {"ForAllValues:StringLike": {"dynamodb:LeadingKeys": ["LIMIT#*"]}}


def test_custom_domain_gets_certificate_dns_records_and_cors():
    from infra.cert_stack import CertStack
    app = cdk.App()
    cert = CertStack(app, "Cert", env=cdk.Environment(account="111111111111", region="us-east-1"),
                     cross_region_references=True, domain="po.example.com", zone_id="Z1", zone_name="example.com")
    stack = InfraStack(app, "Main", env=cdk.Environment(account="111111111111", region="us-east-2"),
                       cross_region_references=True, certificate=cert.certificate,
                       domain="po.example.com", zone_id="Z1", zone_name="example.com")
    Template.from_stack(cert).has_resource_properties(
        "AWS::CertificateManager::Certificate", {"DomainName": "po.example.com", "ValidationMethod": "DNS"})
    main = Template.from_stack(stack)
    main.has_resource_properties("AWS::CloudFront::Distribution",
                                 {"DistributionConfig": Match.object_like({"Aliases": ["po.example.com"]})})
    main.has_resource_properties("AWS::Route53::RecordSet", {"Name": "po.example.com.", "Type": "A"})
    main.has_resource_properties("AWS::Route53::RecordSet", {"Name": "po.example.com.", "Type": "AAAA"})
    api = list(main.find_resources("AWS::ApiGatewayV2::Api").values())[0]
    assert "https://po.example.com" in api["Properties"]["CorsConfiguration"]["AllowOrigins"]


def test_github_can_deploy_only_from_main_and_only_through_cdk_roles():
    from infra.cicd_stack import CicdStack
    stack = CicdStack(cdk.App(), "Cicd", env=cdk.Environment(account="111111111111", region="us-east-2"))
    template = Template.from_stack(stack)
    role = list(template.find_resources("AWS::IAM::Role").values())
    deploy = [r for r in role if r["Properties"].get("RoleName") == "po-intelligence-github-deploy"][0]
    condition = deploy["Properties"]["AssumeRolePolicyDocument"]["Statement"][0]["Condition"]["StringEquals"]
    assert condition["token.actions.githubusercontent.com:sub"] == (
        "repo:saratcsk@58955078/po-intelligence@1450825794:ref:refs/heads/main")      # this repo, main only
    statements = [s for p in template.find_resources("AWS::IAM::Policy").values()
                  for s in p["Properties"]["PolicyDocument"]["Statement"]]
    assert [s["Sid"] for s in statements] == ["UseCdkDeployRolesOnly"]          # nothing else allowed
    assert all("cdk-hnb659fds-" in r for r in statements[0]["Resource"])

