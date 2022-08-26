#!/usr/bin/env python

"""
    integrity_enforcer.py:
    Lambda Function that executes according to a periodic
    CloudWatch Events schedule and enforces the desired
    configuration for ALB, Route53 and WAF resources.
"""

import datetime
import json
import logging
import os
import traceback

import boto3
import botocore.exceptions

from ..services.constants_service import ConstantsService
from ..services.fleet_service import FleetService
from ..services.monitor_service import MonitorService
from ..services.notifier_service import NotifierService
from ..services.statemachine_service import StateMachineService

# set logging
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

# get env vars
REMOVE_ALB_STATE_MACHINE_ARN = os.environ['REMOVE_ALB_STATE_MACHINE_ARN']
REMOVE_ALB_STATE_MACHINE_NAME = os.environ['REMOVE_ALB_STATE_MACHINE_NAME']
ADD_ALB_STATE_MACHINE_ARN = os.environ['ADD_ALB_STATE_MACHINE_ARN']
ADD_ALB_STATE_MACHINE_NAME = os.environ['ADD_ALB_STATE_MACHINE_NAME']
WAF_WEB_ACL_ARN = os.environ['WAF_WEB_ACL_ARN']
HOSTED_ZONE_ID = os.environ['ROUTE_53_PRIVATE_ZONE_ID']
ROUTE_53_ALB_DNS_NAME = os.environ['ROUTE_53_ALB_DNS_NAME']

# static vars
OPERATOR = "INTEGRITY_ENFORCER"

# boto3 clients
stepfunctions_client = boto3.client('stepfunctions')
wafv2_client = boto3.client('wafv2')
route53_client = boto3.client('route53')

# services
notifier_service = NotifierService()
fleet_service = FleetService()
constants_service = ConstantsService()
statemachine_service = StateMachineService()
monitor_service = MonitorService()


def enforce_waf_association(albs: list[str]) -> bool:

    logger.debug("enforce_waf_association")
    logger.debug(albs)

    did_enforce = False

    disassociated_waf_albs = monitor_service.get_albs_disassociated_from_waf(albs=albs)

    if len(disassociated_waf_albs) > 0:
        logger.debug(f"The following ALBs {','.join(disassociated_waf_albs)} are not associated to WAF {WAF_WEB_ACL_ARN}.")
        for disassociated_waf_alb in disassociated_waf_albs:
            did_enforce = True
            logger.info(f"Associating ALB {disassociated_waf_alb} to WAF {WAF_WEB_ACL_ARN}")
            wafv2_client.associate_web_acl(
                WebACLArn=WAF_WEB_ACL_ARN,
                ResourceArn=disassociated_waf_alb
            )

    return did_enforce


def enforce_resource_set_weights() -> bool:

    did_enforce = False

    resource_records = route53_client.list_resource_record_sets(
        HostedZoneId=HOSTED_ZONE_ID,
        StartRecordName=ROUTE_53_ALB_DNS_NAME,
        StartRecordType='A'
    )

    records_to_adjust = []

    # if we have multiple records in the weighted resource set
    # ensure that all records have a weight of 0 for distributed
    # load balancing
    if len(resource_records['ResourceRecordSets']) > 1:
        for record in resource_records['ResourceRecordSets']:
         if record['Weight'] != 0:
            records_to_adjust.append(record)

    if len(records_to_adjust) > 0:
        for record_to_adjust in records_to_adjust:
            change_info = route53_client.change_resource_record_sets(
                HostedZoneId=HOSTED_ZONE_ID,
                ChangeBatch={
                    'Comment': 'Adjusted ALB',
                    'Changes': [
                        {
                            'Action': 'UPSERT',
                            'ResourceRecordSet': {
                                'Name': ROUTE_53_ALB_DNS_NAME,
                                'Type': 'A',
                                'SetIdentifier': record_to_adjust['SetIdentifier'],
                                'Weight': 0,
                                'AliasTarget': {
                                    'HostedZoneId': record_to_adjust['AliasTarget']['HostedZoneId'],
                                    'DNSName': record_to_adjust['AliasTarget']['DNSName'],
                                    'EvaluateTargetHealth': True
                                }
                            }
                        }
                    ]
                }
            )

        waiter = route53_client.get_waiter('resource_record_sets_changed')
        waiter.wait(
        Id=change_info['ChangeInfo']['Id'],
        WaiterConfig={
                'Delay': 15,
                'MaxAttempts': 30
            }
        )

        did_enforce = True
    elif len(resource_records['ResourceRecordSets']) == 1:
        # we only have one load balancer in the fleet, so we need to set the weight to a value
        # greater than 0
        record_to_adjust = resource_records['ResourceRecordSets'][0]
        change_info = route53_client.change_resource_record_sets(
            HostedZoneId=HOSTED_ZONE_ID,
            ChangeBatch={
                'Comment': 'Adjusted ALB',
                'Changes': [
                    {
                        'Action': 'UPSERT',
                        'ResourceRecordSet': {
                            'Name': ROUTE_53_ALB_DNS_NAME,
                            'Type': 'A',
                            'SetIdentifier': record_to_adjust['SetIdentifier'],
                            'Weight': 255,
                            'AliasTarget': {
                                'HostedZoneId': record_to_adjust['AliasTarget']['HostedZoneId'],
                                'DNSName': record_to_adjust['AliasTarget']['DNSName'],
                                'EvaluateTargetHealth': True
                            }
                        }
                    }
                ]
            }
        )

        waiter = route53_client.get_waiter('resource_record_sets_changed')
        waiter.wait(
            Id=change_info['ChangeInfo']['Id'],
            WaiterConfig={
                'Delay': 15,
                'MaxAttempts': 30
            }
        )

        did_enforce = True

    return did_enforce


def get_resource_record_weight() -> bool:
    resource_records = route53_client.list_resource_record_sets(
        HostedZoneId=HOSTED_ZONE_ID,
        StartRecordName=ROUTE_53_ALB_DNS_NAME,
        StartRecordType='A'
    )

    if len(resource_records['ResourceRecordSets']) > 0:
        return 0
    else:
        return 255


def enforce_albs_in_record_set(albs: list[str]) -> bool:

    did_enforce = False

    missing_records = monitor_service.get_missing_resource_set_records(albs)

    logger.debug("enforce_albs_in_record_set")
    logger.debug(missing_records)

    if len(missing_records) > 0:
        for missing_record in missing_records:
            logger.debug("missng record")
            logger.debug(missing_record)
            change_info = route53_client.change_resource_record_sets(
                HostedZoneId=HOSTED_ZONE_ID,
                ChangeBatch={
                    'Comment': 'Adjusted ALB',
                    'Changes': [
                        {
                            'Action': 'CREATE',
                            'ResourceRecordSet': {
                                'Name': ROUTE_53_ALB_DNS_NAME,
                                'Type': 'A',
                                'SetIdentifier': missing_record['LoadBalancerName'],
                                'Weight': get_resource_record_weight(),
                                'AliasTarget': {
                                    'HostedZoneId': missing_record['CanonicalHostedZoneId'],
                                    'DNSName': missing_record['DNSName'],
                                    'EvaluateTargetHealth': True
                                }
                            }
                        }
                    ]
                }
            )

            waiter = route53_client.get_waiter('resource_record_sets_changed')
            waiter.wait(
            Id=change_info['ChangeInfo']['Id'],
            WaiterConfig={
                    'Delay': 10,
                    'MaxAttempts': 30
                }
            )

            did_enforce = True

    return did_enforce


def lambda_handler(event, context):
    # read the event to json
    logger.debug(json.dumps(event, indent=2))

    try:

        logger.info("Executing fleet integrity enforcer.")

        albs = fleet_service.get_load_balancers(constants_service.FILTER_BY_GROUP)

        logger.debug("Enforce integrity on the following albs")
        logger.debug(albs)

        # enforce WAF association
        waf_association_enforced = enforce_waf_association(albs)

        # enforce alb in record set
        route53_association_enforced = enforce_albs_in_record_set(albs)

        # enforce Route53 record set weights
        route53_weights_enforced = enforce_resource_set_weights()

        response_message = { "fleet_integrity_monitor_status": "ok"}

        return {
            'statusCode': 200,
            'body': json.dumps(response_message, indent=2),
            'headers': {'Content-Type': 'application/json'}
        }

    except botocore.exceptions.ClientError as e:

        logger.error(f'Error in executing {OPERATOR} operation: {str(e)}')
        
        subject = f"Unexpected error in ALB Integrity Enforcer process."
        error_timestamp = datetime.datetime.now().strftime("%m/%d/%Y, %H:%M:%S")
        
        template_attributes = {}
        template_attributes['error_message'] = str(e)
        template_attributes['error_timestamp'] = error_timestamp
        template_attributes['alb_instances'] = "NONE"

        notifier_service.send_monitor_failure(
            subject=subject, 
            template_attributes=template_attributes,
            is_specific_failure=True
        )

        return {
            'statusCode': 500,
            'body': event,
            'headers': {'Content-Type': 'application/json'}
        }

    except Exception as e:
        
        traceback.print_exception(type(e), value=e, tb=e.__traceback__)

        logger.error(f'Error in executing {OPERATOR} operation: {str(e)}')
        
        subject = f"Unexpected error in ALB Integrity Enforcer process."
        error_timestamp = datetime.datetime.now().strftime("%m/%d/%Y, %H:%M:%S")
        
        template_attributes = {}
        template_attributes['error_message'] = str(e)
        template_attributes['error_timestamp'] = error_timestamp
        template_attributes['alb_instances'] = "NONE"

        notifier_service.send_monitor_failure(
            subject=subject, 
            template_attributes=template_attributes,
            is_specific_failure=False
        )
        return {
            'statusCode': 500,
            'body': event,
            'headers': {'Content-Type': 'application/json'}
        }
