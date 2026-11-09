#!/usr/bin/env python3
import os

import aws_cdk as cdk

from infra.cert_stack import CertStack
from infra.cicd_stack import CicdStack
from infra.infra_stack import InfraStack

ACCOUNT = os.environ["CDK_DEFAULT_ACCOUNT"]       # the AWS account you are logged in to (set by the cdk CLI)
DOMAIN = "po.saratsasikumar.com"                  # I6: the app's public address
ZONE_ID = "Z021942910ML6XOA6XKW8"                 # Route 53 hosted zone for saratsasikumar.com
ZONE_NAME = "saratsasikumar.com"

app = cdk.App()

cert = CertStack(                                  # must be us-east-1: CloudFront only reads certificates there
    app, "PoIntelligenceCertStack",
    env=cdk.Environment(account=ACCOUNT, region="us-east-1"),
    cross_region_references=True,
    domain=DOMAIN, zone_id=ZONE_ID, zone_name=ZONE_NAME,
    description="PO Intelligence - HTTPS certificate for the custom domain",
)

InfraStack(
    app,
    "PoIntelligenceStack",
    env=cdk.Environment(account=ACCOUNT, region="us-east-2"),
    cross_region_references=True,
    certificate=cert.certificate, domain=DOMAIN, zone_id=ZONE_ID, zone_name=ZONE_NAME,
    description="PO Intelligence MVP - storage for purchase orders and results",
)

CicdStack(                                         # C2: deployed by hand only, never by the pipeline
    app, "PoIntelligenceCicdStack",
    env=cdk.Environment(account=ACCOUNT, region="us-east-2"),
    description="PO Intelligence - GitHub Actions deploy role (OIDC, no stored keys)",
)

cdk.Tags.of(app).add("project", "po-intelligence")

app.synth()
