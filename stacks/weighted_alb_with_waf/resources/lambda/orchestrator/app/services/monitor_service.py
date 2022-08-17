import boto3
import os
import logging
from ..services.constants_service import ConstantsService

# set logging
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

# boto3 clients
wafv2_client = boto3.client('wafv2')
route53_client = boto3.client('route53')

# get env vars
WAF_WEB_ACL_ARN = os.environ['WAF_WEB_ACL_ARN']
PRIVATE_ZONE_ID = os.environ['ROUTE_53_PRIVATE_ZONE_ID']
ROUTE_53_ALB_DNS_NAME = os.environ['ROUTE_53_ALB_DNS_NAME']

constants_service = ConstantsService()

class MonitorService:


    def get_missing_resource_set_records(
            self, 
            albs: list[dict]
        ) -> list[dict]:

        response = route53_client.list_resource_record_sets(
            HostedZoneId=PRIVATE_ZONE_ID,
            StartRecordName=ROUTE_53_ALB_DNS_NAME,
            StartRecordType='A'
        )

        resource_set_records = []

        for record in response['ResourceRecordSets']:
            resource_set_records.append(record['AliasTarget']['DNSName'])

        missing_records = []

        for alb in albs:
            if alb['State']['Code'] == "active":
                compare_dns_name = f"{alb['DNSName'].lower()}.".lower()
                if any(compare_dns_name in record_set for record_set in resource_set_records) == False:
                    missing_records.append(alb)
                    logger.error(f"ALB with DNS Name {alb['DNSName']}, ARN {alb['LoadBalancerArn']} has not been associated to the Route 53 weighted resource set: {ROUTE_53_ALB_DNS_NAME}. Current Route 53 records {','.join(resource_set_records)}, current ALBS {','.join(alb['LoadBalancerArn'] for alb in albs)}.")
                else:
                    logger.info(f"ALB with DNS Name {alb['DNSName']}, ARN {alb['LoadBalancerArn']} has been associated to the Route 53 weighted resource set: {ROUTE_53_ALB_DNS_NAME}. Current Route 53 records {','.join(resource_set_records)}, current ALBS {','.join(alb['LoadBalancerArn'] for alb in albs)}.")

        return missing_records


    def get_albs_disassociated_from_waf(
            self, 
            albs: list[str]
        ) -> list[str]:
        waf_albs = wafv2_client.list_resources_for_web_acl(
            WebACLArn=WAF_WEB_ACL_ARN,
            ResourceType='APPLICATION_LOAD_BALANCER'
        )['ResourceArns']

        disassociated_waf_albs = set(())
        
        for alb in albs:
            if alb['State']['Code'] == "active":
                if alb['LoadBalancerArn'] not in waf_albs:
                    disassociated_waf_albs.add(alb['LoadBalancerArn'])
                    logger.error(f"ALB {alb['LoadBalancerArn']} is not associated to WAF {WAF_WEB_ACL_ARN}. All ALBs must be associated to WAF. Current ALBs associated to WAF: {','.join(waf_albs)}")
                else:
                    logger.info(f"ALB {alb['LoadBalancerArn']} is associated to WAF {WAF_WEB_ACL_ARN}. All ALBs must be associated to WAF. Current ALBs associated to WAF: {','.join(waf_albs)}")

        # remove duplicates while maintaining list type
        disassociated_waf_albs = list(disassociated_waf_albs)

        return disassociated_waf_albs
