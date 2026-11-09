"""Step I6: the HTTPS certificate for the custom domain.

CloudFront only accepts certificates from us-east-1, so this tiny stack lives there,
while everything else stays in Ohio (us-east-2). The certificate is free (AWS Certificate
Manager), renews itself, and is proven by a DNS record added automatically in Route 53.
"""

from aws_cdk import Stack, aws_certificatemanager as acm, aws_route53 as route53
from constructs import Construct


class CertStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, domain: str, zone_id: str, zone_name: str,
                 **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        zone = route53.HostedZone.from_hosted_zone_attributes(
            self, "Zone", hosted_zone_id=zone_id, zone_name=zone_name)
        self.certificate = acm.Certificate(
            self, "SiteCertificate",
            domain_name=domain,
            validation=acm.CertificateValidation.from_dns(zone),   # proves we own the domain
        )
