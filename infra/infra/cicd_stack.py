"""Step C2: lets GitHub Actions deploy to AWS without any stored AWS keys (OIDC).

How it works: when the deploy job runs, GitHub signs a short-lived identity token saying
"I am repo saratcsk/po-intelligence, branch main". AWS checks that signature and hands back
temporary credentials (1 hour) for this one role. No password or access key exists to leak.

This stack is deployed by hand from your Mac, never by the pipeline itself, so the pipeline
cannot change its own permissions.
"""

from aws_cdk import CfnOutput, Duration, Stack, aws_iam as iam
from constructs import Construct

GITHUB_REPO = "saratcsk/po-intelligence"
# GitHub now puts permanent account and repo IDs into the token ("name@id"). The IDs never change,
# so even if this repo were deleted and someone re-created one with the same name, it would not match.
GITHUB_SUBJECT = "repo:saratcsk@58955078/po-intelligence@1450825794:ref:refs/heads/main"
CDK_QUALIFIER = "hnb659fds"                          # the default name used by `cdk bootstrap`


class CicdStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        github = iam.OpenIdConnectProvider(
            self, "GitHubOidc",
            url="https://token.actions.githubusercontent.com",
            client_ids=["sts.amazonaws.com"],
        )
        role = iam.Role(
            self, "GitHubDeployRole",
            role_name="po-intelligence-github-deploy",
            assumed_by=iam.WebIdentityPrincipal(github.open_id_connect_provider_arn, conditions={
                "StringEquals": {
                    "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                    # only this repo's main branch, nothing else on GitHub
                    "token.actions.githubusercontent.com:sub": GITHUB_SUBJECT,
                },
            }),
            max_session_duration=Duration.hours(1),
            description="GitHub Actions deploy role for PO Intelligence (main branch only)",
        )
        # The role itself can do almost nothing: it may only step into the roles that
        # `cdk bootstrap` created in our two regions, which is what `cdk deploy` needs.
        role.add_to_policy(iam.PolicyStatement(
            sid="UseCdkDeployRolesOnly",
            actions=["sts:AssumeRole", "sts:TagSession"],
            resources=[f"arn:aws:iam::{self.account}:role/cdk-{CDK_QUALIFIER}-*-{self.account}-{region}"
                       for region in ("us-east-1", "us-east-2")],
        ))
        CfnOutput(self, "DeployRoleArn", value=role.role_arn)
